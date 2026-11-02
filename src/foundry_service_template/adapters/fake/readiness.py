"""The fake for `ports.readiness.ReadinessCheck`.

Its whole reason to exist is `tests/integration/test_health_ready.py`: it
lets a test force `/readyz` to disagree with `/healthz` without touching any
real dependency.
"""

from __future__ import annotations


class FakeReadinessCheck:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready

    def check(self) -> bool:
        return self.ready
