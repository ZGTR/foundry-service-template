"""Readiness is a distinct question from liveness.

`/healthz` answers "is this process up" and needs no port at all -- if the
process can run the handler, it is up. `/readyz` answers "can this process
currently serve a request", which means asking whatever it depends on. That
is a real check with a real failure mode, so it is a port with a real and a
fake implementation like everything else this service depends on.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ReadinessCheck(Protocol):
    def check(self) -> bool:
        """Return True when dependencies are reachable. Never raises."""
        ...
