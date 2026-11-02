"""The test double. Records every completed span up to ``max_spans``, in the
order they ended, so assertions read naturally: act, then inspect
``tracer.spans``."""

from __future__ import annotations

import contextvars
from collections.abc import Mapping, Sequence

from foundry_contracts.clock import Clock, SystemClock
from foundry_contracts.ids import new_id

from foundry_agent_trace._redact import DenyListRedactor, Redactor
from foundry_agent_trace._span import RecordedSpan, Span, _NoopSpan, _RecordingSpan, new_attributes


class InMemoryTracer:
    """Records spans in memory for test assertions.

    A span is appended to ``spans`` only when it ends -- an in-flight span is
    not yet a fact worth asserting on. ``max_spans`` bounds memory: once full,
    further spans are dropped (counted, not raised) rather than grown without
    limit, the same back-pressure contract a production adapter must honour.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        redactor: Redactor | None = None,
        max_spans: int | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._redactor = redactor or DenyListRedactor()
        self._max_spans = max_spans
        self._spans: list[RecordedSpan] = []
        self._current: contextvars.ContextVar[RecordedSpan | None] = contextvars.ContextVar(
            f"foundry_agent_trace.memory.{id(self)}", default=None
        )
        self.dropped_spans = 0

    @property
    def spans(self) -> tuple[RecordedSpan, ...]:
        return tuple(self._spans)

    def start_span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attributes: Mapping[str, object] | None = None,
        links: Sequence[str] = (),
    ) -> Span:
        if self._max_spans is not None and len(self._spans) >= self._max_spans:
            self.dropped_spans += 1
            return _NoopSpan()

        parent = self._current.get()
        record = RecordedSpan(
            trace_id=parent.trace_id if parent else new_id("trace"),
            span_id=new_id("span"),
            parent_span_id=parent.span_id if parent else None,
            name=name,
            kind=kind,
            started_at=self._clock.now(),
            attributes=new_attributes(attributes, self._redactor),
            links=tuple(links),
        )
        token = self._current.set(record)

        def _on_end(finished: RecordedSpan) -> None:
            self._current.reset(token)
            self._spans.append(finished)

        return _RecordingSpan(record, redactor=self._redactor, clock=self._clock, on_end=_on_end)
