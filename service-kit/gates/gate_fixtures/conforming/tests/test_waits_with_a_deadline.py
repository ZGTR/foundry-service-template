"""GATE FIXTURE, never collected: check_untimed_waits' false-red direction.

Ten calls to `join`, `wait` or `acquire` that the gate must SEE and leave alone,
and three lock bindings none of which is taken with a bare `with`. A gate that
flagged every call to those three names would flag `os.path.join`; one that saw no
call here would be quiet for the wrong reason, which is why the gate counts them.

Two bare `with` statements on things that are not locks, one a name and one an
attribute: a gate that flagged every `with name:` would flag both.

The coroutine at the bottom is the event loop's kind of wait, and so is the
coroutine a sync function hands to `asyncio.wait_for`. They are skipped, not
counted: their deadline is `asyncio.wait_for`.
"""

import asyncio
import contextlib
import os
import subprocess
import sys
import threading

from foundry_testkit.timed import arrived, held, joined, still_running

_LOCK = threading.Lock()


class Counter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.cleanup = contextlib.ExitStack()
        self.value = 0

    def bump(self) -> None:
        with held(self._lock, "the counter's lock"):
            self.value += 1

    def close(self) -> None:
        with self.cleanup:
            self.value = 0


def with_a_deadline(lock: threading.Lock, items: list) -> None:
    with held(lock, "the caller's lock"):
        items.clear()


def test_every_wait_has_a_deadline(tmp_path) -> None:
    worker = threading.Thread(target=lambda: None)
    done = threading.Event()
    barrier = threading.Barrier(1)
    worker.start()
    joined(worker, "the worker")
    done.set()
    arrived(done, "the worker's signal")
    barrier.wait(timeout=5)
    assert done.wait(5)
    assert _LOCK.acquire(timeout=5)
    _LOCK.release()
    assert _LOCK.acquire(False)
    _LOCK.release()
    assert _LOCK.acquire(blocking=False)
    _LOCK.release()
    assert _LOCK.acquire(True, 5)
    _LOCK.release()

    separator = "-"
    label = ", ".join(["a", separator.join(["b", "c"])])
    path = os.path.join(str(tmp_path), "note.txt")
    with open(path, "w") as handle:
        handle.write(label)

    stack = contextlib.ExitStack()
    with stack:
        stack.callback(done.clear)

    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)


def test_a_thread_that_is_meant_to_be_blocked() -> None:
    release = threading.Event()
    blocked = threading.Thread(target=lambda: release.wait(30))
    blocked.start()
    still_running(blocked, "the blocked thread", seconds=0.01)
    release.set()
    joined(blocked, "the blocked thread")


def runs_a_coroutine_with_a_deadline_from_sync_code(event: asyncio.Event) -> None:
    asyncio.run(asyncio.wait_for(event.wait(), timeout=5))


async def waits_on_the_event_loop(event: asyncio.Event, loop_lock: asyncio.Lock) -> None:
    await event.wait()
    await asyncio.wait_for(event.wait(), timeout=5)
    await asyncio.wait_for(fut=event.wait(), timeout=5)
    async with loop_lock:
        event.clear()
