# The accumulator every kit verification arm goes through. Sourced, never run.
#
# It lives in its own file for one reason: it was WRONG, silently, and nothing
# could have caught it while it was inlined in `verify.sh`, because the only way
# to exercise it was to run the whole verification and read prose. Extracted, it
# is a unit `tests/shape/test_verify_arm.py` drives with stub commands that exit
# 0, 1, 2 and 3, and asserts the classification of each.
#
# WHAT WAS WRONG, because it is a shape worth recognising anywhere:
#
#     if "$@"; then
#         echo "verify: PASS $name"
#         return 0
#     fi
#     local code=$?          # <-- NOT the exit status of "$@"
#
# After `if cmd; then ...; fi` with a failing `cmd` and no `else`, `$?` is the
# status of the IF CONSTRUCT, which is 0 when no branch ran. So `code` was 0 for
# every arm that did not pass, and two things followed. `[ "$code" -eq 3 ]` was
# never true, so the exit-3 NAMED-SKIP contract -- the thing that distinguishes
# "this gate could not measure here" from "this gate passed" -- was dead in
# every service; a skip was reported as a failure. And every genuine failure
# printed `exit=0`, so the failure list read as a contradiction and gave a
# reader no code to act on.
#
# This is the masking-shape class from the estate's verification discipline, one
# layer further in: the discipline says not to read an exit code through a pipe
# or a `&&` chain, and here the code was read through a CONTROL STRUCTURE that
# owns its own status. The repair is to capture it from the command directly.
#
# `|| code=$?` is safe under `set -e`: a command on the left of `||` is not a
# fatal failure, which is exactly why the value has to be captured there rather
# than on the following line.

RAN=0
SKIPPED=0
FAILED=""

arm() {
    name="$1"
    shift
    RAN=$((RAN + 1))

    code=0
    "$@" || code=$?

    if [ "$code" -eq 0 ]; then
        echo "verify: PASS $name"
        return 0
    fi
    if [ "$code" -eq 3 ]; then
        SKIPPED=$((SKIPPED + 1))
        echo "verify: SKIP $name (exit 3 -- could not measure here, not a pass)"
        return 0
    fi
    FAILED="$FAILED $name(exit=$code)"
    echo "verify: FAIL $name (exit $code)"
    return 0
}
