"""Deliberately different vocabulary from the real runtime submodule.

`BootSequence` rather than `ServiceLifecycle`, `install_signal_traps` rather
than `install`. A lifecycle gate that only recognises the template's own naming
is matching an idiom, not a rule.
"""

import json
import logging
import signal
import threading


def install_signal_traps(handler: object) -> None:
    signal.signal(signal.SIGTERM, handler)  # type: ignore[arg-type]


def build_probe_router(reporter: object) -> dict[str, object]:
    return {
        "/health/live": reporter,
        "/health/ready": reporter,
        "/health/startup": reporter,
    }


class BootSequence:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._booted = False
        self._draining = False

    def boot(self) -> None:
        with self._lock:
            if self._booted:
                return
            logging.getLogger("pkg").info(json.dumps({"event": "boot", "correlation_id": ""}))
            self._booted = True

    def drain(self, timeout: float = 30.0) -> bool:
        self._draining = True
        return timeout > 0
