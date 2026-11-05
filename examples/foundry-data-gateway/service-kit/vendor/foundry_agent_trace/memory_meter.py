"""The metrics test double, matching ``InMemoryTracer``.

This exists so the contract's S8 rule is satisfiable rather than merely stated.
S8 forbids ``unittest.mock``, ``MagicMock``, ``@patch`` and
``monkeypatch.setattr`` on anything that has a port, and a meter has a port --
so a test that wants to assert "the handler recorded one 500" must have a real
recording implementation to inject. Without this class the only ways to write
that test are to patch the collaborator, which the contract forbids, or to
assert nothing, which is what the estate did.

Assertions read in the order the work happened: act, then inspect
``meter.points``. Aggregation is deliberately NOT performed here. A histogram
bucket is an aggregate over a window, and asserting on an aggregate hides which
observation carried which attributes -- the exact fact a cross-tenant leak or a
mislabelled status class shows up in.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricPoint:
    """One recorded observation, with the attributes it carried."""

    instrument: str
    kind: str
    value: float
    attributes: Mapping[str, object] = field(default_factory=dict)
    unit: str = ""


class _RecordingInstrument:
    def __init__(self, meter: InMemoryMeter, name: str, kind: str, unit: str) -> None:
        self._meter = meter
        self._name = name
        self._kind = kind
        self._unit = unit

    def add(self, value: int | float = 1, attributes: Mapping[str, object] | None = None) -> None:
        if value < 0:
            # A counter that goes backwards corrupts every rate() over it. The
            # port says this is ignored, not raised -- so it is counted as a
            # drop, which makes the mistake visible without failing the caller.
            self._meter.dropped_points += 1
            return None
        self._meter._append(self._name, self._kind, float(value), attributes, self._unit)
        return None

    def record(self, value: int | float, attributes: Mapping[str, object] | None = None) -> None:
        self._meter._append(self._name, self._kind, float(value), attributes, self._unit)
        return None


class InMemoryMeter:
    """Records every metric point in memory for test assertions.

    ``max_points`` bounds memory and exercises the same back-pressure contract a
    production adapter must honour: once full, further points are dropped and
    counted rather than raised or grown without limit.
    """

    def __init__(self, *, max_points: int | None = None) -> None:
        self._max_points = max_points
        self._points: list[MetricPoint] = []
        self._instruments: dict[tuple[str, str], _RecordingInstrument] = {}
        self.dropped_points = 0

    @property
    def points(self) -> tuple[MetricPoint, ...]:
        return tuple(self._points)

    def points_for(self, instrument: str) -> tuple[MetricPoint, ...]:
        return tuple(point for point in self._points if point.instrument == instrument)

    def _append(
        self,
        name: str,
        kind: str,
        value: float,
        attributes: Mapping[str, object] | None,
        unit: str,
    ) -> None:
        if self._max_points is not None and len(self._points) >= self._max_points:
            self.dropped_points += 1
            return None
        self._points.append(
            MetricPoint(
                instrument=name,
                kind=kind,
                value=value,
                attributes=dict(attributes or {}),
                unit=unit,
            )
        )
        return None

    def _instrument(self, name: str, kind: str, unit: str) -> _RecordingInstrument:
        key = (name, kind)
        existing = self._instruments.get(key)
        if existing is not None:
            return existing
        created = _RecordingInstrument(self, name, kind, unit)
        self._instruments[key] = created
        return created

    def counter(self, name: str, *, unit: str = "1", description: str = "") -> _RecordingInstrument:
        del description
        return self._instrument(name, "counter", unit)

    def histogram(
        self,
        name: str,
        *,
        unit: str = "s",
        description: str = "",
        boundaries: tuple[float, ...] | None = None,
    ) -> _RecordingInstrument:
        del description, boundaries
        return self._instrument(name, "histogram", unit)
