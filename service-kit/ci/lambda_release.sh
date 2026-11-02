#!/usr/bin/env bash
# The three release facts the deploy job needs, and the rollback that uses them.
#
#   lambda_release.sh previous-image <function-name>
#       Before the deploy. Prints `image_uri=<digest uri>` for the image the function
#       runs NOW, or `image_uri=` when the function does not exist yet (a first
#       deploy). Any other AWS error is a refusal: a deploy that cannot say what it
#       would roll back to must not start.
#
#   lambda_release.sh api-url <stack-name>
#       After the deploy. Prints `api_url=<url>` from the stack's `ApiUrl` output,
#       which `deploy/iac/serverless.yml` declares. Refuses when the stack or the
#       output cannot be read, so the smoke never runs against an empty URL.
#
#   lambda_release.sh rollback <previous-image> <stage> <function-name>
#       On a red smoke. Redeploys <previous-image> through the SAME serverless path
#       the deploy step used, then reads the function back and requires it to be
#       running that digest. With no previous image it REFUSES and says so: a
#       first deploy has nothing to roll back to, and printing "rolled back" there
#       would be a claim about a rollback that never happened.
#
# Kit-owned for the same reason as `smoke.py`: `.github/workflows/ci.yml` is a kit
# placement and calls it. Everything is a script rather than inline workflow YAML so
# `tests/shape/test_lambda_release_script.py` can drive it with a stubbed `aws` and
# `npx` on PATH -- inline YAML can only be tested by running a real deploy.
#
# `Code.ResolvedImageUri`, not `Code.ImageUri`. ImageUri echoes whatever the last
# deploy passed, which is a digest only if that deploy pinned one; ResolvedImageUri is
# always the digest Lambda actually pulled. A rollback target that is a floating tag
# can silently move to different bytes, so the rollback refuses anything else.
#
# Exit codes follow the kit's vocabulary: 0 done, 1 the action ran and failed,
# 2 refused before acting.
set -uo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$KIT_DIR/.." && pwd)"
IAC_DIR="$ROOT/deploy/iac"
DIGEST_PATTERN='@sha256:[0-9a-f]{64}$'

refuse() {
    echo "lambda_release: REFUSED: $*" >&2
    exit 2
}

fail() {
    echo "lambda_release: FAILED: $*" >&2
    exit 1
}

resolved_image() {
    aws lambda get-function --function-name "$1" \
        --query 'Code.ResolvedImageUri' --output text
}

previous_image() {
    [ "$#" -eq 1 ] && [ -n "$1" ] || refuse "usage: previous-image <function-name>"
    local function_name="$1" out err code=0
    err="$(mktemp)"
    out="$(resolved_image "$function_name" 2>"$err")" || code=$?
    if [ "$code" -ne 0 ]; then
        if grep -q 'ResourceNotFoundException' "$err"; then
            rm -f "$err"
            echo "lambda_release: $function_name does not exist yet -- a first deploy, so no rollback target" >&2
            echo "image_uri="
            return 0
        fi
        local reason
        reason="$(cat "$err")"
        rm -f "$err"
        refuse "could not read $function_name's running image (exit $code): $reason"
    fi
    rm -f "$err"
    out="$(printf '%s' "$out" | tr -d '[:space:]')"
    if ! printf '%s' "$out" | grep -Eq "$DIGEST_PATTERN"; then
        refuse "$function_name exists but reports no digest image (got '$out'); nothing safe to roll back to"
    fi
    echo "lambda_release: $function_name is running $out" >&2
    echo "image_uri=$out"
}

api_url() {
    [ "$#" -eq 1 ] && [ -n "$1" ] || refuse "usage: api-url <stack-name>"
    local stack="$1" out code=0
    out="$(aws cloudformation describe-stacks --stack-name "$stack" \
        --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)" || code=$?
    [ "$code" -eq 0 ] || refuse "could not describe stack $stack (exit $code)"
    out="$(printf '%s' "$out" | tr -d '[:space:]')"
    case "$out" in
        http://* | https://*) ;;
        *) refuse "stack $stack has no usable ApiUrl output (got '$out'); the smoke has nothing to hit" ;;
    esac
    echo "api_url=$out"
}

rollback() {
    [ "$#" -eq 3 ] || refuse "usage: rollback <previous-image> <stage> <function-name>"
    local previous="$1" stage="$2" function_name="$3" now code=0
    if [ -z "$previous" ]; then
        refuse "no previous image was recorded before this deploy -- it was $function_name's first" \
            "deploy, so there is nothing to roll back to. NO ROLLBACK HAPPENED: the release" \
            "that failed its smoke is still live. Fix forward, or remove the stack by hand."
    fi
    printf '%s' "$previous" | grep -Eq "$DIGEST_PATTERN" \
        || refuse "previous image '$previous' is not a digest pin; a tag can move, so it is not a rollback target"
    [ -n "$stage" ] || refuse "no stage given"
    [ -f "$IAC_DIR/serverless.yml" ] || refuse "no serverless.yml at ${IAC_DIR#"$ROOT"/}"

    echo "lambda_release: rolling $function_name back to $previous" >&2
    (cd "$IAC_DIR" && IMAGE_URI="$previous" npx --yes serverless@3 deploy \
        --stage "$stage" --config serverless.yml) || code=$?
    [ "$code" -eq 0 ] || fail "the rollback redeploy exited $code; $function_name is in an unknown state"

    code=0
    now="$(resolved_image "$function_name")" || code=$?
    [ "$code" -eq 0 ] || fail "could not read $function_name back after the rollback (exit $code)"
    now="$(printf '%s' "$now" | tr -d '[:space:]')"
    [ "$now" = "$previous" ] \
        || fail "after the rollback $function_name runs '$now', not '$previous'"
    echo "lambda_release: ROLLED BACK $function_name to $previous (read back from Lambda)"
}

command="${1:-}"
[ "$#" -gt 0 ] && shift
case "$command" in
    previous-image) previous_image "$@" ;;
    api-url) api_url "$@" ;;
    rollback) rollback "$@" ;;
    *) refuse "unknown command '$command' (previous-image | api-url | rollback)" ;;
esac
