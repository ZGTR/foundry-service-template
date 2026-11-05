"""§6: liveness, readiness and startup, with DISTINCT semantics.

The three answer different questions, and collapsing any two of them is the
failure this file exists to rule out:

* **live**    -- is the PROCESS running. Touches nothing. A live check that
  probes a dependency makes an orchestrator KILL a healthy process during a
  database blip, which turns a recoverable outage into a restart storm.
* **ready**   -- can this process serve a request RIGHT NOW. Probes real
  dependencies, and is the only one that may say no while the process is
  perfectly healthy (a drain in progress, a database failover).
* **startup** -- has one-time startup COMPLETED. It LATCHES: once true it stays
  true, because "startup finished" is a fact about the past. A startup probe
  that re-answers "no" mid-life is a readiness probe wearing the wrong name,
  and an orchestrator reading it will kill the process instead of removing it
  from the load balancer.

`tests/unit/test_runtime_probes.py` asserts each of those three sentences
separately, including the two disagreements (live-but-not-ready,
started-and-draining).
"""

from __future__ import annotations

from collections.abc import Sequence

from foundry_data_gateway.ports import HealthReport, ProbeResult, ReadinessCheck

__all__ = ["NamedReadinessProbe", "RuntimeHealthReporter"]


class NamedReadinessProbe:
    """A `ReadinessCheck` plus the name it reports under.

    The name is what makes a red `/health/ready` actionable: "not ready" tells
    an operator nothing, "database: not ready" tells them where to look.
    """

    def __init__(self, name: str, check: ReadinessCheck) -> None:
        if not name:
            raise ValueError("NamedReadinessProbe.name must be non-empty")
        self.name = name
        self._check = check

    def run(self) -> ProbeResult:
        try:
            healthy = bool(self._check.check())
        except Exception as exc:  # noqa: BLE001 -- a probe must never crash the endpoint
            # Recording is not reporting: the result carries healthy=False, so the
            # caller's verdict changes. Swallowing here without flipping the flag
            # is the "green report of a failed run" shape .claude/CLAUDE.md names.
            return ProbeResult(name=self.name, healthy=False, detail=type(exc).__name__)
        return ProbeResult(name=self.name, healthy=healthy, detail="" if healthy else "unreachable")


class RuntimeHealthReporter:
    """The `HealthReporter` port's production implementation.

    Constructed with its dependency probes; it never discovers them, and it
    never reads configuration. `draining` is set by the graceful-shutdown path
    so readiness goes red the instant SIGTERM lands, while liveness stays green
    for the whole drain.
    """

    def __init__(self, probes: Sequence[NamedReadinessProbe] = ()) -> None:
        self._probes = tuple(probes)
        self._started = False
        self.draining = False

    def mark_started(self) -> None:
        """Latch startup as complete. Idempotent, and never reversible."""
        self._started = True

    def live(self) -> HealthReport:
        # Deliberately no dependency probe. See this module's docstring.
        return HealthReport(healthy=True, probes=(ProbeResult(name="process", healthy=True),))

    def ready(self) -> HealthReport:
        if self.draining:
            return HealthReport(
                healthy=False,
                probes=(ProbeResult(name="drain", healthy=False, detail="shutting down"),),
            )
        if not self._started:
            return HealthReport(
                healthy=False,
                probes=(ProbeResult(name="startup", healthy=False, detail="not complete"),),
            )
        results = tuple(probe.run() for probe in self._probes)
        return HealthReport(healthy=all(r.healthy for r in results), probes=results)

    def startup(self) -> HealthReport:
        return HealthReport(
            healthy=self._started,
            probes=(
                ProbeResult(
                    name="startup",
                    healthy=self._started,
                    detail="" if self._started else "not complete",
                ),
            ),
        )
