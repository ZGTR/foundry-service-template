"""§6: graceful shutdown on SIGTERM, draining in-flight requests.

The SIGTERM arm sends a REAL signal to this process rather than calling the
handler directly. Calling `_handle(15, None)` would pass while the handler was
never installed -- which is the one thing this test exists to prove.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from foundry_testkit.timed import joined

from foundry_data_gateway.runtime import GracefulShutdown

_ROOT = Path(__file__).resolve().parents[2]
# THIS checkout's source, never whatever copy the interpreter has installed.
_IMPORT_ROOTS = (_ROOT / "src", _ROOT / "service-kit" / "vendor")


def _recording_handler(events: list[str]) -> Callable[[int, object], None]:
    def _previous(signum: int, _frame: object) -> None:
        events.append(f"previous:{signum}")

    return _previous


def test_a_real_sigterm_requests_shutdown_and_then_hands_the_signal_on() -> None:
    """Readiness goes red FIRST, then the displaced handler runs.

    Under uvicorn the displaced handler is the one that stops the server, so a
    handler that returns without calling it answers SIGTERM with "not ready" and
    never exits. The displaced handler here records instead of
    stopping anything, which is also why this test does not install over the
    default: SIGUSR1's default action terminates the test process.
    """
    events: list[str] = []
    before = signal.signal(signal.SIGUSR1, _recording_handler(events))
    shutdown = GracefulShutdown(on_shutdown_signal=lambda: events.append("not-ready"))
    shutdown.install(signal.SIGUSR1)
    try:
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.05)
    finally:
        shutdown.restore()
        signal.signal(signal.SIGUSR1, before)

    assert shutdown.requested.is_set()
    assert events == ["not-ready", f"previous:{int(signal.SIGUSR1)}"], (
        "readiness must go red the moment the signal lands, and the handler it "
        "displaced must then run -- otherwise nothing ever stops the server"
    )


def test_an_ignored_signal_stays_ignored() -> None:
    """A signal that was SIG_IGN before install is flipped to not-ready and nothing more."""
    events: list[str] = []
    before = signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    shutdown = GracefulShutdown(on_shutdown_signal=lambda: events.append("not-ready"))
    shutdown.install(signal.SIGUSR1)
    try:
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.05)
    finally:
        shutdown.restore()
        signal.signal(signal.SIGUSR1, before)

    assert events == ["not-ready"]


def test_a_second_install_does_not_hand_the_signal_to_itself() -> None:
    """Installing twice must not record this handler as the one it displaced.

    If it did, the hand-off would call `_handle` again, forever.
    """
    events: list[str] = []
    before = signal.signal(signal.SIGUSR1, _recording_handler(events))
    shutdown = GracefulShutdown(on_shutdown_signal=lambda: events.append("not-ready"))
    shutdown.install(signal.SIGUSR1)
    shutdown.install(signal.SIGUSR1)
    try:
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.05)
    finally:
        shutdown.restore()
        signal.signal(signal.SIGUSR1, before)

    assert events == ["not-ready", f"previous:{int(signal.SIGUSR1)}"]
    assert signal.getsignal(signal.SIGUSR1) is before


def test_a_bare_process_still_terminates_on_sigterm() -> None:
    """With the DEFAULT handler displaced, the process must still die of SIGTERM.

    Run in a child process, because the behaviour under test is termination.
    """
    source = (
        "import os, signal, time\n"
        "from foundry_data_gateway.runtime import GracefulShutdown\n"
        "g = GracefulShutdown()\n"
        "g.install(signal.SIGTERM)\n"
        "os.kill(os.getpid(), signal.SIGTERM)\n"
        "time.sleep(5)\n"
        "print('SURVIVED')\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(p) for p in _IMPORT_ROOTS)}
    child = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )

    assert child.returncode == -signal.SIGTERM, (child.returncode, child.stdout, child.stderr)
    assert "SURVIVED" not in child.stdout


def test_the_signal_handler_is_restored_afterwards() -> None:
    """A test that leaves a handler installed poisons every later test in the
    process -- and the failure surfaces somewhere else entirely."""
    before = signal.getsignal(signal.SIGUSR1)
    shutdown = GracefulShutdown()
    shutdown.install(signal.SIGUSR1)
    shutdown.restore()

    assert signal.getsignal(signal.SIGUSR1) is before


def test_request_shutdown_is_idempotent() -> None:
    calls: list[str] = []
    shutdown = GracefulShutdown(on_shutdown_signal=lambda: calls.append("x"))

    shutdown.request_shutdown()
    shutdown.request_shutdown()

    assert calls == ["x"]


def test_an_in_flight_request_is_counted_for_the_whole_of_its_life() -> None:
    shutdown = GracefulShutdown()

    assert shutdown.in_flight == 0
    with shutdown.track_request():
        assert shutdown.in_flight == 1
    assert shutdown.in_flight == 0


def test_the_count_survives_an_exception_in_the_request() -> None:
    """A handler that raises must still decrement, or the drain never completes
    and every deploy waits out the full grace period."""
    shutdown = GracefulShutdown()

    with pytest.raises(RuntimeError), shutdown.track_request():
        raise RuntimeError("handler blew up")

    assert shutdown.in_flight == 0


def test_drain_waits_for_an_in_flight_request_and_then_returns_true() -> None:
    shutdown = GracefulShutdown(grace_seconds=5.0)
    released = threading.Event()

    def _serve() -> None:
        with shutdown.track_request():
            released.wait(2.0)

    worker = threading.Thread(target=_serve)
    worker.start()
    time.sleep(0.05)
    assert shutdown.in_flight == 1

    assert shutdown.drain(timeout=0.2) is False, "a live request must not be reported as drained"

    released.set()
    joined(worker, "the request thread", seconds=2.0)

    assert shutdown.drain(timeout=2.0) is True


def test_drain_returns_false_rather_than_blocking_forever() -> None:
    shutdown = GracefulShutdown(grace_seconds=0.1)
    with shutdown.track_request():
        assert shutdown.drain() is False


def test_an_unbounded_grace_period_is_refused() -> None:
    """One stuck request must not become a deployment that never completes."""
    with pytest.raises(ValueError, match="positive"):
        GracefulShutdown(grace_seconds=0)
