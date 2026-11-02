"""`foundry_testkit.timed`: a wait that runs out of time fails, and says what it waited for.

Each helper is driven against a real lock, thread or event. The timeout arms use
a deadline of a few hundredths of a second on something that is really held or
really still running, so they prove the helper stops waiting, not only that it
can raise.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from foundry_testkit.timed import (
    WAIT_SECONDS,
    WaitTimedOut,
    arrived,
    held,
    joined,
    still_running,
)

SHORT = 0.05


def test_a_timed_out_wait_is_a_failed_assertion() -> None:
    """pytest reports an AssertionError as a failed test with its message."""
    assert issubclass(WaitTimedOut, AssertionError)
    assert 0 < WAIT_SECONDS <= 120


@pytest.mark.parametrize(
    "make",
    [
        threading.Lock,
        threading.RLock,
        threading.Condition,
        threading.Semaphore,
        threading.BoundedSemaphore,
    ],
)
def test_held_takes_the_lock_and_gives_it_back(make: Callable[[], threading.Lock]) -> None:
    guard = make()

    with held(guard, "the guard"):
        ran = True

    assert ran
    assert guard.acquire(timeout=1), "held() must release what it took"
    guard.release()


def test_held_gives_the_lock_back_when_the_body_raises() -> None:
    guard = threading.Lock()

    with pytest.raises(RuntimeError, match="body blew up"), held(guard, "the guard"):
        raise RuntimeError("body blew up")

    assert not guard.locked()


def test_held_stops_waiting_and_names_what_it_waited_for() -> None:
    guard = threading.Lock()
    assert guard.acquire(timeout=1)
    entered: list[str] = []

    with (
        pytest.raises(WaitTimedOut, match=r"the registry lock was not acquired within 0\.05s"),
        held(guard, "the registry lock", seconds=SHORT),
    ):
        entered.append("body")

    assert entered == [], "the body must not run without the lock"
    assert guard.locked(), "held() must not release a lock it never took"
    guard.release()


def test_joined_returns_once_the_thread_has_finished() -> None:
    worker = threading.Thread(target=lambda: None)
    worker.start()

    joined(worker, "the worker")

    assert not worker.is_alive()


def test_joined_fails_while_the_thread_is_still_running() -> None:
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(30))
    worker.start()
    try:
        with pytest.raises(WaitTimedOut, match=r"the worker was still running after 0\.05s"):
            joined(worker, "the worker", seconds=SHORT)
        assert worker.is_alive()
    finally:
        release.set()
        joined(worker, "the worker")


def test_arrived_returns_once_the_event_is_set() -> None:
    done = threading.Event()
    done.set()

    arrived(done, "the signal")


def test_arrived_fails_when_the_event_never_comes() -> None:
    done = threading.Event()

    with pytest.raises(WaitTimedOut, match=r"the signal had not happened after 0\.05s"):
        arrived(done, "the signal", seconds=SHORT)


#: What the standard library reads as "wait for ever", and what is no number of
#: seconds at all. Each helper must refuse these BEFORE it waits.
NOT_A_DEADLINE = [None, -1, -0.5, float("inf"), float("nan"), True]


@pytest.mark.parametrize("seconds", NOT_A_DEADLINE)
def test_held_refuses_a_deadline_that_is_not_one(seconds: object) -> None:
    """On a FREE lock, so a helper that passed the value on would take the lock and
    return, and this test would fail instead of hanging."""
    guard = threading.Lock()

    with pytest.raises(ValueError, match="needs a deadline"), held(guard, "the guard", seconds):
        raise AssertionError("the body ran")

    assert not guard.locked(), "the lock was taken before the deadline was refused"


@pytest.mark.parametrize("seconds", NOT_A_DEADLINE)
def test_joined_and_arrived_refuse_a_deadline_that_is_not_one(seconds: object) -> None:
    """On a finished thread and a set event, for the same reason."""
    worker = threading.Thread(target=lambda: None)
    worker.start()
    joined(worker, "the worker")
    done = threading.Event()
    done.set()

    with pytest.raises(ValueError, match="needs a deadline"):
        joined(worker, "the worker", seconds)
    with pytest.raises(ValueError, match="needs a deadline"):
        arrived(done, "the signal", seconds)
    with pytest.raises(ValueError, match="needs a deadline"):
        still_running(worker, "the worker", seconds)


def test_a_deadline_of_zero_is_a_deadline() -> None:
    guard = threading.Lock()

    with held(guard, "the guard", seconds=0):
        assert guard.locked()


def test_still_running_passes_while_the_thread_is_blocked() -> None:
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(30))
    worker.start()
    try:
        still_running(worker, "the blocked worker", seconds=SHORT)
        assert worker.is_alive()
    finally:
        release.set()
        joined(worker, "the blocked worker")


def test_still_running_fails_when_the_thread_got_through() -> None:
    worker = threading.Thread(target=lambda: None)
    worker.start()

    with pytest.raises(AssertionError, match=r"the worker finished within 0\.05s") as failed:
        still_running(worker, "the worker", seconds=SHORT)

    assert not isinstance(failed.value, WaitTimedOut), "finishing early is not a timeout"
