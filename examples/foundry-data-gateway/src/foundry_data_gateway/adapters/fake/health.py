"""The fake for `ports.health.HealthReporter`.

Lets a test drive `/health/live`, `/health/ready` and `/health/startup` to any
combination -- including the combinations a real dependency cannot be forced
into on demand, such as "live but not ready" during a drain.
"""

from __future__ import annotations

from foundry_data_gateway.ports import HealthReport, ProbeResult


class FakeHealthReporter:
    def __init__(
        self, *, live: bool = True, ready: bool = True, startup: bool = True
    ) -> None:
        self.live_ok = live
        self.ready_ok = ready
        self.startup_ok = startup

    def _report(self, name: str, healthy: bool) -> HealthReport:
        return HealthReport(healthy=healthy, probes=(ProbeResult(name=name, healthy=healthy),))

    def live(self) -> HealthReport:
        return self._report("process", self.live_ok)

    def ready(self) -> HealthReport:
        return self._report("dependencies", self.ready_ok)

    def startup(self) -> HealthReport:
        return self._report("startup", self.startup_ok)
