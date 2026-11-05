"""`service-kit/ci/arm.sh` classifies an arm by the exit code it actually got.

This file exists because the accumulator was WRONG in a way no test could have
seen. It read the arm's exit code as `$?` on the line after an `if "$@"; then
... fi`, where `$?` belongs to the IF CONSTRUCT and is 0 when no branch ran. So
every non-passing arm was recorded as `exit=0`, which meant:

* a gate's exit 3 -- the NAMED SKIP that says "this gate could not measure
  here", the whole reason `verify.sh` can run where there is no Docker daemon
  and no live database -- was classified as a FAILURE, in every service; and
* every genuine failure printed `exit=0`, a self-contradicting line that gives a
  reader no code to act on.

It was found by reading the output of a run that had a legitimately red arm and
noticing that `verify: FAILED --> check_kit_drift.py(exit=0)` cannot be true:
exit 0 is what passing means. A green run never shows it, which is why it
survived the kit extraction and A1's own verification -- that proof recorded
"11 arms green, 0 skipped", and the 0 was not a measurement.

The four cases below are the classification's whole domain. 2 is included
separately from 1 because gates in this estate use it for "could not measure",
and a classifier that lumped it with 3 would turn a refusal into a skip.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ARM_SH = Path(__file__).parent.parent.parent / "service-kit/ci/arm.sh"

#: (exit code, the word the output must carry, the word it must NOT carry).
#: HAND-WRITTEN, and the negative half is what makes each row bite: without it
#: a classifier that printed all three words for every code would pass.
CASES = (
    (0, "PASS", "FAIL"),
    (1, "FAIL", "SKIP"),
    (2, "FAIL", "SKIP"),
    (3, "SKIP", "FAIL"),
)


def _drive(code: int) -> subprocess.CompletedProcess:
    """Source arm.sh and run one arm whose command exits `code`."""
    script = f"""
    set -uo pipefail
    . '{ARM_SH}'
    arm "probe" sh -c 'exit {code}'
    echo "RAN=$RAN SKIPPED=$SKIPPED FAILED=[$FAILED]"
    """
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, timeout=30
    )


def test_arm_sh_exists_and_parses() -> None:
    """The positive control for every arm below.

    Without it a renamed or deleted `arm.sh` would make `_drive` fail for a
    reason unrelated to classification, and four tests would go red pointing at
    the wrong thing.
    """
    assert ARM_SH.is_file(), f"{ARM_SH} is missing; every case below is measuring nothing"
    parsed = subprocess.run(
        ["/bin/bash", "-n", str(ARM_SH)], capture_output=True, text=True, timeout=30
    )
    assert parsed.returncode == 0, parsed.stderr


@pytest.mark.parametrize(("code", "expected", "forbidden"), CASES)
def test_an_arm_is_classified_by_its_real_exit_code(
    code: int, expected: str, forbidden: str
) -> None:
    result = _drive(code)

    assert result.returncode == 0, f"arm() must not abort the caller:\n{result.stderr}"
    assert f"verify: {expected} probe" in result.stdout, result.stdout
    assert f"verify: {forbidden} probe" not in result.stdout, result.stdout


def test_a_failing_arm_records_the_code_it_actually_got() -> None:
    """The founding defect, stated as an assertion.

    `exit=0` in a FAILED list is the signature: it is what the old accumulator
    printed for every red arm, and it is indistinguishable from a passing arm's
    code. Asserting the real code is present AND that `exit=0` is absent is the
    pair -- the first alone would pass a line reading `probe(exit=0) exit=2`.
    """
    result = _drive(2)

    assert "probe(exit=2)" in result.stdout, result.stdout
    assert "exit=0" not in result.stdout, result.stdout


def test_the_counters_separate_a_skip_from_a_pass_and_from_a_failure() -> None:
    """A skip is COUNTED, never folded into either verdict.

    `verify.sh`'s closing line reports the skip count because a run where half
    the gates could not measure is not the same evidence as a run where none
    skipped. That sentence is only true if the counter moves.
    """
    passing = _drive(0)
    skipping = _drive(3)
    failing = _drive(1)

    assert "RAN=1 SKIPPED=0 FAILED=[]" in passing.stdout, passing.stdout
    assert "RAN=1 SKIPPED=1 FAILED=[]" in skipping.stdout, skipping.stdout
    assert "RAN=1 SKIPPED=0 FAILED=[ probe(exit=1)]" in failing.stdout, failing.stdout


# ---------------------------------------------------------------------------
# The verify log path must be private to the run
# ---------------------------------------------------------------------------
#
# WHAT WENT WRONG. `verify.sh` wrote `/tmp/verify-$profile.log` and parsed four
# values back out of it. That path is scoped to neither the checkout nor the
# run, so two concurrent `verify.sh` invocations shared one file. Measured
# 2026-09-18 with `lsof -d cwd`: two runs in two worktrees of this template,
# both writing `/tmp/verify-fake.log`. One reported
# `profiles fake=0/381(skip=0) real=0/381(skip=0)` -- 381 tests COLLECTED and 0
# parsed as passed, on a suite whose log on disk read `381 passed in 169.10s`.
#
# WHICH HALF BROKE IS THE INSTRUCTIVE PART. `collected()` pipes pytest straight
# into `grep -c` and never touches the file, so it reported 381 correctly. Only
# the parses that read the shared file were wrong, and they disagreed with each
# other: the summary-line control found a summary (so it did not fire) while the
# passed-count parse found nothing, because they read the same path at two
# instants and the file had different contents at each. The skip floor then read
# "0 skipped" from a log it had not really read and MATCHED its expected 0 --
# the quiet direction of the same defect, passing a floor against a file
# belonging to another checkout.
#
# A grep-shaped test rather than a behavioural one, deliberately: reproducing
# the collision needs two concurrent full suite runs, which is minutes of
# wall time and inherently racy. What can be asserted cheaply and
# deterministically is that the script does not name a fixed shared path.


VERIFY_SH = Path(__file__).parent.parent.parent / "service-kit/ci/verify.sh"


def _verify_source() -> str:
    source = VERIFY_SH.read_text()
    # The positive control for the read itself: an empty or missing file would
    # make every assertion below pass by vacuity.
    assert "SEED_PROFILE" in source, f"{VERIFY_SH} does not look like verify.sh"
    return source


def _executable_lines(source: str) -> list[str]:
    """Lines with the leading comment stripped, so prose cannot satisfy the test.

    The defect is DOCUMENTED in this file's own header, quoting the old path, so
    a naive substring search over the whole source matches the explanation and
    reports the defect as present forever -- a false red naming a real file.
    """
    return [line for line in source.splitlines() if not line.lstrip().startswith("#")]


def test_verify_does_not_write_a_shared_fixed_log_path() -> None:
    executable = "\n".join(_executable_lines(_verify_source()))

    assert "/tmp/verify-" not in executable, (
        "verify.sh names a fixed /tmp log path in executable code. Two concurrent runs "
        "then share one file and each parses what the other truncated -- loudly as a "
        "0-passed run, or silently as a plausible count from another checkout's suite."
    )


def test_verify_creates_a_private_log_directory() -> None:
    """The positive half: absence of the bad path is not presence of the fix.

    A `verify.sh` that simply stopped writing a log at all would satisfy the
    assertion above, so the private directory has to be asserted directly.
    """
    executable = "\n".join(_executable_lines(_verify_source()))

    assert "mktemp -d" in executable, (
        "verify.sh does not create a private log directory with `mktemp -d`; the "
        "per-profile logs need a location that cannot collide with another run."
    )
    assert "$LOG_DIR/verify-$profile.log" in executable, (
        "verify.sh does not write its per-profile logs under $LOG_DIR"
    )


def test_the_comment_stripper_does_not_hide_a_real_finding() -> None:
    """The control for `_executable_lines`, which is where this test could go blind.

    If the stripper were too greedy -- dropping every line containing a `#`, say
    -- it would remove executable lines with trailing comments and the two tests
    above would pass over a script that had reintroduced the shared path. So it
    is driven both ways on input whose answer is known.
    """
    hidden = "# this mentions /tmp/verify-fake.log in prose only"
    real = 'echo "x" > "/tmp/verify-$profile.log"  # with a trailing comment'

    stripped = "\n".join(_executable_lines(f"{hidden}\n{real}\n"))

    assert "/tmp/verify-" in stripped, "the stripper removed a REAL occurrence"
    assert "prose only" not in stripped, "the stripper kept a comment-only line"


def test_verify_reconciles_passed_and_skipped_against_collected() -> None:
    """The counts have different sources, so comparing them catches a foreign one.

    `$passed` and `$skipped` are parsed out of the pytest LOG; `$count` comes
    from a private pipe in `collected()`. Measured 2026-09-18: a concurrent run
    in a second worktree made this script print `381 passed, 379 collected` and
    exit 0, because the two numbers were printed side by side on the same line
    and never compared. A passed count larger than the collection it is shown
    against is impossible for one run.

    Asserted as a SHAPE because the behavioural proof needs a full two-profile
    suite run with a deliberately wrong `collected()`, which is minutes of wall
    clock -- that proof was taken by hand against the real script and is
    recorded in the commit; this keeps the branch from being deleted later.
    """
    executable = "\n".join(_executable_lines(_verify_source()))

    assert '"$accounted" -ne "$count"' in executable, (
        "verify.sh does not reconcile its outcome counts against the collected count. "
        "Those numbers have different provenance, and their disagreement is the only "
        "signal here that a count came from a different run."
    )
    for term in ("failed_n", "errors_n", "xfailed_n", "xpassed_n"):
        assert term in executable, (
            f"verify.sh does not parse {term}; a state missing from the sum makes the "
            f"arm false-red on a green tree that happens to use it."
        )
    assert "deselected" not in executable.split("accounted=$((")[1].split(")")[0], (
        "deselected is in the sum. collected() applies the same -m filter, so its count "
        "already excludes deselected tests and adding the term double-counts the "
        "exclusion -- measured at 6 against 5 collected."
    )


def _collected_function_source() -> str:
    """The real `collected()` text, lifted out of verify.sh so it can be driven.

    Extracted rather than reimplemented: a copy of the function in this file
    would keep passing after verify.sh's own copy regressed, which is the whole
    failure mode being guarded. If the function is renamed or deleted the
    extraction comes back empty and the caller below refuses rather than
    reporting a pass over nothing.
    """
    source = _verify_source()
    start = source.index("collected() {")
    end = source.index("\n}\n", start) + len("\n}\n")
    return source[start:end]


def _drive_collected(tree: Path) -> subprocess.CompletedProcess:
    """Run the real `collected()` against `tree`, which must hold a `tests/` dir."""
    body = _collected_function_source()
    assert "--collect-only" in body, (
        "the extracted text is not collected(); verify.sh's shape has moved and this "
        "test is measuring nothing"
    )
    script = f"""
    set -uo pipefail
    PYTHON='{sys.executable}'
    LOG_DIR='{tree}/logs'
    mkdir -p "$LOG_DIR"
    {body}
    out=$(collected probe)
    rc=$?
    echo "RC=$rc"
    echo "OUT=[$out]"
    """
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tree,
    )


def _tree_with(tmp_path: Path, extra: str = "") -> Path:
    """Two collectable tests, plus whatever `extra` adds."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text(
        "def test_one() -> None:\n    assert True\n\n"
        "def test_two() -> None:\n    assert True\n"
    )
    if extra:
        (tests / "test_extra.py").write_text(extra)
    return tmp_path


def test_collected_returns_a_count_when_collection_succeeds(tmp_path: Path) -> None:
    """The green control. Without it the red arm below cannot be read.

    An arm that only exercises the failing direction cannot tell a working
    refusal from a function that refuses unconditionally -- measured in this
    estate more than once, which is why the pair is written together.
    """
    result = _drive_collected(_tree_with(tmp_path))

    assert "RC=0" in result.stdout, result.stdout + result.stderr
    assert "OUT=[2]" in result.stdout, (
        f"a healthy two-test tree did not yield 2: {result.stdout}{result.stderr}"
    )


def test_collected_refuses_rather_than_returning_a_partial_count(
    tmp_path: Path,
) -> None:
    """A collection ERROR still prints the tests it managed to collect.

    Measured 2026-09-18 on this template: two good tests beside one file whose
    import fails gives `2 tests collected, 1 error`, pytest exit 2, and a `::`
    count of exactly 2 -- THE SAME NUMBER as the control tree with the broken
    file removed. Nothing downstream can tell the two apart, so a `collected()`
    that discards the exit code hands the reconciliation arm a smaller suite
    that balances against itself perfectly.

    The count is not merely wrong here; it is plausible, which is worse. The
    refusal is the only signal available.
    """
    tree = _tree_with(tmp_path, "import a_module_that_does_not_exist  # noqa: F401\n")

    result = _drive_collected(tree)

    assert "RC=1" in result.stdout, (
        f"collected() did not refuse a failed collection: {result.stdout}{result.stderr}"
    )
    assert "OUT=[]" in result.stdout, (
        f"collected() returned a count from a failed collection: {result.stdout}"
    )


def test_the_refusal_log_holds_the_reason(tmp_path: Path) -> None:
    """Its own arm, because it is its own clause.

    Written as one test with the refusal above, both clauses reddened the same
    test name and a mutation table could not say which had broken -- the
    one-fixture-per-CLAUSE rule this repository already pays for, reproduced
    from scratch. Measured 2026-09-18: pytest puts a collection error on STDOUT
    and leaves stderr EMPTY (1157 bytes against 0), so a function redirecting
    only stderr refuses correctly and names a zero-byte file while doing it.
    """
    tree = _tree_with(tmp_path, "import a_module_that_does_not_exist  # noqa: F401\n")

    _drive_collected(tree)

    log = tree / "logs/collect-probe.log"
    assert log.is_file(), "the refusal names a log path that was never created"
    body = log.read_text()
    assert "error" in body.lower(), (
        f"the log the refusal names does not hold the reason ({log.stat().st_size} "
        f"bytes). A remedy pointing at an empty file is a remedy nobody can perform."
    )


def test_the_caller_acts_on_the_refusal(tmp_path: Path) -> None:
    """A function that returns 1 into a caller that ignores it has changed nothing.

    This file's own tests above drive `collected()` in isolation and are
    structurally blind to whether verify.sh still consults its exit status --
    the same shape as `a-unit-test-cannot-see-its-own-caller` in the knowledge
    graph. So the caller is asserted separately, and by SHAPE, because reaching
    it behaviourally costs a full two-profile suite run.
    """
    executable = "\n".join(_executable_lines(_verify_source()))

    assert 'if ! count=$(collected "$profile"); then' in executable, (
        "verify.sh calls collected() without testing its exit status, so a refusal "
        "would be discarded and the old partial-count behaviour restored"
    )
    assert "collection-failed" in executable, (
        "the caller does not record a failed collection as a distinct outcome; "
        "silently continuing is what made this invisible"
    )


def _interpreter_probe_source() -> str:
    """The real `require_a_usable_interpreter()`, lifted out of verify.sh.

    Extracted rather than reimplemented, for the reason `_collected_function_source`
    gives: a copy here would keep passing after verify.sh's own copy regressed.
    """
    source = _verify_source()
    start = source.index("require_a_usable_interpreter() {")
    end = source.index("\n}\n", start) + len("\n}\n")
    body = source[start:end]
    assert "missing module(s)" in body, (
        "the extracted text is not the interpreter probe; verify.sh's shape has moved "
        "and this test is measuring nothing"
    )
    return body


def _fake_python(tmp_path: Path, *, absent: tuple[str, ...]) -> Path:
    """An interpreter that runs `-c` for real but claims `absent` modules are missing.

    A deterministic fixture, because `python3` resolves to a different build on
    every machine -- measured 2026-09-18: on one developer's PATH it was
    Homebrew 3.14 with NEITHER pytest nor ruff, on another Xcode's 3.9.6 with
    pytest but not ruff. A test keyed on whichever one is installed would be
    measuring the machine rather than the probe.
    """
    shim = tmp_path / "fake_python"
    missing = " ".join(absent)
    shim.write_text(
        "#!/bin/bash\n"
        f'for m in {missing or "__none__"}; do\n'
        '    if [ "$1" = "-m" ] && [ "$2" = "$m" ]; then exit 1; fi\n'
        "done\n"
        f'exec "{sys.executable}" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _drive_probe(python: str, kit_dir: Path) -> subprocess.CompletedProcess:
    script = f"""
    set -uo pipefail
    PYTHON='{python}'
    KIT_DIR='{kit_dir}'
    {_interpreter_probe_source()}
    require_a_usable_interpreter
    echo "PROBE_RETURNED_0"
    """
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, timeout=60
    )


KIT_DIR = Path(__file__).parent.parent.parent / "service-kit"


def test_the_probe_passes_an_interpreter_that_can_run_the_suite(
    tmp_path: Path,
) -> None:
    """The green control. Without it a probe that refused unconditionally would pass.

    Driven through the same shim as the red arms, with nothing absent, so the
    two directions differ in exactly one variable.
    """
    shim = _fake_python(tmp_path, absent=())

    result = _drive_probe(str(shim), KIT_DIR)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE_RETURNED_0" in result.stdout, (
        f"the probe refused an interpreter that has both modules: {result.stderr}"
    )
    assert "REFUSE" not in result.stderr, result.stderr


@pytest.mark.parametrize(
    "absent",
    [("ruff",), ("pytest",), ("pytest", "ruff")],
    ids=["ruff-only", "pytest-only", "both"],
)
def test_the_probe_refuses_at_exit_2_and_names_what_is_missing(
    tmp_path: Path, absent: tuple[str, ...]
) -> None:
    """Exit 2 is the claim, and every absent module is named in ONE message.

    Three cells because the two observed failures differed: one machine had
    neither module, another had pytest but not ruff. A probe checking only the
    module it was written for would clear the second.
    """
    shim = _fake_python(tmp_path, absent=absent)

    result = _drive_probe(str(shim), KIT_DIR)

    assert result.returncode == 2, (
        f"a refusal must exit 2 -- 1 is what a real finding exits with, and calling "
        f"this a finding is the confusion being fixed. Got {result.returncode}."
    )
    assert result.stderr.count("REFUSE") == 1, (
        f"the refusal is not one message: {result.stderr}"
    )
    for module in absent:
        assert module in result.stderr, f"{module} is missing but unnamed: {result.stderr}"


def test_the_refusal_says_the_arms_are_not_implicated(tmp_path: Path) -> None:
    """The inference the last reader had to make by hand, made mechanical.

    What identified the observed failure as an instrument rather than five
    defects was noticing that ruff shares nothing with the pytest arms EXCEPT
    the interpreter. Nothing in the output said so. A refusal that costs the
    next reader that inference has not finished its job, so the message states
    the shared input and counts the arms it would otherwise have accused.
    """
    shim = _fake_python(tmp_path, absent=("ruff",))

    result = _drive_probe(str(shim), KIT_DIR)

    assert "Every arm runs through that one interpreter" in result.stderr, (
        "the refusal does not say the arms share the interpreter, so a reader still "
        "has to derive it"
    )
    assert "not implicated" in result.stderr, result.stderr
    assert "Nothing is missing from the checkout" in result.stderr, (
        "the refusal does not distinguish itself from a named skip, which is what an "
        "input genuinely absent from the checkout would be"
    )


def test_no_arm_is_attempted_before_the_probe() -> None:
    """The probe is CALLED before the first arm, not merely defined before it.

    A function defined at the top and invoked after the gate loop would satisfy
    every assertion above and still print thirteen FAILs first.
    """
    source = _verify_source()

    # ANCHORED ON A TOKEN THE PROBE ITSELF DOES NOT CONTAIN. The first version
    # searched for the gate GLOB, and the probe counts gates with that same glob,
    # so `index` found the probe's own copy and the assertion compared the probe
    # against itself. `GATE_COUNT` appears only in the real loop -- asserted here
    # rather than assumed, because that is precisely what went wrong.
    assert source.count("GATE_COUNT=$((GATE_COUNT + 1))") == 1, (
        "the anchor is no longer unique, so this test may be measuring the probe's "
        "own gate count instead of the real loop"
    )
    definition = source.index("require_a_usable_interpreter() {")
    call = source.index("\nrequire_a_usable_interpreter\n")
    gate_loop = source.index("GATE_COUNT=$((GATE_COUNT + 1))")
    first_arm = source.index('. "$KIT_DIR/ci/arm.sh"')

    assert definition < call < first_arm < gate_loop, (
        "the probe does not run before the arm machinery is even sourced; a refusal "
        "that arrives after thirteen FAILs has not prevented anything"
    )
