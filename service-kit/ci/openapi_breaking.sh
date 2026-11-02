#!/usr/bin/env bash
# Refuse a breaking change to the committed OpenAPI spec inside a major version.
#
#   openapi_breaking.sh install <bin-dir>
#       Downloads oasdiff 1.33.0's release tarball for this OS/arch, verifies it
#       against the sha256 pinned below (copied from the release's checksums.txt),
#       and extracts the binary into <bin-dir>. A checksum mismatch, an unknown
#       platform or a failed download is a refusal: an unverified binary never runs.
#
#   openapi_breaking.sh compare [<base-rev>]
#       Runs `oasdiff breaking <base spec> <this tree's spec> --fail-on ERR`.
#       With no argument the base is resolved the way CI sees it: BASE_REF (a pull
#       request's target branch) first, else BEFORE_SHA (the branch tip before a
#       push), fetched at depth 1 because actions/checkout fetches only the head.
#
# THE POLICY oasdiff ENFORCES, and why nothing here re-implements it: paths carry
# the major version (`/v1/...`) and only additive change lands inside a major. A
# break ships as a new major path beside the old; the old operation is marked
# `deprecated: true` with an `x-sunset` date and may be removed only after that
# date. oasdiff's `api-path-removed-without-deprecation` and
# `api-removed-before-sunset` rules are exactly that, so removing a deprecated
# operation whose sunset has passed is not reported, and removing anything else is.
#
# WHERE THE SPEC IS. Two paths, and the script looks for both: the kit's own
# `contracts/openapi/v1.json` (what `scripts/export_openapi.py` writes) and
# `openapi/v1.json`, the estate directory's default, where a service kept its spec
# before it adopted the kit. Reading the kit's path alone would give such a service a
# named skip on every run once it synced this kit, and the commit that moves its spec
# to the kit's path would be a skip too, so a break made with the move would not be
# judged. No service had synced this step when that was written
# (measured 2026-10-04). The base and this tree are resolved separately, so a change
# that MOVES the spec is still compared across the move. Both paths present on one
# side is a refusal: one service publishes one contract, and choosing for the caller
# would judge a file nobody may be reading.
#
# A base with no committed spec is a NAMED SKIP (exit 3), never a pass and never a
# failure: the first change that adds the spec has nothing to be compared against.
# The spec itself comes from `scripts/export_openapi.py`, and the suite's
# `tests/shape/test_openapi_contract.py` keeps it equal to what the app serves --
# without that, this step would judge a document nobody serves.
#
# Kit-owned, like smoke.py and lambda_release.sh: `.github/workflows/ci.yml` is a
# kit placement and calls it, and a script can be driven by a test where inline
# workflow YAML cannot (`tests/shape/test_openapi_breaking_script.py`).
#
# Exit codes, the kit's vocabulary: 0 no breaking change, 1 breaking change (or
# oasdiff failed), 2 refused before comparing, 3 named skip.
set -uo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$KIT_DIR/.." && pwd)"
SPEC_PATHS=("contracts/openapi/v1.json" "openapi/v1.json")
OASDIFF_VERSION="1.33.0"
ZERO_SHA="0000000000000000000000000000000000000000"

refuse() {
    echo "openapi_breaking: REFUSED -- $*" >&2
    exit 2
}

pinned_sha256() {
    case "$1" in
        linux_amd64) echo "43a4e328e2d13ba1552d760aa68d2485c75c5621f309f6ff64ae895188345247" ;;
        linux_arm64) echo "4ae3c362d6074d919aada2dea82d0ee84366591600384455d0bdc658ddf8f7ae" ;;
        darwin_all) echo "2a479337c15afdcbf0b1e89c0b4d0cf0176472dbc11483358fed1f831d1e46c5" ;;
        *) return 1 ;;
    esac
}

platform() {
    local os arch
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"
    case "$os/$arch" in
        darwin/*) echo "darwin_all" ;;
        linux/x86_64 | linux/amd64) echo "linux_amd64" ;;
        linux/aarch64 | linux/arm64) echo "linux_arm64" ;;
        *) return 1 ;;
    esac
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

install_oasdiff() {
    local bin_dir="${1:-}" plat expected tarball url actual work
    [ -n "$bin_dir" ] || refuse "usage: openapi_breaking.sh install <bin-dir>"
    plat="$(platform)" || refuse "no pinned oasdiff build for $(uname -s)/$(uname -m)"
    expected="$(pinned_sha256 "$plat")" || refuse "no pinned sha256 for $plat"
    tarball="oasdiff_${OASDIFF_VERSION}_${plat}.tar.gz"
    url="${OASDIFF_RELEASE_URL:-https://github.com/oasdiff/oasdiff/releases/download/v${OASDIFF_VERSION}}/$tarball"
    work="$(mktemp -d)"
    curl -fsSL --retry 3 -o "$work/$tarball" "$url" || refuse "download failed: $url"
    actual="$(sha256_of "$work/$tarball")"
    if [ "$actual" != "$expected" ]; then
        refuse "sha256 mismatch for $tarball: got $actual, pinned $expected"
    fi
    tar -xzf "$work/$tarball" -C "$work" oasdiff || refuse "$tarball holds no oasdiff binary"
    mkdir -p "$bin_dir" && install -m 0755 "$work/oasdiff" "$bin_dir/oasdiff"
    rm -rf "$work"
    echo "openapi_breaking: installed oasdiff $OASDIFF_VERSION ($plat, sha256 $actual) into $bin_dir"
}

# Prints the one spec path that exists at <rev>, or in the working tree when <rev>
# is empty. Prints nothing when neither exists. Refuses when both do.
spec_path_at() {
    local rev="$1" candidate found=""
    for candidate in "${SPEC_PATHS[@]}"; do
        if [ -n "$rev" ]; then
            # `<rev>:./<path>` resolves against $ROOT; a bare `<rev>:<path>` resolves
            # against the REPOSITORY root, which is not $ROOT once the service lives in a
            # subdirectory of a larger repository. The bare form found no
            # spec there and turned every compare into a permanent exit-3 skip.
            git -C "$ROOT" cat-file -e "$rev:./$candidate" 2>/dev/null || continue
        else
            [ -f "$ROOT/$candidate" ] || continue
        fi
        if [ -n "$found" ]; then
            if [ -n "$rev" ]; then
                refuse "both $found and $candidate exist at $rev; one service publishes one contract"
            fi
            refuse "both $found and $candidate exist in this tree; one service publishes one" \
                "contract -- delete the one nothing serves"
        fi
        found="$candidate"
    done
    echo "$found"
}

# Prints the base revision to compare against, fetching it when CI named one.
# Exit 3 when there is no base at all (a manual dispatch, a branch's first push).
resolve_base() {
    local base_ref="${BASE_REF:-}" before="${BEFORE_SHA:-}"
    if [ -n "$base_ref" ]; then
        git -C "$ROOT" fetch --no-tags --depth=1 origin \
            "+refs/heads/$base_ref:refs/remotes/origin/$base_ref" >&2 \
            || refuse "cannot fetch the base branch $base_ref"
        echo "origin/$base_ref"
    elif [ -n "$before" ] && [ "$before" != "$ZERO_SHA" ]; then
        # A force-push can leave the old tip unreachable on the remote. That is a
        # missing input, not a finding about this change, so it is a named skip;
        # the pull request's own run still compares against the base branch.
        if ! git -C "$ROOT" cat-file -e "$before^{commit}" 2>/dev/null \
            && ! git -C "$ROOT" fetch --no-tags --depth=1 origin "$before" >&2; then
            echo "openapi_breaking: SKIP -- the pre-push tip $before cannot be fetched (force-push?)" >&2
            exit 3
        fi
        echo "$before"
    else
        echo "openapi_breaking: SKIP -- no base to compare against (BASE_REF and BEFORE_SHA unset or zero)" >&2
        exit 3
    fi
}

compare() {
    local base="${1:-}" old status base_spec head_spec
    command -v oasdiff >/dev/null 2>&1 || refuse "oasdiff is not on PATH; run \`openapi_breaking.sh install <dir>\` first"
    if [ -z "$base" ]; then
        base="$(resolve_base)" || exit $?
    fi
    git -C "$ROOT" rev-parse --verify --quiet "$base^{commit}" >/dev/null \
        || refuse "base revision $base does not resolve"
    base_spec="$(spec_path_at "$base")" || exit $?
    if [ -z "$base_spec" ]; then
        echo "openapi_breaking: SKIP -- the base ($base) has no committed spec yet (looked for" \
            "${SPEC_PATHS[0]}, ${SPEC_PATHS[1]}), so there is nothing to break" >&2
        exit 3
    fi
    # The base published a contract and this tree does not: every operation is gone.
    # Checked AFTER the base, so a service that has not adopted the spec yet (this
    # workflow reaches it through a kit sync) skips above instead of going red here.
    head_spec="$(spec_path_at "")" || exit $?
    if [ -z "$head_spec" ]; then
        echo "openapi_breaking: BREAKING -- the base ($base) commits $base_spec and this tree removed it." \
            "Restore it with the service's export script (\`python scripts/export_openapi.py\` in the template)." >&2
        exit 1
    fi
    old="$(mktemp -d)/base-v1.json"
    git -C "$ROOT" show "$base:./$base_spec" >"$old"
    echo "openapi_breaking: $(oasdiff --version) -- $base:$base_spec -> working tree $head_spec"
    oasdiff breaking "$old" "$ROOT/$head_spec" --fail-on ERR
    status=$?
    rm -rf "$(dirname "$old")"
    if [ "$status" -gt 1 ]; then
        echo "openapi_breaking: oasdiff itself failed (exit $status) -- no verdict on the change" >&2
        exit 1
    fi
    if [ "$status" -eq 1 ]; then
        echo "openapi_breaking: BREAKING -- a change inside the major version removes or narrows" \
            "what callers rely on. Add it beside the old path under the next major, and mark the" \
            "old operation \`deprecated: true\` with an \`x-sunset\` date; remove it only after that date." >&2
        exit 1
    fi
    echo "openapi_breaking: no breaking change against $base"
}

case "${1:-}" in
    install) shift && install_oasdiff "$@" ;;
    compare) shift && compare "$@" ;;
    *) refuse "usage: openapi_breaking.sh install <bin-dir> | compare [<base-rev>]" ;;
esac
