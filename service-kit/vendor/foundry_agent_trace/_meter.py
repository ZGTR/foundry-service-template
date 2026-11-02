"""The metrics port, alongside the tracing port in ``_tracer``.

Spans answer "what happened in this one request". Metrics answer "what is
happening across all of them", which is the question an SLO is written against
and the question a burn-rate alert fires on. The estate had the first and not
the second, so every availability and latency objective was unmeasurable.

The same three contracts the tracer port makes are made here, for the same
reason -- telemetry is never allowed to change a caller's result:

* ``Meter`` never raises into the caller. A recorder that cannot record drops
  the point and counts it in ``dropped_points``.
* Every instrument is usable the moment it is returned, including the inert one
  returned under back-pressure. A caller never checks for ``None``.
* Instruments are cheap to re-request by name. A caller may hold one or ask for
  it per call; an adapter is expected to return the same underlying instrument
  for the same (name, kind), so the choice is the caller's and costs nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class Counter(Protocol):
    """A monotonically increasing total: requests served, errors returned."""

    def add(self, value: int | float = 1, attributes: Mapping[str, object] | None = None) -> None:
        """Add to the total. Never raises; a negative value is ignored rather
        than rejected, because a counter that goes backwards corrupts every
        ``rate()`` computed over it and a telemetry call must not raise."""
        ...


@runtime_checkable
class Histogram(Protocol):
    """A distribution: request duration, payload size, queue depth on arrival.

    An SLO's latency objective is read off a histogram's buckets, so the bucket
    boundaries are part of the contract between the service and its alert rules.
    """

    def record(self, value: int | float, attributes: Mapping[str, object] | None = None) -> None:
        """Record one observation. Never raises."""
        ...


@runtime_checkable
class Meter(Protocol):
    dropped_points: int

    def counter(self, name: str, *, unit: str = "1", description: str = "") -> Counter:
        """Return the counter named ``name``, creating it on first request."""
        ...

    def histogram(
        self,
        name: str,
        *,
        unit: str = "s",
        description: str = "",
        boundaries: tuple[float, ...] | None = None,
    ) -> Histogram:
        """Return the histogram named ``name``, creating it on first request.

        ``boundaries`` are explicit bucket edges. They are passed rather than
        left to the SDK default because bucket count is the dominant term in
        active-series cost -- one histogram is ``len(boundaries) + 3`` series
        per attribute combination -- and because an SLO threshold that does not
        fall on a bucket edge cannot be evaluated exactly.
        """
        ...
