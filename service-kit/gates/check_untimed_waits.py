#!/usr/bin/env python3
"""Test code may not wait without a deadline.

WHAT WENT WRONG. comms-core's suite hung for 25 minutes on a lock nobody was going
to release and printed nothing. A test that deadlocks does not
fail: it stops, and the run is killed from outside with no line naming the wait.
`lock.acquire()`, `thread.join()` and `event.wait()` have no deadline unless one is
passed, and `with lock:` cannot be given one at all.

THREE FINDINGS, read from the syntax tree of every file under `tests/` and
`service-kit/testing/`:

  U1  `x.join()`, `x.wait()` or `x.acquire()` with no timeout. A timeout of None
      is no timeout. `acquire(False)` does not block and is not a finding.
  U2  `thread.join(5)` whose result nobody can read: as a statement, as the
      element of a comprehension that is itself a statement, or assigned to `_`.
      `join` returns None whether the thread finished or not, so the deadline is
      spent and the test carries on beside a thread that is still running.
      `foundry_testkit.timed.joined` checks; `still_running` is the opposite
      check, for a thread that is meant to be blocked.
  U3  `with lock:` where `lock` is a threading or multiprocessing lock. Use
      `with held(lock, "what"):`.

HOW U3 KNOWS A LOCK. Not by its name. A name is a lock when the same FILE binds it
to `threading.Lock()` (or RLock, Condition, Semaphore, BoundedSemaphore), or
annotates it as one. An attribute (`self._lock`) is a lock when ANY scanned file
binds an attribute of that name to one, in `__init__`, as a dataclass field or as
a class attribute, because the class that makes the lock and the code that takes
it are often in different files.

WHAT IT DOES NOT SEE.
  * A lock that arrives with no binding or annotation in test code: one built in
    `src/`, or returned by a function. U3 is quiet on it. So is a module lock of
    ANOTHER file taken as `support.LOCK` or imported by name, a lock that is one
    arm of an expression (`lock or threading.Lock()`), one held in a tuple or a
    dict, one aliased (`mine = theirs`), and one annotated `threading.Lock | None`
    or with the annotation in quotes.
  * `await x.wait()`, `async with lock:`, and a call handed to one of asyncio's
    own wrappers (`asyncio.wait_for(x.wait(), 5)`, `gather`, `shield`,
    `create_task`, `ensure_future`, `run_until_complete`). Those belong to an
    event loop, whose deadline is `asyncio.wait_for`; they are skipped here, not
    judged. Any other blocking call inside an `async def` is judged like the rest.
  * A timeout that is a NAME whose value is None, or `float("inf")`.
  * A timed `join` whose result is thrown away some other way than the three U2
    names: passed to a function that ignores it, or bound to a name nobody reads.
  * `event.wait(5)` whose False result is not read. `timed.arrived` checks; this
    gate does not require it, because a Barrier's and a process's `wait(timeout)`
    raise on their own and the syntax does not say which one a call is.
  * `concurrent.futures.wait(futures)` and `os.path.join(a, b)`: a first argument
    reads as a timeout, so both are quiet. Right for the second, a miss for the
    first.
  * Any other blocking call: `queue.get()`, `future.result()`, a socket read.

WHAT IT FLAGS THAT HAS NO TIMEOUT TO GIVE. `Pool.join()` and `Queue.join()` take
none, so U1's remedy cannot be followed there. Give the wait a thread of its own
and `joined(...)` that, or use the `multiprocessing` forms that do take one.

`service-kit/testing/foundry_testkit/timed.py` is the one file allowed a bare
`join(...)` statement, because it is where the check after the join lives: one in
`joined`, one in `still_running`. The exemption is by path and is counted: the
file must exist, and read TOGETHER with every other scanned file it must hold
exactly those two findings. Read alone it would be excused a `with x._lock:` on a
lock some test file makes, because that is only known across files.

Exit codes: `gate_exit_codes.py`.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import (  # noqa: E402
    EXPECTED_MARKED_FIXTURE_FILES,
    FIXTURE_ROOT,
    KIT_ROOT,
    PACKAGE_ROOT,
    marker_count,
    python_sources,
    unmarked_flagged_files,
)
from gate_exit_codes import GREEN, finding, refuse, skip  # noqa: E402

TESTS_DIR = PACKAGE_ROOT / "tests"
KIT_TESTING = KIT_ROOT / "testing"
HELPER = KIT_TESTING / "foundry_testkit" / "timed.py"

OFFENDING = FIXTURE_ROOT / "offending"
CONFORMING = FIXTURE_ROOT / "conforming"
OFFENDING_FIXTURE = OFFENDING / "tests" / "test_waits_forever.py"
#: Takes a lock OFFENDING_FIXTURE makes. Quiet alone, one finding read together.
OFFENDING_ELSEWHERE = OFFENDING / "tests" / "support" / "takes_a_lock_made_elsewhere.py"
CONFORMING_FIXTURE = CONFORMING / "tests" / "test_waits_with_a_deadline.py"

BLOCKING_VERBS = frozenset({"join", "wait", "acquire"})
LOCK_TYPES = frozenset({"Lock", "RLock", "Condition", "Semaphore", "BoundedSemaphore"})
LOCK_MODULES = frozenset({"threading", "multiprocessing"})
#: asyncio's own wrappers. A call handed to one is a coroutine for the loop.
LOOP_WRAPPERS = frozenset(
    {"wait_for", "gather", "shield", "create_task", "ensure_future", "run_until_complete"}
)

#: HAND-WRITTEN. What the offending fixture plants, by finding, and how many
#: blocking calls and lock bindings the conforming fixture holds that must be
#: SEEN and left alone. A conforming arm that saw nothing is quiet for the wrong
#: reason.
EXPECTED_OFFENDING = {"U1": 9, "U2": 4, "U3": 14}
#: Of those fourteen, the one that exists only because attribute locks are shared
#: across files.
EXPECTED_ELSEWHERE = 1
EXPECTED_CONFORMING_CALLS = 10
EXPECTED_CONFORMING_LOCKS = 3
#: The helper holds exactly two bare `join(...)` statements, each followed by the
#: check that is the reason it exists: `joined` and `still_running`.
EXPECTED_HELPER_FINDINGS = 2
#: A scan that read almost nothing reports a clean estate it never looked at.
MIN_TEST_FILES = 10


@dataclass
class Reading:
    findings: list[str] = field(default_factory=list)
    #: What an exempt file would have been flagged for. Counted, never dropped.
    excused: list[str] = field(default_factory=list)
    calls: int = 0
    locks: int = 0
    files: int = 0


def _is_none(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_false(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _has_deadline(call: ast.Call, verb: str) -> bool:
    """Whether this blocking call can end on its own."""
    timeout = _keyword(call, "timeout")
    if verb == "acquire":
        blocking = call.args[0] if call.args else _keyword(call, "blocking")
        if _is_false(blocking):
            return True
        if timeout is None and len(call.args) > 1:
            timeout = call.args[1]
    elif timeout is None and call.args:
        timeout = call.args[0]
    if timeout is None or _is_none(timeout):
        return False
    negative = isinstance(timeout, ast.UnaryOp) and isinstance(timeout.op, ast.USub)
    return not negative


def _lock_makers(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(module aliases of threading/multiprocessing, local names of their lock types)."""
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in LOCK_MODULES:
                    modules.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in LOCK_MODULES:
            for alias in node.names:
                if alias.name in LOCK_TYPES:
                    names.add(alias.asname or alias.name)
    return modules, names


def _is_lock_type(node: ast.AST | None, modules: set[str], names: set[str]) -> bool:
    """`threading.Lock` or an imported `Lock`, as a reference rather than a call."""
    if isinstance(node, ast.Name):
        return node.id in names
    return (
        isinstance(node, ast.Attribute)
        and node.attr in LOCK_TYPES
        and isinstance(node.value, ast.Name)
        and node.value.id in modules
    )


def _makes_a_lock(value: ast.AST | None, modules: set[str], names: set[str]) -> bool:
    """`threading.Lock()`, or `field(default_factory=threading.Lock)`."""
    if not isinstance(value, ast.Call):
        return False
    if _is_lock_type(value.func, modules, names):
        return True
    return _is_lock_type(_keyword(value, "default_factory"), modules, names)


def lock_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(names this file binds or annotates as a lock, attribute names it binds as one)."""
    modules, makers = _lock_makers(tree)
    names: set[str] = set()
    attributes: set[str] = set()

    def bind(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            attributes.add(target.attr)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _makes_a_lock(node.value, modules, makers):
            for target in node.targets:
                bind(target)
        elif isinstance(node, ast.AnnAssign):
            annotated = _is_lock_type(node.annotation, modules, makers)
            if annotated or _makes_a_lock(node.value, modules, makers):
                bind(node.target)
        elif isinstance(node, ast.arg) and _is_lock_type(node.annotation, modules, makers):
            names.add(node.arg)
        elif isinstance(node, ast.ClassDef):
            # A class-level `_lock: threading.Lock = field(...)` is read as `self._lock`.
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    annotated = _is_lock_type(stmt.annotation, modules, makers)
                    if annotated or _makes_a_lock(stmt.value, modules, makers):
                        attributes.add(stmt.target.id)
                elif isinstance(stmt, ast.Assign) and _makes_a_lock(stmt.value, modules, makers):
                    # `_lock = threading.Lock()` in a class body is `self._lock` too.
                    attributes.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
    return names, attributes


def _coroutines(tree: ast.Module) -> set[int]:
    """Calls that make a coroutine for the event loop and block nothing themselves:
    the operand of `await`, and a call handed to one of asyncio's wrappers
    (`asyncio.wait_for(event.wait(), 5)`), by position or by keyword.

    By the WRAPPER'S NAME, not by being inside an `async def`. Every argument of
    every call in a coroutine used to be skipped, which hid
    `results.append(thread.join())` and a sync helper nested in the coroutine."""
    found = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Await)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = node.func
            name = getattr(callee, "attr", None) or getattr(callee, "id", None)
            if name in LOOP_WRAPPERS:
                found.update(id(argument) for argument in node.args)
                found.update(id(keyword.value) for keyword in node.keywords)
    return found


def _discarded(tree: ast.Module) -> set[int]:
    """Calls whose result nobody can read: an expression statement, the element of
    a comprehension that is itself one, and the value of an assignment to `_`."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr):
            found.add(id(node.value))
            if isinstance(node.value, ast.ListComp | ast.SetComp | ast.GeneratorExp):
                found.add(id(node.value.elt))
        elif isinstance(node, ast.Assign) and all(
            isinstance(target, ast.Name) and target.id == "_" for target in node.targets
        ):
            found.add(id(node.value))
    return found


def read_file(path: Path, relative: str, shared_attributes: set[str]) -> Reading:
    """Every finding in one file, plus how many blocking calls and locks it read."""
    reading = Reading(files=1)
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as exc:
        reading.findings.append(f"{relative}:{exc.lineno}: unreadable: {exc.msg}")
        return reading
    names, attributes = lock_bindings(tree)
    reading.locks = len(names) + len(attributes)
    on_the_loop = _coroutines(tree)
    discarded = _discarded(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            verb = node.func.attr
            if verb not in BLOCKING_VERBS or id(node) in on_the_loop:
                continue
            if isinstance(node.func.value, ast.Constant):
                continue  # `", ".join(parts)`: a string's join, not a wait
            reading.calls += 1
            receiver = ast.unparse(node.func.value)
            if not _has_deadline(node, verb):
                reading.findings.append(
                    f"{relative}:{node.lineno}: U1: {receiver}.{verb}() has no timeout"
                )
            elif verb == "join" and id(node) in discarded:
                reading.findings.append(
                    f"{relative}:{node.lineno}: U2: {receiver}.join(...) returns None whether "
                    "or not the thread finished; use foundry_testkit.timed.joined"
                )
        elif isinstance(node, ast.With):
            for item in node.items:
                target = item.context_expr
                is_lock = (isinstance(target, ast.Name) and target.id in names) or (
                    isinstance(target, ast.Attribute) and target.attr in shared_attributes
                )
                if is_lock:
                    reading.findings.append(
                        f"{relative}:{node.lineno}: U3: `with {ast.unparse(target)}:` waits "
                        "for the lock with no deadline; use foundry_testkit.timed.held"
                    )
    return reading


def read_tree(files: list[tuple[Path, str]], exempt: frozenset[Path] = frozenset()) -> Reading:
    """Read `files` as one population: attribute locks are shared across all of them."""
    shared: set[str] = set()
    for path, _ in files:
        try:
            shared |= lock_bindings(ast.parse(path.read_text()))[1]
        except SyntaxError:
            continue
    total = Reading()
    for path, relative in files:
        reading = read_file(path, relative, shared)
        total.files += 1
        total.calls += reading.calls
        total.locks += reading.locks
        if path in exempt:
            total.excused.extend(reading.findings)
        else:
            total.findings.extend(reading.findings)
    return total


def _by_kind(findings: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in findings:
        kind = item.split(": ", 2)[1]
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def fixture_problems() -> list[str]:
    """Both fixture arms and the helper exemption, each against a hand-written number."""
    problems: list[str] = []
    for fixture in (OFFENDING_FIXTURE, OFFENDING_ELSEWHERE, CONFORMING_FIXTURE, HELPER):
        if not fixture.is_file():
            problems.append(f"{fixture} is missing; its arm cannot run")
    if problems:
        return problems

    elsewhere = str(OFFENDING_ELSEWHERE.relative_to(OFFENDING))
    offending = read_tree(
        [
            (OFFENDING_FIXTURE, str(OFFENDING_FIXTURE.relative_to(OFFENDING))),
            (OFFENDING_ELSEWHERE, elsewhere),
        ]
    )
    found = dict(sorted(_by_kind(offending.findings).items()))
    if found != EXPECTED_OFFENDING:
        problems.append(
            f"the OFFENDING fixture produced {found}, expected the hand-written "
            f"{EXPECTED_OFFENDING}: {offending.findings}"
        )
    across = sum(item.startswith(f"{elsewhere}:") for item in offending.findings)
    alone = read_tree([(OFFENDING_ELSEWHERE, elsewhere)]).findings
    if across != EXPECTED_ELSEWHERE or alone:
        problems.append(
            f"{elsewhere} has {across} finding(s) read with the file that makes its lock "
            f"and {len(alone)} read alone, expected {EXPECTED_ELSEWHERE} and 0: an attribute "
            "lock must be known across files, and only through a binding"
        )
    unmarked = unmarked_flagged_files(OFFENDING, offending.findings)
    if unmarked:
        problems.append(f"flagged fixture file(s) carry no marker: {unmarked}")

    conforming = read_tree([(CONFORMING_FIXTURE, CONFORMING_FIXTURE.name)])
    if conforming.findings:
        problems.append(
            f"the CONFORMING fixture was flagged: {conforming.findings}. A wait with a "
            "deadline must stay quiet, or this gate is counting ordinary calls."
        )
    if (conforming.calls, conforming.locks) != (
        EXPECTED_CONFORMING_CALLS,
        EXPECTED_CONFORMING_LOCKS,
    ):
        problems.append(
            f"the CONFORMING fixture holds {conforming.calls} blocking call(s) and "
            f"{conforming.locks} lock binding(s) as this gate reads it, expected "
            f"{EXPECTED_CONFORMING_CALLS} and {EXPECTED_CONFORMING_LOCKS}; an arm that sees "
            "nothing is quiet for the wrong reason"
        )

    marked = marker_count()
    if marked != EXPECTED_MARKED_FIXTURE_FILES:
        problems.append(
            f"{marked} offending fixture file(s) carry the marker, expected "
            f"{EXPECTED_MARKED_FIXTURE_FILES}; the control has been neutralised"
        )
    return problems


def production_files() -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for root in (TESTS_DIR, KIT_TESTING):
        if root.is_dir():
            files.extend(
                (path, str(path.relative_to(PACKAGE_ROOT))) for path in python_sources(root)
            )
    return files


def main() -> int:
    if not TESTS_DIR.is_dir():
        return skip(f"{TESTS_DIR} does not exist; there is no test code to read")
    problems = fixture_problems()
    if problems:
        return refuse("; ".join(problems))

    files = production_files()
    if len(files) < MIN_TEST_FILES:
        return refuse(
            f"only {len(files)} test file(s) scanned, floor is {MIN_TEST_FILES}; an empty "
            "scan reports a clean estate it never read"
        )
    reading = read_tree(files, exempt=frozenset({HELPER}))
    excused = _by_kind(reading.excused)
    if excused != {"U2": EXPECTED_HELPER_FINDINGS}:
        return refuse(
            f"{HELPER.name} is exempt by path and read as {excused}, expected "
            f"{{'U2': {EXPECTED_HELPER_FINDINGS}}}: the exemption excuses exactly the two "
            f"joins the helper checks, and nothing else: {reading.excused}"
        )
    if reading.findings:
        for item in reading.findings:
            print(f"  {item}", file=sys.stderr)
        return finding(
            f"{len(reading.findings)} wait(s) in test code with no deadline, or with one "
            "nobody checks. See foundry_testkit.timed: held, joined, arrived."
        )
    print(
        f"PASS: {reading.files} test file(s), {reading.calls} join/wait/acquire call(s) read "
        f"and none lacks a deadline, {reading.locks} lock binding(s) and none taken with a bare "
        f"`with`; offending fixture {sum(EXPECTED_OFFENDING.values())} finding(s) across "
        f"{len(EXPECTED_OFFENDING)} kinds, conforming fixture {EXPECTED_CONFORMING_CALLS} "
        f"call(s) seen and quiet, {EXPECTED_ELSEWHERE} lock known across files, helper "
        f"exemption excuses exactly {EXPECTED_HELPER_FINDINGS}; 3 fixture arms + 1 helper arm "
        "+ 1 production arm = 5 arms; 5 executed, 0 skipped, 0 unaccounted."
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
