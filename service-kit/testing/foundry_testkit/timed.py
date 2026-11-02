"""Waits that end, and say what they were waiting for.

WHAT WENT WRONG. comms-core's suite hung for 25 minutes on a lock nobody was going
to release, and printed nothing. `lock.acquire()`,
`thread.join()` and `event.wait()` have no deadline unless one is passed, so a
test that deadlocks does not fail. It stops, and the run is killed from outside
with no line naming the wait.

Passing a timeout is half of it. `Thread.join(5)` returns None whether the thread
finished or not, and `Event.wait(5)` returns False that nobody is made to read, so
a timed wait that is not checked turns a hang into a test that carries on beside a
thread still running. Each helper here checks, and raises `WaitTimedOut` naming
what it waited for.

`service-kit/gates/check_untimed_waits.py` is the other half: it refuses the
untimed forms in test code, and a bare `thread.join(...)` statement, so the
helpers are the way through rather than a convention.

`barrier.wait(timeout=...)` and `process.wait(timeout=...)` need no helper. Both
raise on their own when the time runs out.

A DEADLINE HAS TO BE ONE. The standard library reads `acquire(timeout=-1)`,
`join(None)` and `wait(None)` as "wait for ever", so a helper that passed those
on would be the untimed wait with a reassuring name, and the gate could not see
it: the call site is `held(...)`, not `acquire`. Every helper refuses a `seconds`
that is None, negative, infinite or not a number, before it waits.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

__all__ = ["WAIT_SECONDS", "WaitTimedOut", "arrived", "held", "joined", "still_running"]

#: Long enough that a loaded host does not trip it, short enough that a deadlock
#: is reported while the run that caused it is still on the screen.
WAIT_SECONDS = 60.0


class WaitTimedOut(AssertionError):
    """A wait ran out of time. An AssertionError, so pytest reports a failed test
    with the message, and not an error in a fixture or a hang."""


class _Acquirable(Protocol):
    def acquire(self, blocking: bool = ..., timeout: float = ...) -> bool: ...

    def release(self) -> None: ...


class _Joinable(Protocol):
    def join(self, timeout: float | None = ...) -> None: ...

    def is_alive(self) -> bool: ...


def _deadline(seconds: float) -> float:
    """`seconds`, once it is known to be a deadline."""
    if seconds is None or isinstance(seconds, bool) or not 0 <= seconds < math.inf:
        raise ValueError(
            f"a wait needs a deadline of zero seconds or more, got {seconds!r}; None and a "
            "negative number mean 'wait for ever' to the standard library"
        )
    return seconds


@contextmanager
def held(lock: _Acquirable, what: str, seconds: float = WAIT_SECONDS) -> Iterator[None]:
    """`with lock:` with a deadline. Works for Lock, RLock, Condition and Semaphore."""
    if not lock.acquire(timeout=_deadline(seconds)):
        raise WaitTimedOut(f"{what} was not acquired within {seconds:g}s; something still holds it")
    try:
        yield
    finally:
        lock.release()


def joined(thread: _Joinable, what: str, seconds: float = WAIT_SECONDS) -> None:
    """`thread.join()` with a deadline, and a failure when the thread is still running."""
    thread.join(_deadline(seconds))
    if thread.is_alive():
        raise WaitTimedOut(f"{what} was still running after {seconds:g}s")


def still_running(thread: _Joinable, what: str, seconds: float) -> None:
    """The opposite check: give the thread `seconds` to finish, and fail if it DID.

    For a test that proves a thread is blocked, on a lock another holds or a row
    another transaction has. `seconds` has no default: how long "still blocked" has
    to hold is the test's claim, and it is time the test always spends.
    """
    thread.join(_deadline(seconds))
    if not thread.is_alive():
        raise AssertionError(f"{what} finished within {seconds:g}s; it should still be blocked")


def arrived(event: threading.Event, what: str, seconds: float = WAIT_SECONDS) -> None:
    """`event.wait()` with a deadline, and a failure when the event was never set."""
    if not event.wait(_deadline(seconds)):
        raise WaitTimedOut(f"{what} had not happened after {seconds:g}s")
