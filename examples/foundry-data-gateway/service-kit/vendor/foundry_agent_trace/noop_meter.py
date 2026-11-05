"""The default meter, matching ``NoopTracer``: a host that has wired no
telemetry still constructs and calls everything, and pays nothing for it.

This is what makes ``OTEL_EXPORTER_OTLP_ENDPOINT`` unset a supported production
configuration rather than a broken one, which is in turn what makes the P1
rollback a single environment variable.
"""

from __future__ import annotations

from collections.abc import Mapping


class _NoopInstrument:
    """One object serves as both counter and histogram: neither records."""

    def add(self, value: int | float = 1, attributes: Mapping[str, object] | None = None) -> None:
        del value, attributes
        return None

    def record(self, value: int | float, attributes: Mapping[str, object] | None = None) -> None:
        del value, attributes
        return None


_INSTRUMENT = _NoopInstrument()


class NoopMeter:
    """The meter every host gets until it wires a real one."""

    def __init__(self) -> None:
        self.dropped_points = 0

    def counter(self, name: str, *, unit: str = "1", description: str = "") -> _NoopInstrument:
        del name, unit, description
        return _INSTRUMENT

    def histogram(
        self,
        name: str,
        *,
        unit: str = "s",
        description: str = "",
        boundaries: tuple[float, ...] | None = None,
    ) -> _NoopInstrument:
        del name, unit, description, boundaries
        return _INSTRUMENT
