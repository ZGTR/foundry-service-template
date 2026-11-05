# GATE_MUST_FLAG_THIS -- clause: check_untimed_waits. Twenty-six waits a deadlock
# would turn into a hung suite, or into a test that carries on beside a thread
# that is still running:
#   * U1 x9: join, wait and acquire with no timeout, and with a timeout that is
#     None or negative, which is none; two of them inside a coroutine, where
#     only a call handed to one of asyncio's own wrappers belongs to the loop;
#   * U2 x4: `thread.join(5)` whose outcome nobody can read: a statement, twice,
#     a comprehension that is a statement, and an assignment to `_`;
#   * U3 x13: `with lock:` on every way this file can say a name is a lock. One
#     case per clause of the gate, so that deleting a clause changes the count.
# The fourteenth U3 is in `support/takes_a_lock_made_elsewhere.py`, which takes a
# lock this file makes.
import multiprocessing as mp
import threading
from dataclasses import dataclass, field
from threading import Condition
from threading import RLock as ReentrantLock

_REGISTRY_LOCK = threading.Lock()


def make_guard() -> object:
    return threading.Lock()


@dataclass
class Recorder:
    """A lock named only by its default factory."""

    _lock: object = field(default_factory=threading.Lock)
    seen: list = field(default_factory=list)

    def note(self, item: object) -> None:
        with self._lock:
            self.seen.append(item)


class Slot:
    """A lock named only by a class-level annotation."""

    _slot_lock: threading.Lock

    def take(self) -> None:
        with self._slot_lock:
            pass


class Shared:
    """A lock made in the class body and taken through `self`."""

    _class_lock = threading.Lock()

    def take(self) -> None:
        with self._class_lock:
            pass


class Gate:
    """A lock bound to an attribute in `__init__`, through a from-import."""

    def __init__(self) -> None:
        self._ready = Condition()

    def open(self) -> None:
        with self._ready:
            self._ready.notify_all()


def through_an_annotated_parameter(lock: threading.Lock, items: list) -> None:
    with lock:
        items.clear()


def test_the_registry_is_taken_with_a_bare_with() -> None:
    with _REGISTRY_LOCK:
        assert Recorder().seen == []


def test_a_process_lock_through_a_module_alias() -> None:
    process_lock = mp.Lock()
    with process_lock:
        pass


def test_every_kind_of_lock_is_a_lock() -> None:
    reentrant = ReentrantLock()
    semaphore = threading.Semaphore()
    bounded = threading.BoundedSemaphore()
    with reentrant, semaphore, bounded:
        pass


def test_a_name_that_is_only_annotated_or_only_assigned() -> None:
    annotated: threading.Lock = make_guard()
    valued: object = threading.Lock()
    with annotated:
        pass
    with valued:
        pass


def test_two_names_bound_to_one_lock_in_one_statement() -> None:
    first = second = threading.Lock()
    with second:
        assert first is second


async def a_coroutine_is_not_an_excuse(worker: threading.Thread, results: list) -> None:
    results.append(worker.join())

    def report(guard: object) -> None:
        print(guard.acquire())

    report(worker)


def test_waits_with_no_deadline() -> None:
    worker = threading.Thread(target=lambda: None)
    done = threading.Event()
    barrier = threading.Barrier(1)
    guard = mp.Lock()
    worker.start()
    worker.join()
    done.wait()
    barrier.wait(timeout=None)
    guard.acquire()
    guard.acquire(True)
    guard.acquire(timeout=-1)
    worker.join(None)


def test_a_join_with_a_deadline_nobody_checks() -> None:
    worker = threading.Thread(target=lambda: None)
    worker.start()
    worker.join(5)
    worker.join(timeout=5)


def test_a_timed_join_thrown_away_some_other_way() -> None:
    workers = [threading.Thread(target=lambda: None) for _ in range(2)]
    for worker in workers:
        worker.start()
    [worker.join(5) for worker in workers]
    _ = workers[0].join(5)
