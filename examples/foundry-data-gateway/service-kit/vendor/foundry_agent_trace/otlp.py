"""OpenTelemetry SDK adapter with bounded, replaceable OTLP export.

The package owns instrumentation policy, not a telemetry backend. Hosts pass
resource identity and an OTLP endpoint; the official SDK owns trace context
and transport, while this adapter owns the bounded, observable export queue.
Export failures are counted and never cross back into the caller whose work is
being observed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
import weakref
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.context import (
    _SUPPRESS_INSTRUMENTATION_KEY,
    attach,
    detach,
    set_value,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
    SpanProcessor,
)
from opentelemetry.trace import Link, SpanKind, Status, StatusCode

from foundry_agent_trace._redact import DenyListRedactor, Redactor
from foundry_agent_trace._span import Span, _NoopSpan

_DEFAULT_ALLOWED_ATTRIBUTE_KEYS = frozenset(
    {
        "action",
        "agent",
        "contract",
        "cost_micros",
        "model",
        "outcome",
        "step",
        "steps_taken",
        "tokens",
        "tool_name",
        "error.type",
        "eval.case.id",
        "eval.fence.token",
        "eval.lane.severity",
        "eval.predicate.key",
        "eval.result.state",
        "eval.run.id",
        "eval.suite.key",
        "eval.suite.version",
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "output_contract.attempt_count",
        "output_contract.enforcement_mode",
        "output_contract.error_code",
        "output_contract.id",
        "output_contract.schema_digest",
        "output_contract.validation",
        "output_contract.version",
        "policy.action",
        "policy.result",
        "policy.rule",
        "foundry.receipt.id",
        "tool.name",
    }
)
_KIND = {
    "internal": SpanKind.INTERNAL,
    "server": SpanKind.SERVER,
    "client": SpanKind.CLIENT,
    "producer": SpanKind.PRODUCER,
    "consumer": SpanKind.CONSUMER,
}
_DROP = object()


@dataclass(frozen=True)
class OtelResource:
    """Required low-cardinality identity attached to every exported span."""

    service_name: str
    service_instance_id: str
    service_version: str
    deployment_environment: str
    foundry_box: str
    image_digest: str

    def attributes(self) -> dict[str, str]:
        values = {
            "service.name": self.service_name,
            "service.instance.id": self.service_instance_id,
            "service.version": self.service_version,
            "deployment.environment.name": self.deployment_environment,
            "foundry.box": self.foundry_box,
            "oci.manifest.digest": self.image_digest,
        }
        empty = [key for key, value in values.items() if not value]
        if empty:
            raise ValueError(f"OtelResource values must be non-empty: {empty}")
        return values


class _CountingExporter(SpanExporter):
    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self._failed_spans = 0

    @property
    def failed_spans(self) -> int:
        with self._lock:
            return self._failed_spans

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception:
            result = SpanExportResult.FAILURE
        if result is not SpanExportResult.SUCCESS:
            with self._lock:
                self._failed_spans += len(spans)
        return result

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            result = self._inner.force_flush(timeout_millis)
            return True if result is None else bool(result)
        except Exception:
            return False

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception:
            return None

    def after_fork(self) -> None:
        self._lock = threading.Lock()


class _BoundedBatchSpanProcessor(SpanProcessor):
    """Small bounded processor with observable queue loss.

    The upstream Python ``BatchSpanProcessor`` bounds and drops its queue but
    does not expose that count through its public API. the platform's tracer port
    requires an exact drop signal, so this processor preserves the same
    drop-oldest/batch/background-export shape while owning the counter.
    """

    def __init__(
        self,
        exporter: SpanExporter,
        *,
        max_queue_size: int,
        max_export_batch_size: int,
        schedule_delay_millis: int,
    ) -> None:
        self._exporter = exporter
        self._max_queue_size = max_queue_size
        self._max_export_batch_size = max_export_batch_size
        self._delay_seconds = schedule_delay_millis / 1_000
        self._queue: deque[ReadableSpan] = deque()
        self._condition = threading.Condition()
        self._drop_lock = threading.Lock()
        self._queue_drops = 0
        self._inflight = 0
        self._flush_requested = False
        self._shutdown = False
        self._pid = os.getpid()
        self._worker = self._new_worker()
        self._worker.start()

    def _new_worker(self) -> threading.Thread:
        return threading.Thread(
            target=self._run,
            name="foundry-otel-batch-export",
            daemon=True,
        )

    @property
    def queue_drops(self) -> int:
        with self._drop_lock:
            return self._queue_drops

    def after_fork(self) -> None:
        inherited = len(self._queue)
        self._condition = threading.Condition()
        self._drop_lock = threading.Lock()
        self._queue = deque()
        self._queue_drops += inherited
        self._inflight = 0
        self._flush_requested = False
        self._shutdown = False
        self._pid = os.getpid()
        self._worker = self._new_worker()
        self._worker.start()

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        del span, parent_context

    def on_end(self, span: ReadableSpan) -> None:
        if not (span.context and span.context.trace_flags.sampled):
            return
        if self._pid != os.getpid():
            self.after_fork()
        dropped = 0
        with self._condition:
            if self._shutdown:
                return
            if len(self._queue) >= self._max_queue_size:
                self._queue.popleft()
                dropped = 1
            self._queue.append(span)
            # Wake on every enqueue. The worker starts by waiting for a first
            # item; notifying only at max batch size leaves low-volume services
            # asleep forever and the schedule delay never begins.
            self._condition.notify()
        if dropped:
            with self._drop_lock:
                self._queue_drops += dropped

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        deadline = time.monotonic() + timeout_millis / 1_000
        with self._condition:
            self._flush_requested = True
            self._condition.notify()
            while self._queue or self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        remaining_millis = max(0, int((deadline - time.monotonic()) * 1_000))
        return self._exporter.force_flush(remaining_millis)

    def shutdown(self) -> None:
        with self._condition:
            if self._shutdown:
                return
            self._shutdown = True
            self._flush_requested = True
            self._condition.notify()
        self._worker.join(timeout=30)
        self._exporter.shutdown()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._shutdown:
                    self._condition.wait()
                if self._shutdown and not self._queue:
                    return
                if (
                    not self._flush_requested
                    and not self._shutdown
                    and len(self._queue) < self._max_export_batch_size
                ):
                    self._condition.wait(self._delay_seconds)
                size = min(len(self._queue), self._max_export_batch_size)
                batch = tuple(self._queue.popleft() for _ in range(size))
                self._inflight += len(batch)
            try:
                token = attach(set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
                try:
                    self._exporter.export(batch)
                finally:
                    detach(token)
            finally:
                with self._condition:
                    self._inflight -= len(batch)
                    if not self._queue:
                        self._flush_requested = False
                    self._condition.notify_all()


class _OtelSpan:
    def __init__(self, otel_span: Any, scope: Any, owner: OtlpTracer) -> None:
        self._span = otel_span
        self._scope = scope
        self._owner = owner
        self._ended = False

    def set_attribute(self, key: str, value: object) -> None:
        if self._ended:
            return
        try:
            mapped = self._owner._map_attribute(key, value)
            if mapped is _DROP:
                return
            output_key, output_value = mapped
            self._span.set_attribute(output_key, output_value)
        except Exception:
            self._owner._note_attribute_failure()

    def record_error(self, error: BaseException) -> None:
        if self._ended:
            return
        try:
            error_type = type(error).__name__
            if type(error).__module__ != "builtins" or error_type not in {
                "ArithmeticError",
                "AssertionError",
                "ConnectionError",
                "EOFError",
                "ImportError",
                "LookupError",
                "OSError",
                "RuntimeError",
                "TimeoutError",
                "TypeError",
                "ValueError",
            }:
                error_type = "Exception"
            # OpenTelemetry's record_exception() serialises the message and
            # stacktrace into an event. Either can contain a tenant prompt,
            # provider response, connector payload, or secret. Error spans keep
            # a fixed, bounded type and status while those bytes stay in-process.
            self.set_attribute("error.type", error_type)
            self._span.set_status(Status(StatusCode.ERROR, error_type))
        except Exception:
            self._owner._note_attribute_failure()

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        try:
            self._scope.__exit__(None, None, None)
        except Exception:
            self._owner._note_local_drop()
        try:
            self._span.end()
        except Exception:
            self._owner._note_local_drop()

    def __enter__(self) -> _OtelSpan:
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> bool:
        if exc is not None:
            self.record_error(exc)
        self.end()
        return False


class OtlpTracer:
    """Official OTel SDK adapter satisfying the platform's small ``Tracer`` port.

    ``endpoint`` is the complete OTLP/HTTP traces URL, normally
    ``http://otel-collector:4318/v1/traces``. ``exporter`` is an injected seam
    for deterministic tests and alternative OTLP transports; exactly one of
    the two must be supplied.
    """

    def __init__(
        self,
        *,
        resource: OtelResource,
        endpoint: str | None = None,
        exporter: SpanExporter | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 2.0,
        batch: bool = True,
        max_queue_size: int = 2_000,
        max_export_batch_size: int = 200,
        schedule_delay_millis: int = 1_000,
        org_id_hmac_key: bytes | None = None,
    ) -> None:
        if (endpoint is None) == (exporter is None):
            raise ValueError("provide exactly one of endpoint or exporter")
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        if not 1 <= max_export_batch_size <= max_queue_size:
            raise ValueError("max_export_batch_size must be between 1 and max_queue_size")
        if schedule_delay_millis < 0:
            raise ValueError("schedule_delay_millis must be non-negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        real_exporter = exporter or OTLPSpanExporter(
            endpoint=endpoint,
            headers=dict(headers or {}),
            timeout=timeout_seconds,
        )
        self._exporter = _CountingExporter(real_exporter)
        if org_id_hmac_key is not None and len(org_id_hmac_key) < 32:
            raise ValueError("org_id_hmac_key must contain at least 32 bytes")
        self._redactor: Redactor = DenyListRedactor()
        self._org_id_hmac_key = org_id_hmac_key
        self._lock = threading.Lock()
        self._local_drops = 0
        self._attribute_failures = 0
        self._shutdown = False
        self._pid = os.getpid()

        self._provider = TracerProvider(resource=Resource.create(resource.attributes()))
        if batch:
            processor = _BoundedBatchSpanProcessor(
                self._exporter,
                max_queue_size=max_queue_size,
                max_export_batch_size=max_export_batch_size,
                schedule_delay_millis=schedule_delay_millis,
            )
            self._batch_processor: _BoundedBatchSpanProcessor | None = processor
        else:
            processor = SimpleSpanProcessor(self._exporter)
            self._batch_processor = None
        self._provider.add_span_processor(processor)
        self._tracer = self._provider.get_tracer("foundry-agent-trace", "0.1.0")
        if hasattr(os, "register_at_fork"):
            weak_self = weakref.ref(self)

            def _reset_after_fork() -> None:
                instance = weak_self()
                if instance is not None:
                    instance._after_fork()

            os.register_at_fork(after_in_child=_reset_after_fork)

    @property
    def dropped_spans(self) -> int:
        with self._lock:
            local_drops = self._local_drops
        queue_drops = self._batch_processor.queue_drops if self._batch_processor else 0
        return local_drops + queue_drops + self._exporter.failed_spans

    @property
    def attribute_failures(self) -> int:
        with self._lock:
            return self._attribute_failures

    def _note_local_drop(self, count: int = 1) -> None:
        with self._lock:
            self._local_drops += count

    def _note_attribute_failure(self) -> None:
        with self._lock:
            self._attribute_failures += 1

    def _after_fork(self) -> None:
        self._lock = threading.Lock()
        self._pid = os.getpid()
        self._exporter.after_fork()
        if self._batch_processor is not None:
            self._batch_processor.after_fork()

    def _map_attribute(self, key: str, value: object) -> object:
        if key == "org_id":
            if self._org_id_hmac_key is None:
                return _DROP
            hashed = hmac.new(
                self._org_id_hmac_key,
                str(value).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            return "foundry.org_id_hash", hashed
        if key not in _DEFAULT_ALLOWED_ATTRIBUTE_KEYS:
            return _DROP
        redacted = self._redactor.redact(key, value)
        normalized = _otel_value(redacted)
        if normalized is _DROP:
            return _DROP
        return key, normalized

    def start_span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attributes: Mapping[str, object] | None = None,
        links: Sequence[str] = (),
    ) -> Span:
        otel_span = None
        scope = None
        try:
            otel_links = []
            for traceparent in links:
                context = propagate.extract({"traceparent": traceparent})
                span_context = trace.get_current_span(context).get_span_context()
                if span_context.is_valid:
                    otel_links.append(Link(span_context))
            otel_span = self._tracer.start_span(
                name,
                kind=_KIND.get(kind, SpanKind.INTERNAL),
                links=otel_links,
            )
            scope = trace.use_span(otel_span, end_on_exit=False)
            scope.__enter__()
            wrapped = _OtelSpan(otel_span, scope, self)
            for key, value in (attributes or {}).items():
                wrapped.set_attribute(key, value)
            return wrapped
        except Exception:
            self._note_local_drop()
            if scope is not None:
                try:
                    scope.__exit__(None, None, None)
                except Exception:
                    pass
            if otel_span is not None:
                try:
                    otel_span.end()
                except Exception:
                    pass
            return _NoopSpan()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return bool(self._provider.force_flush(timeout_millis))
        except Exception:
            return False

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        try:
            self._provider.shutdown()
        except Exception:
            return None


def _otel_value(value: object) -> object:
    if isinstance(value, (bool, str, bytes, int, float)):
        return value
    if isinstance(value, (tuple, list)) and all(
        isinstance(item, (bool, str, bytes, int, float)) for item in value
    ):
        return tuple(value)
    return _DROP
