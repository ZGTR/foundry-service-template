"""§6: graceful shutdown on SIGTERM, draining in-flight requests.

The sequence, and why each step is in this order:

1. SIGTERM arrives. Readiness flips to NOT ready immediately, so the load
   balancer stops sending new work while the process is still perfectly able to
   finish what it has. Flipping this after the drain would mean the drain
   competes with new arrivals it cannot refuse.
2. In-flight requests keep running. A process that exits at step 1 returns 502s
   for work it had already accepted.
3. When the in-flight count reaches zero -- or `grace_seconds` elapses,
   whichever is first -- the process exits.

The timeout is not optional. A drain with no ceiling turns one stuck request
into a deployment that never completes.

THE HANDLER HANDS THE SIGNAL ON, AND THAT IS WHAT MAKES STEP 3 HAPPEN.
`install()` runs from the app's startup hook, which is INSIDE `uvicorn.run`, so
the handler it replaces is uvicorn's own -- the one that tells the server to
stop. This handler used to flip readiness and return, so nothing ever told
uvicorn: a SIGTERM was answered with "not ready" and the process served on
until SIGKILL (measured 2026-09-24, `an earlier change`: alive 80s after two
SIGTERMs; on ECS, every task stop waited out `stopTimeout`). It now flips
readiness and then calls the handler it displaced. Under uvicorn that stops
accepting, finishes in-flight requests, and runs the shutdown hook, which is
where `drain()` runs. In a bare process the displaced handler is the default,
which is restored and re-raised so the process terminates as it always would
have. The module still knows nothing about which server is running it.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

__all__ = ["GracefulShutdown"]


class GracefulShutdown:
    def __init__(
        self,
        *,
        on_shutdown_signal: Callable[[], None] | None = None,
        grace_seconds: float = 30.0,
    ) -> None:
        if grace_seconds <= 0:
            raise ValueError("grace_seconds must be positive; an unbounded drain never completes")
        self._grace_seconds = grace_seconds
        self._on_shutdown_signal = on_shutdown_signal
        self._lock = threading.Lock()
        self._in_flight = 0
        self._idle = threading.Event()
        self._idle.set()
        self.requested = threading.Event()
        self._installed: list[tuple[int, object]] = []

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def install(self, *signals: int) -> None:
        """Install the handler. Idempotent, and safe to call off the main thread.

        `signal.signal` raises `ValueError` off the main thread -- a real
        constraint under a test runner or an ASGI worker -- so that is reported
        as "not installed" rather than crashing startup.
        """
        for signal_number in signals or (signal.SIGTERM, signal.SIGINT):
            if signal_number in self._previous_handlers():
                # A second install would record THIS handler as the one it
                # displaced, and handing the signal on would then recurse.
                continue
            try:
                previous = signal.signal(signal_number, self._handle)
            except (ValueError, OSError):
                continue
            self._installed.append((signal_number, previous))

    def restore(self) -> None:
        for signal_number, previous in reversed(self._installed):
            try:
                signal.signal(signal_number, previous)  # type: ignore[arg-type]
            except (ValueError, OSError, TypeError):
                continue
        self._installed.clear()

    def _previous_handlers(self) -> dict[int, object]:
        return dict(self._installed)

    def _handle(self, signum: int, frame: object) -> None:
        # Readiness first, so the load balancer stops routing before the server
        # stops accepting; then the hand-off, without which nothing exits.
        self.request_shutdown()
        previous = self._previous_handlers().get(signum)
        if callable(previous):
            previous(signum, frame)
        elif previous == signal.SIG_DFL:
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)
        # SIG_IGN, or a handler Python did not install (None): the signal was
        # being ignored before this module arrived, so it still is.

    def request_shutdown(self) -> None:
        """Step 1: stop accepting, keep serving. Idempotent."""
        already = self.requested.is_set()
        self.requested.set()
        if not already and self._on_shutdown_signal is not None:
            self._on_shutdown_signal()

    @contextmanager
    def track_request(self) -> Iterator[None]:
        """Step 2: one in-flight request, counted for the whole of its life."""
        with self._lock:
            self._in_flight += 1
            self._idle.clear()
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._idle.set()

    def drain(self, timeout: float | None = None) -> bool:
        """Step 3: block until idle. True if it drained, False if it timed out."""
        return self._idle.wait(self._grace_seconds if timeout is None else timeout)
