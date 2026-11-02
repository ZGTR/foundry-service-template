#!/usr/bin/env bash
# The one command a HUMAN runs. Kit-owned, so every service verifies itself the
# same way.
#
# WHAT IT IS NOT: this is not what CI runs, and saying so matters. The workflow
# keeps one STEP per gate on purpose -- the Actions UI then names which gates ran
# and which never did, where a single wrapper step would report one verdict over
# an unknown scope. So there are two callers, which is exactly the shape that let
# eleven monorepo gates end up named in one list and not the other
# (`check_fence_workflow_parity.py` exists for it).
#
# What keeps them honest is that NEITHER carries a roster: this script globs
# `gates/check_*.py` and refuses a glob that matched almost nothing, and the
# workflow's own comment requires a step per gate with nothing asserting the
# number. A gate added to the kit is picked up here automatically, and its
# missing CI step is a reviewable diff rather than a silent gap.
#
# EVERY ARM RUNS. Under `set -e` the first red arm aborts the script and silences
# every arm behind it, while the verdict keeps its full scope -- so a failure
# early on would report as "verification failed" having measured a fraction of
# what the name claims. Arms accumulate into FAILED and the exit is at the end,
# and the last line says how many ran.
#
# Exit 3 from a gate is a NAMED SKIP, not a pass: it means that gate could not
# measure here (no live database, no Docker daemon). It is tolerated and COUNTED,
# and the count is printed, because a run where half the gates skipped is not the
# same evidence as a run where none did.
set -uo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$KIT_DIR/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"

# EVERY ARM IN THIS SCRIPT RUNS THROUGH $PYTHON -- the gates on line 51, both
# pytest profiles, and ruff. So a mis-specified interpreter is not a defect in
# any arm; it is the one input all of them share, and running anyway prints a
# FAIL beside each of them.
#
# That is the expensive direction. A missing INPUT is not a finding (this
# estate's own rule), and every FAIL this script prints names a real arm, so
# the first thing a reader does is go and open that arm. Measured 2026-09-18:
# an invocation with no active venv produced five FAILs -- both profiles at
# `0 passed, 0 collected` plus ruff -- and none was real. What identified it as
# an instrument rather than five defects was noticing that ruff shares nothing
# with the pytest arms EXCEPT the interpreter. Nothing in the output said so.
# This refusal says it, so the next reader does not have to make that inference.
#
# PROBED WITH `-m`, NOT `import`, because `-m` is the shape this script uses and
# the two disagree: `import json` exits 0 while `python3 -m json` exits 1. An
# import probe would clear a module the arms cannot actually run.
#
# Exit 2, a REFUSAL. Not 1, which is what a real finding exits with and calling
# this a finding is the confusion being fixed. Not 3 either: a named skip is for
# an input genuinely absent from the checkout, and here the suite is present and
# the tool to run it was mis-specified.
require_a_usable_interpreter() {
    local resolved version missing gate_n
    if ! "$PYTHON" -c 'pass' >/dev/null 2>&1; then
        echo "verify: REFUSE -- PYTHON=$PYTHON cannot execute; no arm has been attempted." >&2
        echo "verify:   Point PYTHON at a real interpreter, e.g." >&2
        echo "verify:     PYTHON=/path/to/.venv/bin/python3 bash service-kit/ci/verify.sh" >&2
        exit 2
    fi
    resolved=$("$PYTHON" -c 'import sys; print(sys.executable)')
    version=$("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')

    missing=""
    for module in pytest ruff; do
        if ! "$PYTHON" -m "$module" --version >/dev/null 2>&1; then
            missing="$missing $module"
        fi
    done
    [ -n "$missing" ] || return 0

    gate_n=0
    for gate in "$KIT_DIR"/gates/check_*.py; do
        [ -f "$gate" ] && gate_n=$((gate_n + 1))
    done

    {
        echo "verify: REFUSE -- the interpreter cannot run this suite, so no arm has been attempted."
        echo "verify:   PYTHON resolved to : $resolved"
        echo "verify:   which is           : CPython $version"
        echo "verify:   missing module(s)  :$missing"
        echo "verify:"
        echo "verify:   Every arm runs through that one interpreter -- $gate_n gate(s), 2 pytest"
        echo "verify:   profile(s) and ruff, $((gate_n + 3)) in total. This is ONE instrument failure, not"
        echo "verify:   $((gate_n + 3)) findings, and the arms are not implicated. Running anyway would print a"
        echo "verify:   FAIL naming each of them, and a named arm is the first thing a reader"
        echo "verify:   goes and opens."
        echo "verify:"
        echo "verify:   Nothing is missing from the checkout. Point PYTHON at an interpreter that"
        echo "verify:   has them, or activate that environment first:"
        echo "verify:     PYTHON=/path/to/.venv/bin/python3 bash service-kit/ci/verify.sh"
    } >&2
    exit 2
}
require_a_usable_interpreter

# The accumulator, and the RAN/SKIPPED/FAILED it maintains. Sourced rather than
# written here so that `tests/shape/test_verify_arm.py` can drive it directly
# with commands that exit 0, 1, 2 and 3 -- see arm.sh's own header for the
# defect that made a testable unit necessary. A gate's exit 3 was classified as
# a FAILURE in every service, and every real failure printed `exit=0`.
# shellcheck source=service-kit/ci/arm.sh
. "$KIT_DIR/ci/arm.sh"

# The gates are ENUMERATED by glob, never listed. A roster written here is how a
# gate number ten arrives uncovered: `new_service.sh` carried exactly that
# defect, a hardcoded six against a tree that held eight.
GATE_COUNT=0
for gate in "$KIT_DIR"/gates/check_*.py; do
    [ -f "$gate" ] || continue
    GATE_COUNT=$((GATE_COUNT + 1))
    arm "$(basename "$gate")" "$PYTHON" "$gate"
done
# Refuse a glob that matched (almost) nothing rather than reporting a clean run
# over zero gates.
if [ "$GATE_COUNT" -lt 6 ]; then
    echo "verify: REFUSE -- found only $GATE_COUNT gate(s) under $KIT_DIR/gates; expected >= 6." >&2
    exit 2
fi

# THE SUITE RUNS TWICE, ONCE PER SEED PROFILE, AND THE TWO ARE COMPARED.
#
# A real arm that silently collects FEWER tests than the fake arm is the failure
# this block exists to catch, and it is invisible in every other instrument: both
# runs are green, both print a passed count, and the difference between them is a
# number nobody computes. The comparison is the whole point -- a class that fails
# to import under `SEED_PROFILE=real`, a fixture that errors at collection, or a
# suite quietly deselected all read as "green" on their own.
#
# REQUIRE_REAL_RESOURCES=1 is set for BOTH runs, not just the real one. The real
# resources are brought up by declaration rather than by profile -- `tests/contract`
# and `tests/integration` exercise a real PostgreSQL under either profile -- so a
# fake run that could not start them has measured less than it claims too.
export REQUIRE_REAL_RESOURCES=1

# `--collect-only -q` and its EXIT CODE, never a parsed summary line. Measured in
# this repository: a `-q` run of 23 passing tests printed the dots, a warnings
# block and no "N passed" line at all, because this template's pyproject had no
# [tool.pytest.ini_options] and pytest climbed to a parent whose addopts already
# began `-q`. A flag you pass is not the flag the tool receives.
# AND IT NOW ACTUALLY READS THAT EXIT CODE. The comment above argued correctly
# for the exit code and the code below discarded it: the pytest status was piped
# into `grep -c`, the caller took stdout through `count=$(collected)` and never
# looked at `$?`, and `2>/dev/null` threw away the only other evidence.
#
# So a collection ERROR returned whatever partial count `grep -c` happened to
# see, at exit 0, with no signal. Measured on a two-good-tests tree plus one file
# with a broken import:
#
#     broken import present:  count=2, pytest exit 2, "1 error during collection"
#     broken import removed:  count=2, pytest exit 0        <- the control
#
# THE SAME NUMBER EITHER WAY. The third test never collected, and nothing
# downstream could tell: `COLLECTED_fake` and `COLLECTED_real` both come from
# the same broken collection, so they agree and the collected-counts-differ
# check passes. The reconciliation arm below is satisfied too, because the
# smaller suite reconciles against itself perfectly -- both arms derive numbers
# and neither asked whether the thing producing them succeeded.
#
# Not hypothetical: four of this estate's eight B2-B9 services error at
# collection today, all on one missing module, and each would hand this script a
# plausible number.
#
# The pytest call is NOT piped, so its status is read directly rather than
# through `PIPESTATUS`, and stderr goes to the run's private log directory
# instead of `/dev/null` -- free now that the script owns a directory, and it
# means the error text survives for the reader instead of being the one thing
# nobody can see. `grep -c` exits 1 on zero matches, which is why its status is
# deliberately not the function's: the count is captured and the return is
# explicit.
collected() {   # $1 = profile, naming this run's collection log
    local out rc n
    out=$("$PYTHON" -m pytest tests --collect-only -q -p no:cacheprovider \
        2>"$LOG_DIR/collect-$1.log")
    rc=$?
    if [ "$rc" -ne 0 ]; then
        # PYTEST REPORTS A COLLECTION ERROR ON STDOUT, NOT STDERR. Measured
        # 2026-09-18 against a tree with one unimportable file: exit 2, 1157
        # bytes of traceback and `1 test collected, 1 error` on stdout, and
        # stderr EMPTY. The refusal below names this log, so redirecting only
        # stderr would hand the reader a zero-byte file and an instruction to
        # go read it -- a remedy nobody can perform.
        printf '%s\n' "$out" >>"$LOG_DIR/collect-$1.log"
        return 1
    fi
    n=$(printf '%s\n' "$out" | grep -cE '::')
    printf '%s' "$n"
    return 0
}

# A SKIP IS INVISIBLE TO EVERY OTHER NUMBER THIS SCRIPT PRINTS, and the floor
# below is the only thing that can see one.
#
# The collected/passed comparison is structurally blind to it: a skipped test is
# COLLECTED in both profiles and PASSES in neither, so both sides stay equal
# however many skip. Measured on this branch -- four SQS arms and one Redis arm
# skipped under `REQUIRE_REAL_RESOURCES=1`, and every figure here was identical
# to a run where they had all executed. The `$SKIPPED` in the final line does not
# help either: it counts GATE arms that exited 3, and has never had anything to
# do with pytest.
#
# HAND-WRITTEN LITERALS, one per profile. Deriving the expectation from the run
# would make the assertion `n == n` -- true for every possible run, including one
# where the entire suite skipped. Zero is the right value because this script
# exports `REQUIRE_REAL_RESOURCES=1`, under which an unavailable real resource is
# an ERROR rather than a skip; a genuine environment-conditional skip should be
# declared here, with a reason, rather than absorbed.
EXPECTED_SKIPS_fake=0
EXPECTED_SKIPS_real=0

# THE LOG PATH IS PER RUN, AND IT USED TO BE `/tmp/verify-$profile.log`.
#
# That path is scoped to neither the checkout nor the run, and this script both
# WRITES it and parses three values back out of it. Two concurrent `verify.sh`
# invocations therefore share one file and one truncates it while the other is
# grepping. Measured 2026-09-18 with `lsof -d cwd`: PID 1627 in
# `tpl-realprofile` and PID 18159 in `tpl-pydantic-fix`, both running this
# script, both writing `/tmp/verify-fake.log`. My run reported
# `FAIL pytest[fake] -- exit 0 with 0 passed` while the file on disk read
# `381 passed in 169.10s`.
#
# The LOUD direction is the lucky one. The quiet direction is a run that parses
# the OTHER checkout's completed log and reports a plausible `N passed` for a
# suite it never ran, at exit 0, with no error -- a real number from the wrong
# tree, and nothing here could tell. `-p no:randomly`-style determinism does not
# help, and neither does any exit code.
#
# `compose_project_name` in `foundry_testkit.real_profile` already solves this
# for containers by hashing the checkout's realpath; this file left its own
# artifact unscoped two hundred lines from that docstring. `mktemp -d` is
# stronger than hashing the checkout, because it also separates two runs in the
# SAME checkout, which a per-checkout hash would not.
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/foundry-verify.XXXXXX")" || {
    echo "verify: REFUSE -- could not create a private log directory." >&2
    exit 2
}
# Not cleaned up on exit: these logs are the only artifact a CI reader or a
# developer has after a red arm, and every failure message below names the path.
echo "verify: logs in $LOG_DIR"

# Sentinels, because a profile whose COLLECTION failed `continue`s without
# setting these and `set -u` would then kill the comparison below -- turning a
# named arm failure into an unbound-variable abort that reports nothing. This
# is the same shape as the `accounted` bug in this file's history: a value
# assigned on only one path through the loop.
COLLECTED_fake=""
COLLECTED_real=""
PROFILE_SUMMARY=""
for profile in fake real; do
    export SEED_PROFILE="$profile"

    # REFUSE a partial count rather than comparing one. Every number this loop
    # computes is only as good as the collection that produced it, so a failed
    # collection ends this profile here instead of feeding a plausible figure to
    # three arms that cannot see the difference. The other profile still runs --
    # under `set -e` this would have aborted the script and reported a verdict
    # over half the scope, which is the defect the header's EVERY ARM RUNS
    # paragraph exists for.
    if ! count=$(collected "$profile"); then
        RAN=$((RAN + 1))
        FAILED="$FAILED pytest[$profile](collection-failed)"
        echo "verify: FAIL pytest[$profile] -- collection did not succeed, so there is no" \
             "trustworthy count to compare. A partial count here reads as a smaller suite" \
             "and reconciles against itself perfectly. Collection stderr:" \
             "$LOG_DIR/collect-$profile.log"
        sed -n '$p' "$LOG_DIR/collect-$profile.log" 2>/dev/null | sed 's/^/verify:   /'
        PROFILE_SUMMARY="$PROFILE_SUMMARY $profile=COLLECTION-FAILED"
        continue
    fi
    eval "COLLECTED_$profile=\$count"

    # `-rs` so the log carries the REASON for every skip. A count tells you the
    # floor was breached; only the reason tells you by what, and this log is the
    # only artifact a CI reader gets.
    "$PYTHON" -m pytest tests --no-header -rs -p no:cacheprovider > "$LOG_DIR/verify-$profile.log" 2>&1
    code=$?
    # The PASSED count from the run's own output. Read here and asserted below
    # rather than trusted: a run that collected nothing exits 0 with "no tests
    # ran", which is the shape the whole comparison exists to refuse.
    passed=$(grep -oE '[0-9]+ passed' "$LOG_DIR/verify-$profile.log" | tail -1 | grep -oE '^[0-9]+')
    passed="${passed:-0}"
    eval "PASSED_$profile=\$passed"

    # THE POSITIVE CONTROL FOR THE TWO PARSES ABOVE AND THE ONE BELOW. pytest
    # prints "N skipped" only when N is non-zero, so an absent match and a real
    # zero are the same bytes -- and a parse that silently stopped working would
    # report 0 skipped, match the expected 0, and pass. Requiring the summary
    # LINE to exist is what distinguishes "nothing skipped" from "nothing read".
    summary=$(grep -E '(passed|failed|error|skipped).* in [0-9.]+s' "$LOG_DIR/verify-$profile.log" | tail -1)
    skipped=$(grep -oE '[0-9]+ skipped' "$LOG_DIR/verify-$profile.log" | tail -1 | grep -oE '^[0-9]+')
    skipped="${skipped:-0}"
    eval "expected_skips=\$EXPECTED_SKIPS_$profile"

    outcome() {
        printf '%s' "$summary" | grep -oE "[0-9]+ $1" | tail -1 | grep -oE '^[0-9]+'
    }
    failed_n=$(outcome 'failed'); failed_n="${failed_n:-0}"
    errors_n=$(outcome 'errors?'); errors_n="${errors_n:-0}"
    xfailed_n=$(outcome 'xfailed'); xfailed_n="${xfailed_n:-0}"
    xpassed_n=$(outcome 'xpassed'); xpassed_n="${xpassed_n:-0}"
    accounted=$((passed + failed_n + errors_n + skipped + xfailed_n + xpassed_n))

    RAN=$((RAN + 1))
    if [ -z "$summary" ]; then
        FAILED="$FAILED pytest[$profile](no-summary-line)"
        echo "verify: FAIL pytest[$profile] -- no pytest summary line in the log, so the" \
             "passed and skipped counts were read from nothing and cannot be trusted." \
             "Log: $LOG_DIR/verify-$profile.log"
    elif [ "$skipped" -ne "$expected_skips" ]; then
        FAILED="$FAILED pytest[$profile](skipped=$skipped,declared=$expected_skips)"
        echo "verify: FAIL pytest[$profile] -- $skipped skipped, $expected_skips declared." \
             "A skip is collected in both profiles and passes in neither, so nothing else" \
             "here can see it. Reasons:"
        grep -E '^SKIPPED' "$LOG_DIR/verify-$profile.log" | sed 's/^/verify:   /'
    fi
    if [ "$code" -ne 0 ]; then
        FAILED="$FAILED pytest[$profile](exit=$code)"
        echo "verify: FAIL pytest[$profile] (exit $code, $passed passed, $count collected)." \
             "Log: $LOG_DIR/verify-$profile.log"
    elif [ "$passed" -lt 1 ]; then
        FAILED="$FAILED pytest[$profile](0-passed)"
        echo "verify: FAIL pytest[$profile] -- exit 0 with $passed passed. A run that" \
             "collected nothing exits 0 and reads as success." \
             "Log: $LOG_DIR/verify-$profile.log"
    # EVERY OUTCOME STATE NEEDS A TERM, OR THE SUM FALSE-REDS ON A GREEN TREE.
    #
    # Parsed from `$summary` -- the single summary LINE -- rather than from the
    # whole log, because a traceback or a captured subprocess can print
    # "1 failed" in its own output and `tail -1` over the file would pick the
    # wrong occurrence.
    #
    # `errors?` because pytest writes "1 error" and "2 errors". `[0-9]+ passed`
    # cannot match inside "1 xpassed": after the digit and space comes `x`, not
    # `p`. Each parse defaults to 0, and an absent match is indistinguishable
    # from a real zero -- pytest omits `0 xfailed` entirely -- which is exactly
    # why the arm below is gated on `$summary` being non-empty. Without that
    # gate, six values read from a log nobody proved was read would be compared
    # to a real collection, which is the shape the shared-log collision just
    # taught this file.

    # THE TWO NUMBERS WERE PRINTED SIDE BY SIDE AND NEVER COMPARED.
    #
    # `$passed` comes from the LOG and `$count` from a private pipe, so they have
    # different provenance -- which is exactly why comparing them catches a count
    # that arrived from somewhere else. Measured 2026-09-18, the other half of
    # the shared-log defect this file now scopes: a concurrent run in a second
    # worktree reported `381 passed, 379 collected`. A passed count LARGER than
    # the collection it is compared against is impossible for one run, it was
    # printed in that shape, and nothing here said so. The run exited 0.
    #
    # THIS IS NOT REDUNDANT WITH THE `mktemp` SCOPING. Scoping fixes the one
    # mechanism by which a foreign count could arrive; this asserts the property
    # that makes any such count detectable, whatever the next mechanism turns out
    # to be. A donated number is otherwise well-formed -- right shape, plausible
    # magnitude, truthful exit code -- and this is the only instrument here that
    # separates it from a real one.
    #
    # EQUALITY, NOT `<=`, AND EVERY STATE IN THE SUM. Both halves were measured
    # rather than argued.
    #
    # `passed + skipped` alone false-reds on an ordinary `@pytest.mark.xfail`:
    # six collected against 3 passed + 1 skipped + 1 xfailed + 1 xpassed gives 4,
    # on a tree where nothing is wrong. This file is kit-owned and byte-identical
    # in every emitted service, so that red would arrive from a file the service
    # author did not write -- the false-red direction, where the finding names a
    # real number and invites fixing the test rather than doubting the gate.
    #
    # `deselected` is DELIBERATELY NOT a term, and including it is the mistake
    # this comment exists to stop. `collected()` runs the same `--collect-only`
    # with the same flags, so its count ALREADY excludes deselected tests: under
    # `-m "not slow"` the measured cells are 5 collected against
    # 2 passed + 1 skipped + 1 xfailed + 1 xpassed = 5, and adding the 1
    # deselected gives 6 and a false red. The deselection shrinks both sides.
    #
    # `<=` would never false-red and is still wrong, because a donated count is
    # not bounded in one direction. Today's contamination arrived HIGH (381
    # against 379 collected) and the same collision against a smaller peer tree
    # -- 344, 339 and 366 were all live in this estate the same day -- arrives
    # LOW and sails through any inequality. That catches the instance and not
    # the class.
    #
    # `failed` and `error` are in the sum although the exit-code arm above
    # already covers them, because leaving them out unbalances the arithmetic on
    # exactly the runs where you most want it to reconcile.
    elif [ -n "$summary" ] && [ "$accounted" -ne "$count" ]; then
        FAILED="$FAILED pytest[$profile](accounted=$accounted,collected=$count)"
        echo "verify: FAIL pytest[$profile] -- the outcomes do not account for the collection:" \
             "$passed passed + $failed_n failed + $errors_n error + $skipped skipped +" \
             "$xfailed_n xfailed + $xpassed_n xpassed = $accounted, against $count collected." \
             "These numbers have different sources -- the outcomes from the log, the" \
             "collection from a private pipe -- so a mismatch means one of them describes a" \
             "different run: a concurrent verify, a stale log, or a truncated read." \
             "Log: $LOG_DIR/verify-$profile.log"
    else
        echo "verify: PASS pytest[$profile] ($passed passed, $count collected)"
    fi
    PROFILE_SUMMARY="$PROFILE_SUMMARY $profile=$passed/$count(skip=$skipped)"
done
unset SEED_PROFILE

if [ -z "$COLLECTED_fake" ] || [ -z "$COLLECTED_real" ]; then
    # Already reported as a collection failure above. Saying so here too keeps
    # the comparison from claiming a scope it did not measure.
    echo "verify: SKIP collected-counts comparison -- at least one profile had no" \
         "usable collection."
elif [ "$COLLECTED_fake" -ne "$COLLECTED_real" ]; then
    FAILED="$FAILED collected-counts-differ"
    echo "verify: FAIL collected counts differ -- fake=$COLLECTED_fake real=$COLLECTED_real." \
         "The real arm is running a DIFFERENT suite from the fake arm, and both are green."
fi

arm "ruff" "$PYTHON" -m ruff check .

if [ -n "$FAILED" ]; then
    echo "verify: profiles$PROFILE_SUMMARY"
    echo "verify: FAILED -->$FAILED"
    echo "verify: $RAN arm(s) ran, $SKIPPED skipped, $GATE_COUNT gate(s) discovered by glob."
    exit 1
fi
echo "verify: profiles$PROFILE_SUMMARY"
echo "verify: PASS -- $RAN arm(s) ran, $SKIPPED skipped, $GATE_COUNT gate(s) discovered by glob."
