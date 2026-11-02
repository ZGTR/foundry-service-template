#!/usr/bin/env bash
# Emit a NEW, SELF-CONTAINED service from this template.
#
# Usage:
#   scripts/new_service.sh <service-name> [destination-directory]
#
#   scripts/new_service.sh foundry-orders
#       -> <monorepo>/services/foundry-orders   (run from the monorepo submodule)
#       -> ../foundry-orders                    (run from a standalone clone)
#   scripts/new_service.sh foundry-orders ~/src/foundry-orders
#       -> exactly that path, ready to `git init` and push
#
# TWO MODES, ONE SCRIPT. In a standalone clone of this repository the template's
# own vendor/foundry_contracts is used. When the template sits in a monorepo
# beside a `foundry-contracts` package, that sibling is the authoritative
# contracts source and is vendored fresh. The mode is decided by whether that
# sibling directory exists, never by a path name.
#
# WHY THE DESTINATION IS A PARAMETER. Until 2026-09-04 this script hardcoded
# DEST_DIR="$PACKAGES_DIR/$NEW_NAME", so its only possible output was a second
# package INSIDE the monorepo -- precisely the shape the service-extraction plan
# exists to leave. Fixing the name validator alone would have produced a
# correctly-named copy in the wrong place, which is worse than an outright
# refusal because it looks like it worked.
#
# WHAT IT DOES NOT DO: it does not touch git, install anything, or run the new
# service's tests. `tests/shape/test_new_service_script.py` does that, and it
# asserts a NON-ZERO passed count from the copy's own suite -- a green "no tests
# ran" is exactly the outcome an untested scaffolder produces.
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "usage: $0 <service-name> [destination-directory]" >&2
    exit 1
fi

NEW_NAME="$1"
# `foundry-` is the slug every Foundry service carries. `comms-` is the second
# accepted prefix: notification services are emitted from this same template with
# the comms flavour (scripts/apply_comms_flavour.py).
if ! [[ "$NEW_NAME" =~ ^(foundry|comms)-[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
    echo "error: service name must match foundry-<kebab-case> or comms-<kebab-case>, got: $NEW_NAME" >&2
    exit 1
fi
NEW_MODULE="${NEW_NAME//-/_}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PACKAGES_DIR="$(cd "$TEMPLATE_DIR/.." && pwd)"
MONOREPO_CONTRACTS="$PACKAGES_DIR/foundry-contracts/src/foundry_contracts"

if [ -d "$MONOREPO_CONTRACTS" ]; then
    MODE="monorepo"
    CONTRACTS_SRC="$MONOREPO_CONTRACTS"
    # packages/ -> platform/ -> the monorepo root, which owns services/.
    DEFAULT_DEST="$(cd "$PACKAGES_DIR/../.." && pwd)/services/$NEW_NAME"
else
    MODE="standalone"
    CONTRACTS_SRC="$TEMPLATE_DIR/service-kit/vendor/foundry_contracts"
    DEFAULT_DEST="$PACKAGES_DIR/$NEW_NAME"
fi

if [ "$#" -eq 2 ]; then
    DEST_DIR="$2"
else
    DEST_DIR="$DEFAULT_DEST"
fi

# A destination inside the monorepo's packages/ recreates the exact defect this
# script was fixed to stop, so it is refused by path rather than by convention.
# Standalone, the template's parent is an ordinary directory and a sibling
# service is the expected output, so the refusal applies to monorepo mode only.
DEST_PARENT="$(cd "$(dirname "$DEST_DIR")" 2>/dev/null && pwd || echo "")"
if [ "$MODE" = "monorepo" ] && [ -n "$DEST_PARENT" ] && [ "$DEST_PARENT" = "$PACKAGES_DIR" ]; then
    echo "error: destination is inside $PACKAGES_DIR, which would emit another monorepo" >&2
    echo "       package rather than a self-contained service. Pass a path outside it." >&2
    exit 1
fi

if [ -e "$DEST_DIR" ]; then
    echo "error: $DEST_DIR already exists" >&2
    exit 1
fi

echo "new_service: $NEW_NAME -> $DEST_DIR ($MODE mode)"
mkdir -p "$DEST_DIR"

# ENUMERATE THE ARTIFACT, do not deny-list around it.
#
# SHARP EDGE, stated because it bit during development: "the template" here means
# "what git tracks", so a NEW source file that has not been `git add`ed is
# invisible to this copy and the emitted service will be missing it. That is the
# same property that keeps caches out, seen from the other side. If an emitted
# service reports ModuleNotFoundError for a module that plainly exists, check
# `git status` before checking anything else. This used to be
# `rsync --exclude .pytest_cache --exclude __pycache__ ...`, a hardcoded list
# standing in for "the template's files" -- so anything the list did not
# anticipate was copied into every new service. It already missed `.ruff_cache`,
# which is in the tree today. `git ls-files` IS the set of files the template
# consists of, so build output and caches are excluded because they are not
# tracked, not because someone remembered them.
cd "$TEMPLATE_DIR"
FILE_COUNT=0
# TEMPLATE-ONLY FILES, named explicitly rather than skipped silently. The
# scaffolder and its test belong to the template, not to the template's output: a
# generated service is not a git checkout, so `git ls-files` there returns
# nothing and the copied test would fail for a reason that has nothing to do with
# the service. Anything else unrecognised is copied, so a NEW template file is
# included by default rather than by remembering to add it here.
# test_sync_bootstrap.py is here for the reason the paragraph above gives, arrived at
# the hard way: it drives sync.py with `--from <the template>` and `git archive`s a
# revision out of it, so it needs the TEMPLATE's git history. Emitted into a service it
# failed there, and the emitted-suite arm in test_new_service_script.py caught it --
# the copied-by-default rule working exactly as designed, since the cost of the default
# is one caught failure and the cost of the alternative is a new file silently missing
# from every service.
# apply_comms_flavour.py is an EMISSION-TIME tool: new_service.sh runs it from the
# template directory, never from the emitted copy. Shipped, it was dead code in every
# comms-* service and a second copy of the strip manifest nobody maintains
#.
# check_kit_ownership_single_reader.py guards the TEMPLATE's kit source; in a service the
# kit's bytes are pinned by check_kit_drift.py, so a second copy would prove nothing, and
# test_kit_ownership.py builds its scratch template from this repository.
# test_comms_strip_leaves_valid_python.py loads apply_comms_flavour.py, which is not
# shipped, and reads markers a comms-* emission no longer carries.
# test_iac_gate_print_streams.py runs check_service_iac.sh over copies of deploy/ that it
# edits by exact text. deploy/ is the service's own, so in a service that had changed its
# config the file went red on a correct one. The gate is the same bytes in every service
# and each service's own ci.yml runs it.
# test_template_deploy_declaration.py runs deploy_env.py over deploy/deploy_env.json and
# deploy/iac/serverless.yml and asserts what the TEMPLATE's say: the estate's listener and
# zone declared, read from the internal-edge stack's two parameters, and the rule priority
# set by the step. Two of its arms shipped inside test_deploy_env_script.py, and
# in a service that followed the README's estate opt-in one of them was red and started
# the aws CLI twice from inside pytest. test_deploy_env_script.py still ships; each arm
# left in it writes its own declaration and runs under its own stub.
TEMPLATE_ONLY="scripts/new_service.sh scripts/apply_comms_flavour.py scripts/check_kit_ownership_single_reader.py tests/shape/test_new_service_script.py tests/shape/test_sync_bootstrap.py tests/shape/test_kit_ownership.py tests/shape/test_comms_strip_leaves_valid_python.py tests/shape/test_iac_gate_print_streams.py tests/shape/test_template_deploy_declaration.py"

# ONE copy process, not three per file. The loop below only filters and counts, in
# shell builtins; the bytes move in a single tar pipeline. The per-file form ran
# `mkdir`, `dirname` and `cp` for each of ~246 files, and on a machine where starting
# a process costs ~0.26s (endpoint security) that alone was most of a 302.8s emission
# whose CPU time was 3.9s. The template tracks no symlinks and
# no gitlinks, so tar copies exactly the bytes and modes `cp` did.
COPY_LIST="$(mktemp)"
trap 'rm -f "$COPY_LIST"' EXIT
while IFS= read -r -d '' rel; do
    case " $TEMPLATE_ONLY " in
        *" $rel "*) continue ;;
    esac
    printf '%s\0' "$rel" >> "$COPY_LIST"
    FILE_COUNT=$((FILE_COUNT + 1))
done < <(git ls-files -z .)
if [ "$FILE_COUNT" -gt 0 ]; then
    # COPYFILE_DISABLE keeps macOS tar from carrying extended attributes along as
    # AppleDouble entries; GNU tar ignores it.
    COPYFILE_DISABLE=1 tar --null -T "$COPY_LIST" -cf - | (cd "$DEST_DIR" && tar -xf -)
fi

# Refuse a scan that saw nothing. Run from a non-git checkout, `git ls-files`
# exits non-zero or prints nothing, and without this the script would cheerfully
# create an EMPTY directory and report success.
if [ "$FILE_COUNT" -lt 20 ]; then
    # The comparison figure is COUNTED, not quoted. This line read "the template
    # has ~69" while the template tracked 246 files -- a hand-maintained number
    # standing in for the artifact it describes, 3.5x low, in the one message a
    # reader consults when the copy has gone wrong. The floor of 20 is the guard;
    # this number is orientation, and stale orientation sends you the wrong way.
    echo "error: copied only $FILE_COUNT file(s) from $TEMPLATE_DIR;" >&2
    echo "       git ls-files there reports $(git ls-files . | wc -l | tr -d ' ')." >&2
    echo "       The scan saw nothing usable -- is this a git checkout?" >&2
    exit 1
fi
echo "new_service: copied $FILE_COUNT tracked file(s)"

echo "new_service: renaming module foundry_service_template -> $NEW_MODULE"
mv "$DEST_DIR/src/foundry_service_template" "$DEST_DIR/src/$NEW_MODULE"

echo "new_service: rewriting references"
REWRITTEN=0
# NUL-delimited: `for f in $FILES` word-split an unquoted command substitution,
# so a path containing whitespace became two broken paths.
# ONE rewrite process for every file, not a `sed` and an `rm` per file.
# The loop only collects paths; perl edits them in place.
REWRITE_FILES=()
while IFS= read -r -d '' f; do
    REWRITE_FILES+=("$f")
    REWRITTEN=$((REWRITTEN + 1))
done < <(grep -rlI --null --exclude-dir=service-kit \
    "foundry_service_template\|foundry-service-template" "$DEST_DIR" || true)
if [ "$REWRITTEN" -gt 0 ]; then
    NEW_MODULE="$NEW_MODULE" NEW_NAME="$NEW_NAME" perl -pi -e \
        's/foundry_service_template/$ENV{NEW_MODULE}/g; s/foundry-service-template/$ENV{NEW_NAME}/g' \
        -- "${REWRITE_FILES[@]}"
fi

# `service-kit/` IS EXCLUDED, and leaving it in was a real defect rather than
# untidiness. The kit is template-owned and hash-pinned by service-kit/MANIFEST.json,
# so rewriting a single byte of it makes the emitted service fail
# `service-kit/gates/check_kit_drift.py` on arrival -- measured 2026-09-18, 7 findings
# on a pristine scaffold before anything was edited, identically for two different
# service names, which is what proved it was the scaffolder and not the service.
#
# Every name the kit carries is a COMMENT, a docstring, a URL or the drift gate's own
# error message; nothing functional, which is exactly what "the kit is name-agnostic"
# means and what the manifest asserts. One of them was actively harmful: the rewrite
# turned sync.py's DEFAULT_TEMPLATE_URL into the EMITTED SERVICE's own repository, so
# `python service-kit/sync.py` in a fresh service would fetch the kit from itself. The
# drift gate's remedy line was rewritten the same way and told the reader to fix the
# template in the service that had just been created.
#
# The floor below still counts only the files this loop rewrote, so excluding the kit
# lowers that count -- the kit accounts for 6 of the ~40 the template names itself in.

# A rewrite that touched nothing is the failure this script had for one revision:
# `grep -rlIZ` on BSD grep 2.6.0 emits a NEWLINE terminator, not NUL, so a
# `read -d ''` loop consumed one unterminated record and ran its body zero times
# -- at exit 0, with every file copied and none renamed. Count, then refuse.
if [ "$REWRITTEN" -lt 10 ]; then
    echo "error: rewrote $REWRITTEN file(s); the template names itself in ~40." >&2
    echo "       The rename pass matched nothing -- refusing to emit a half-renamed service." >&2
    exit 1
fi
echo "new_service: rewrote $REWRITTEN file(s)"

# VENDOR foundry-contracts. A self-contained service must carry its dependency:
# the Dockerfile COPYs `vendor/foundry_contracts`, and CI resolves it through
# PYTHONPATH=vendor. The template's own tracked vendor/ copy arrived with the
# `git ls-files` pass above; in monorepo mode it is REPLACED by the sibling package,
# so an emitted service carries the contracts the monorepo holds today rather than
# whatever the template last vendored.
#
# The cost, stated rather than discovered later: this is a COPY, so a
# foundry-contracts change does not reach an emitted service until someone
# re-vendors it. That is the price of "self-contained"; the alternative is a
# private index, which trades it for infrastructure.
if [ ! -f "$CONTRACTS_SRC/__init__.py" ]; then
    echo "error: foundry-contracts not found at $CONTRACTS_SRC ($MODE mode)" >&2
    exit 1
fi
# Monorepo mode goes through the ONE vendoring writer, never a
# bare `cp`. A `cp` replaced the bytes and left the VENDOR.json that arrived with the
# template describing the template's copy, so every emitted service could start with
# check_vendor_digests red. The writer takes the bytes from git at the monorepo's HEAD
# and records that revision and their digest in the same step. It follows that an
# uncommitted edit to foundry-contracts is NOT vendored: a record naming a commit
# that does not contain the bytes would be false from the first line.
if [ "$MODE" = "monorepo" ]; then
    MONOREPO_ROOT="$(cd "$PACKAGES_DIR/../.." && pwd)"
    python3 "$TEMPLATE_DIR/scripts/vendor_package.py" foundry_contracts \
        --monorepo "$MONOREPO_ROOT" --revision HEAD --into "$DEST_DIR" \
        --discard-local-changes
fi
/usr/bin/find "$DEST_DIR/service-kit/vendor" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
if [ ! -f "$DEST_DIR/service-kit/vendor/foundry_contracts/principal.py" ]; then
    echo "error: the emitted service has no service-kit/vendor/foundry_contracts ($MODE mode)" >&2
    exit 1
fi
echo "new_service: vendored foundry_contracts from $CONTRACTS_SRC"

# The template's Dockerfile and ruff config are already rooted at the repository
# they live in, so an emitted copy needs no rewriting. These two checks refuse the
# regressions that made an emitted service unbuildable or unlintable before the
# template was its own repository: a COPY of a monorepo `packages/` path
# and a ruff `extend` pointing outside the checkout.
if grep -n 'packages/' "$DEST_DIR/service-kit/docker/Dockerfile" >&2; then
    echo "error: the emitted Dockerfile references a monorepo packages/ path" >&2
    exit 1
fi
if grep -nE '^extend *= *"\.\./' "$DEST_DIR/pyproject.toml" >&2; then
    echo "error: the emitted pyproject.toml extends a ruff config outside the service" >&2
    exit 1
fi

# --- comms flavour: no SQS point-to-point publish path -----------------------
#
# comms-02 rule 1: a producer holds only events:PutEvents on its own bus; no
# queue URL or consumer endpoint in code, config or IaC. The template's default
# flavour carries SqsPublisher, EVENT_BUS_MODE=sqs, sqs:SendMessage IAM and the
# outbox relay's downstream aws_sqs_queue -- exactly the coupling that rule
# forbids. Declared as ONE manifest in
# scripts/apply_comms_flavour.py rather than scattered seds here.
if [[ "$NEW_NAME" == comms-* ]]; then
    python3 "$TEMPLATE_DIR/scripts/apply_comms_flavour.py" --dest "$DEST_DIR" --module "$NEW_MODULE"
fi

# THE CONTRACT GATES, asserted on the EMITTED tree rather than assumed from the copy.
# `git ls-files` copies what is tracked, so a gate that was never `git add`ed is
# invisible here -- and a service scaffolded without its gates is a service whose
# architecture is a starting position rather than a contract.
#
# ENUMERATED from the template's own scripts/, never a hardcoded roster. The list
# here read `check_layering check_mvc_separation check_size_limits
# check_mock_budget check_docker_shared check_lifecycle` -- six names, while the
# template already shipped check_test_first.py and later regained
# check_seeded_tests.py. Both could have gone missing from an emitted service with
# this block green and its message reporting a full six. A glob makes gate number
# nine covered by default rather than by someone remembering this line.
GATE_COUNT=0
MISSING_GATES=""
for gate_path in "$TEMPLATE_DIR"/service-kit/gates/check_*.py; do
    [ -f "$gate_path" ] || continue
    gate="${gate_path##*/}"
    GATE_COUNT=$((GATE_COUNT + 1))
    if [ ! -f "$DEST_DIR/service-kit/gates/$gate" ]; then
        MISSING_GATES="$MISSING_GATES $gate"
    fi
done
# Refuse a glob that matched (almost) nothing. Without a floor, a scripts/ that
# failed to copy reports "emitted 0 contract gate(s)" at exit 0 -- the vacuous-scan
# pass the FILE_COUNT floor above exists to prevent for the copy as a whole, and an
# absence gate is exactly where it costs most, because finding nothing IS its pass.
if [ "$GATE_COUNT" -lt 6 ]; then
    echo "error: found only $GATE_COUNT gate(s) in $TEMPLATE_DIR/service-kit/gates; expected at least 6." >&2
    echo "       The glob matched nothing usable -- refusing to certify the emitted gates." >&2
    exit 1
fi
if [ -n "$MISSING_GATES" ]; then
    echo "error: the emitted service is missing contract gate(s):$MISSING_GATES" >&2
    echo "       They are template files -- are they tracked by git?" >&2
    exit 1
fi
echo "new_service: emitted $GATE_COUNT contract gate(s) into service-kit/gates/"

# THE SEEDED + E2E TIER (contract S13/S14). These are ordinary tracked files, so
# the `git ls-files` pass above copies them -- but the whole tier was LOST when the
# template was extracted into its own repository, and nothing noticed, because a
# missing test directory costs nothing until somebody goes looking for it. Named
# here so a second extraction is a hard error rather than a silence.
MISSING_SEEDED=""
for artefact in service-kit/gates/check_seeded_tests.py scripts/prove_seeded_tests.py \
                tests/support/environment.py; do
    if [ ! -f "$DEST_DIR/$artefact" ]; then
        MISSING_SEEDED="$MISSING_SEEDED $artefact"
    fi
done

# THE TEST KIT, ENUMERATED BY GLOB, for the reason the gate block above already
# states: this line named `foundry_testkit/seeded_case.py` and nothing else, so
# `real_profile.py` -- which carries the entire real-resource lifecycle -- was
# emitted or not emitted without this check noticing either way. It did not
# notice: the emitted service failed at `pytest_plugins` with
# `ModuleNotFoundError: No module named 'foundry_testkit.real_profile'`, several
# steps from the roster that caused it. A glob makes kit module number nine
# covered by default rather than by someone remembering this line.
KIT_MODULE_COUNT=0
for module_path in "$TEMPLATE_DIR"/service-kit/testing/foundry_testkit/*.py; do
    [ -f "$module_path" ] || continue
    module="${module_path##*/}"
    KIT_MODULE_COUNT=$((KIT_MODULE_COUNT + 1))
    if [ ! -f "$DEST_DIR/service-kit/testing/foundry_testkit/$module" ]; then
        MISSING_SEEDED="$MISSING_SEEDED service-kit/testing/foundry_testkit/$module"
    fi
done
# The same floor the gates get, for the same reason: a glob that matched nothing
# reports every module present and certifies a kit that never travelled.
if [ "$KIT_MODULE_COUNT" -lt 5 ]; then
    echo "error: found only $KIT_MODULE_COUNT test-kit module(s) in $TEMPLATE_DIR/service-kit/testing/foundry_testkit; expected at least 5." >&2
    exit 1
fi
# The two tier DIRECTORIES are checked for a test module, not for existence. The
# copy loop runs `mkdir -p` per file, so `-d tests/seeded` would be satisfied by a
# directory that any unrelated path created and would prove nothing travelled.
for tier in seeded e2e; do
    if [ -z "$(/usr/bin/find "$DEST_DIR/tests/$tier" -name 'test_*.py' -print -quit 2>/dev/null)" ]; then
        MISSING_SEEDED="$MISSING_SEEDED tests/$tier/test_*.py"
    fi
done
if [ -n "$MISSING_SEEDED" ]; then
    echo "error: the emitted service is missing seeded-tier file(s):$MISSING_SEEDED" >&2
    exit 1
fi
echo "new_service: emitted the seeded + e2e test tier"

# The shared containerisation folder, same rule: named, and missing is an error.
MISSING_DOCKER=""
for artefact in Dockerfile entrypoint.sh healthcheck.sh wait-for.sh README.md; do
    if [ ! -f "$DEST_DIR/service-kit/docker/$artefact" ]; then
        MISSING_DOCKER="$MISSING_DOCKER $artefact"
    fi
done
# The FRAGMENTS are counted by glob rather than named, because naming them is how
# a fourth one arrives unverified. This list read `compose/localstack.yml
# compose/postgres.yml compose/service.yml` when `compose/redis.yml` was added:
# the copy is a directory copy, so the new fragment DID reach the emitted service
# and nothing here would have noticed if it had not. Same defect, same file, as
# the `foundry_testkit/*.py` roster a few lines below.
FRAGMENT_COUNT=0
for fragment in "$DEST_DIR"/service-kit/docker/compose/*.yml; do
    [ -f "$fragment" ] || continue
    FRAGMENT_COUNT=$((FRAGMENT_COUNT + 1))
done
# A FLOOR, so a glob that matched nothing cannot read as a clean emission. Four is
# what the kit ships today (localstack, postgres, redis, service); a fifth is
# covered automatically and a deletion is a finding.
if [ "$FRAGMENT_COUNT" -lt 4 ]; then
    MISSING_DOCKER="$MISSING_DOCKER compose/*.yml(found=$FRAGMENT_COUNT,expected>=4)"
fi
if [ -n "$MISSING_DOCKER" ]; then
    echo "error: the emitted service is missing service-kit/docker/ artefact(s):$MISSING_DOCKER" >&2
    exit 1
fi
echo "new_service: emitted the kit's docker/ folder ($FRAGMENT_COUNT compose fragment(s))"

# The SERVICE-OWNED half of the split: which resources this service runs and how
# it ships. `check_folder_structure.py` also requires `deploy/compose.yml`, and
# that gate is the enumerating authority -- this block is here so a failure names
# the missing file instead of arriving later as "put it under an existing one".
MISSING_DEPLOY=""
for artefact in compose.yml iac/serverless.yml iac/app/main.tf iac/resources/main.tf; do
    if [ ! -f "$DEST_DIR/deploy/$artefact" ]; then
        MISSING_DEPLOY="$MISSING_DEPLOY $artefact"
    fi
done
if [ -n "$MISSING_DEPLOY" ]; then
    echo "error: the emitted service is missing deploy/ artefact(s):$MISSING_DEPLOY" >&2
    echo "       They are template files -- are they tracked by git?" >&2
    exit 1
fi
echo "new_service: emitted the service-owned deploy/ folder"

# THE PUBLISHED API CONTRACT: the exporter, the committed spec, the
# drift test that keeps the two equal, and the kit script CI's breaking-change step
# calls. All four are tracked files the copy above carries, and they only work as a
# set: a spec with no drift test goes stale silently, and the CI step with no spec
# skips forever. The spec's `info.title` is the one place it names the service, and
# the rewrite pass must have reached it or the emitted suite is red on arrival.
MISSING_CONTRACT=""
for artefact in scripts/export_openapi.py contracts/openapi/v1.json \
                tests/shape/test_openapi_contract.py service-kit/ci/openapi_breaking.sh; do
    if [ ! -f "$DEST_DIR/$artefact" ]; then
        MISSING_CONTRACT="$MISSING_CONTRACT $artefact"
    fi
done
if [ -n "$MISSING_CONTRACT" ]; then
    echo "error: the emitted service is missing API-contract artefact(s):$MISSING_CONTRACT" >&2
    echo "       They are template files -- are they tracked by git?" >&2
    exit 1
fi
if ! grep -q "\"title\": \"$NEW_NAME\"" "$DEST_DIR/contracts/openapi/v1.json"; then
    echo "error: the emitted contracts/openapi/v1.json is not titled $NEW_NAME;" >&2
    echo "       the rename pass did not reach the committed spec." >&2
    exit 1
fi
echo "new_service: emitted the OpenAPI contract (exporter, spec, drift test, CI comparison)"

echo "new_service: done."
echo "  cd $DEST_DIR"
echo "  python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements/service.lock"
echo "  .venv/bin/pip install --no-deps -e . && .venv/bin/pip install httpx jsonschema 'moto[server]' redis pytest"
echo "  .venv/bin/python -m pytest tests"   # pyproject pythonpath carries the kit
echo
# Build through deploy/compose.yml, the one place a service declares SERVICE_PACKAGE
# and HAS_MIGRATIONS. A bare `docker build` hint had to repeat both, and CI's copy of
# that command dropped one.
echo "  SERVICE_IMAGE=$NEW_NAME:local docker compose -f deploy/compose.yml build service"
