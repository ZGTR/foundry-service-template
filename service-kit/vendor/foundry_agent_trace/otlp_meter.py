"""Official OTel SDK metrics adapter satisfying the platform's ``Meter`` port.

Two decisions here are cost decisions rather than style, and both are easy to
reverse by accident, so they are enforced in code instead of documented as
guidance.

**``service.instance.id`` is dropped from the metrics resource.** It is correct
on a span, where it says which replica served one request. On a metric it is a
per-replica dimension: every pod replacement mints a fresh series set, active
series is a windowed count, and a platform that rotates nodes on a schedule
churns replicas continuously. The estate's own tracing resource sets it, so
reusing that resource unchanged for metrics is the obvious move and the
expensive one. ``_metric_attributes`` removes it, and the contract suite asserts
the removal rather than trusting this docstring.

**Histogram buckets are always explicit.** The SDK default is 14 buckets or so,
which is ``len(boundaries) + 3`` series per attribute combination before any
route or status dimension multiplies it. Boundaries also have to land on SLO
thresholds to be evaluable at all -- see ``_slo_buckets``.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

from foundry_agent_trace._slo_buckets import REQUEST_DURATION_SECONDS
from foundry_agent_trace.otlp import OtelResource

_INSTANCE_ID_KEY = "service.instance.id"


def metric_attributes(resource: OtelResource) -> dict[str, str]:
    """The tracing resource, minus the one attribute that is per-replica.

    Exposed rather than private because the collector's relabel allowlist and
    this function must agree, and a test can only prove they agree if it can
    call this.
    """

    attributes = resource.attributes()
    attributes.pop(_INSTANCE_ID_KEY, None)
    return attributes


class _BoundInstrument:
    """Wraps an SDK instrument so a failure to record is counted, not raised.

    The port promises telemetry never changes a caller's result. The SDK is
    well-behaved, but an attribute value it cannot serialise would otherwise
    propagate out of a ``record`` call and into the request it was measuring.
    """

    def __init__(self, meter: OtlpMeter, instrument: object, kind: str) -> None:
        self._meter = meter
        self._instrument = instrument
        self._kind = kind

    def add(self, value: int | float = 1, attributes: Mapping[str, object] | None = None) -> None:
        if value < 0:
            self._meter._count_drop()
            return None
        try:
            self._instrument.add(value, dict(attributes or {}))
        except Exception:  # noqa: BLE001 - telemetry must never raise into a caller
            self._meter._count_drop()
        return None

    def record(self, value: int | float, attributes: Mapping[str, object] | None = None) -> None:
        try:
            self._instrument.record(value, dict(attributes or {}))
        except Exception:  # noqa: BLE001 - telemetry must never raise into a caller
            self._meter._count_drop()
        return None


class OtlpMeter:
    """``endpoint`` is the complete OTLP/HTTP metrics URL, normally
    ``http://otel-collector:4318/v1/metrics``. ``exporter`` and ``reader`` are
    injected seams for deterministic tests and alternative transports: exactly
    one of the three must be supplied, so a misconfigured host fails at
    construction rather than exporting silently to nowhere.
    """

    def __init__(
        self,
        *,
        resource: OtelResource,
        endpoint: str | None = None,
        exporter: MetricExporter | None = None,
        reader: MetricReader | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 2.0,
        export_interval_millis: int = 30_000,
        default_boundaries: tuple[float, ...] = REQUEST_DURATION_SECONDS,
    ) -> None:
        supplied = [endpoint is not None, exporter is not None, reader is not None]
        if sum(supplied) != 1:
            raise ValueError("provide exactly one of endpoint, exporter or reader")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if export_interval_millis < 1:
            raise ValueError("export_interval_millis must be positive")

        self._lock = threading.Lock()
        self.dropped_points = 0
        self._default_boundaries = default_boundaries
        self._instruments: dict[tuple[str, str], _BoundInstrument] = {}
        self._pid = os.getpid()

        if reader is None:
            real_exporter = exporter
            if real_exporter is None:
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                    OTLPMetricExporter,
                )

                real_exporter = OTLPMetricExporter(
                    endpoint=endpoint,
                    headers=dict(headers or {}),
                    timeout=int(timeout_seconds),
                )
            reader = PeriodicExportingMetricReader(
                real_exporter,
                export_interval_millis=export_interval_millis,
            )
        self._reader = reader

        # No provider-level View. Boundaries are set PER INSTRUMENT, at creation,
        # through the SDK's `explicit_bucket_boundaries_advisory` -- see
        # `histogram()`.
        #
        # This code previously registered one `View` over every histogram and
        # discarded the per-instrument `boundaries` argument with a `del`. Two
        # defects came out of that, in order:
        #
        # 1. The first version matched `instrument_name="*"`, which also matches
        #    COUNTERS: histogram aggregation converted every counter into a
        #    histogram, so the counter kept accepting `add()` while every
        #    `rate()` over it read nothing. The port's contract suite caught it
        #    because it runs its counter assertions against BOTH implementations;
        #    the fake alone agrees with itself and would have shipped it.
        # 2. Narrowing the View to `instrument_type=Histogram` fixed the counters
        #    and left the real defect standing: ONE set of boundaries for ALL
        #    histograms. A caller asking for `PAYLOAD_BYTES` silently got
        #    `REQUEST_DURATION_SECONDS`, so every upload above 120 bytes landed
        #    in `+Inf` and the payload-size question the instrument existed to
        #    answer was unanswerable from its own output. `_sum` and `_count`
        #    stayed exact, which is what made it survive review.
        #
        # The suite did not catch (2) because it asserted boundaries for a single
        # histogram, and one histogram cannot show that a second one gets
        # different edges. That arm now exists.
        self._provider = MeterProvider(
            resource=Resource.create(metric_attributes(resource)),
            metric_readers=[reader],
        )
        self._meter = self._provider.get_meter("foundry")

    def _count_drop(self) -> None:
        with self._lock:
            self.dropped_points += 1

    def counter(self, name: str, *, unit: str = "1", description: str = "") -> _BoundInstrument:
        key = (name, "counter")
        existing = self._instruments.get(key)
        if existing is not None:
            return existing
        created = _BoundInstrument(
            self, self._meter.create_counter(name, unit=unit, description=description), "counter"
        )
        self._instruments[key] = created
        return created

    def histogram(
        self,
        name: str,
        *,
        unit: str = "s",
        description: str = "",
        boundaries: tuple[float, ...] | None = None,
    ) -> _BoundInstrument:
        key = (name, "histogram")
        existing = self._instruments.get(key)
        if existing is not None:
            return existing
        created = _BoundInstrument(
            self,
            self._meter.create_histogram(
                name,
                unit=unit,
                description=description,
                # PER INSTRUMENT, which is the whole point: a duration histogram
                # and a payload-size histogram have nothing to say to each other,
                # and a shared set of edges makes one of them meaningless. The
                # SDK calls this an "advisory" because a View would override it;
                # no View is registered here, so it is what actually applies.
                explicit_bucket_boundaries_advisory=list(
                    boundaries if boundaries is not None else self._default_boundaries
                ),
            ),
            "histogram",
        )
        self._instruments[key] = created
        return created

    def force_flush(self, timeout_millis: int = 5_000) -> bool:
        """Export now. A Lambda freezes between invocations, so a periodic
        reader alone loses the tail of every request; the runtime calls this
        before returning."""

        return bool(self._provider.force_flush(timeout_millis))

    def shutdown(self) -> None:
        self._provider.shutdown()
