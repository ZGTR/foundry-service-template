"""The health-probe port.

`runtime/` owns the SEMANTICS of liveness, readiness and startup (§6);
`transport`/`runtime` HTTP surfaces render them. The probe result type lives
here, with the protocol, so a renderer depends on a port rather than on the
runtime implementation -- the direction §1 fixes as
`runtime -> transport -> application -> domain`, with everything allowed to
reach `ports`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = ["HealthReport", "HealthReporter", "ProbeResult"]


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One dependency's answer. `detail` never carries a credential."""

    name: str
    healthy: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class HealthReport:
    """The aggregate answer for one probe kind."""

    healthy: bool
    probes: tuple[ProbeResult, ...] = field(default_factory=tuple)

    def as_data(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "probes": [
                {"name": p.name, "healthy": p.healthy, "detail": p.detail} for p in self.probes
            ],
        }


@runtime_checkable
class HealthReporter(Protocol):
    def live(self) -> HealthReport:
        """Is the PROCESS running. Never touches a dependency, never blocks."""
        ...

    def ready(self) -> HealthReport:
        """Can this process serve a request RIGHT NOW -- real dependency probes."""
        ...

    def startup(self) -> HealthReport:
        """Has one-time startup (migrations, warm-up) COMPLETED. Latches true."""
        ...
