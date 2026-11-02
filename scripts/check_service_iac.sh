#!/usr/bin/env bash
# check_service_iac.sh -- prove this service's IaC parses, resolves and
# validates, instead of asserting it in a comment.
#
# This is the service-repo descendant of the monorepo's
# `scripts/check_template_iac.sh`, which found a real defect on its first run:
# `serverless print --config <subdir>/serverless.yml` from a parent exits 1, so
# the template's deploy step could never have worked. That file is checked into
# a different repository, so this one is not a copy for tidiness -- the arms
# have to run where the IaC lives or they do not run at all. `new_service.sh`
# emits this file into every service it creates, with `SERVICE` below rewritten
# to the new name by the same pass that renames the Python module.
#
# A service with its own model-egress, network-egress or other scoped-grant
# requirement adds its own arm here (see llm-gateway's arm 8,
# which checks a resolved Bedrock IAM grant against an allow-list) and bumps
# EXPECTED_ARMS to match -- that arm is specific to what the gateway deploys,
# not to every service this template emits.
#
# NO CONDITIONAL ARMS. Every arm runs or this script refuses. A gate that skips
# when a binary is missing exits 0 having checked nothing, and that zero is
# byte-identical to a real pass. Missing tooling is a REFUSAL (exit 2) naming the
# install command.
#
# Usage:  bash scripts/check_service_iac.sh

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# TWO terraform roots since the resource/application split. `resources` owns the
# durable state (keys, queues, buckets) and `app` owns the release (task, routing,
# IAM, logs). They are validated INDEPENDENTLY: a root that only validates as a
# pair is not separately deployable, and the split would then be decorative.
TF_ROOTS=(app resources)
SLS_DIR="$ROOT_DIR/deploy/iac"
WORKFLOW="$ROOT_DIR/.github/workflows/ci.yml"
SERVICE="foundry-service-template"

# EIGHT, not seven. This read `7` while the file invoked eight arms, so deleting
# any one of them left `RAN` at 7, cleared the floor, and printed the full success
# line at exit 0 -- the floor-below-count defect, inside the gate written to
# prevent the class. The eighth arm ("no resolved function is deployed with an
# import-only command") was added when `drainOutbox` was deleted; this line was not
# bumped with it, which is the same half-landed change that left the monorepo's own
# `scripts/check_template_iac.sh` red.
# TWELVE since the resource/application split: the four terraform arms now run
# once PER ROOT. Bumping this line is part of adding a root, exactly as it is part
# of adding an arm -- the comment above records what a stale floor already cost here.
# FOURTEEN since an earlier change added "no non-HTTP function competes with the
# adapter's runtime client" (R.1 named this arm for the awslambdaric shape it
# required then; R.2 redesigned the arm's CONTENT -- awslambdaric turned out to
# be a second runtime client competing with the adapter -- without changing the
# arm COUNT, so this line did not need a second bump).
# NINETEEN since an earlier change: five arms for a service that CALLS another
# private service -- one terraform test and four over the resolved serverless
# config (the block before the verdict says what each one holds).
# TWENTY since that slice's second review: `estate` committed with its two variables
# undeclared was refused only by the framework, after the image push. Arm 20 refuses
# it here, before the build.
EXPECTED_ARMS=20
RAN=0
FAILED=0

# Every arm goes through here, so the count in the verdict cannot claim an arm
# that an abort skipped -- there is no `set -e`, and a red arm records itself and
# lets the rest run.
arm() {
  local name="$1"
  shift
  RAN=$((RAN + 1))
  if "$@"; then
    echo "[service-iac] ok: $name"
  else
    echo "[service-iac] FAIL: $name" >&2
    FAILED=$((FAILED + 1))
  fi
}

refuse() {
  echo "[service-iac] REFUSED: $1" >&2
  exit 2
}

# --- refusals: the inputs this gate cannot check without ----------------------

for _root in "${TF_ROOTS[@]}"; do
  [ -d "$ROOT_DIR/deploy/iac/$_root" ] || refuse "no terraform root at deploy/iac/$_root"
done
[ -f "$SLS_DIR/serverless.yml" ] || refuse "no serverless.yml at ${SLS_DIR#"$ROOT_DIR"/}"
# serverless.yml reads this file through a fallback, so the framework treats a missing
# one as "no VPC" and says nothing. This gate does not.
[ -f "$SLS_DIR/caller_vpc.yml" ] || refuse "no caller_vpc.yml at ${SLS_DIR#"$ROOT_DIR"/}"
[ -f "$WORKFLOW" ] || refuse "no workflow at ${WORKFLOW#"$ROOT_DIR"/}"

TF_BIN=""
for candidate in terraform tofu /opt/homebrew/bin/tofu; do
  if command -v "$candidate" >/dev/null 2>&1; then
    TF_BIN="$candidate"
    break
  fi
done
[ -n "$TF_BIN" ] || refuse "neither 'terraform' nor 'tofu' is on PATH; \`brew install opentofu\`"

# Every arm below inits in a THROWAWAY copy, so without a shared plugin cache each of the
# two roots re-downloads the provider on every invocation. Measured 2026-09-18: ~650MB and
# 380-658s per root, which is how this gate came to exceed its own test's 180s budget.
#
# The cache alone does not fix it, and that is the part worth knowing. A lockfile naming
# only registry.terraform.io cannot be satisfied by a cache entry tofu writes under
# registry.opentofu.org, so the cache is REPOPULATED every run rather than used -- the cold
# and warm logs came back byte-identical. Both roots' locks therefore carry BOTH provider
# blocks, which .terraform.lock.hcl permits because blocks are keyed by full source address.
# With the pair in place: 380s -> 0-1s, and the log finally says "Using ... from the shared
# cache directory" with no accompanying "Installing ... to" line.
export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
mkdir -p "$TF_PLUGIN_CACHE_DIR" || refuse "cannot create the provider cache at $TF_PLUGIN_CACHE_DIR"

command -v npx >/dev/null 2>&1 || refuse "'npx' is not on PATH; install Node to run 'serverless print'"

# --- arms 1-4: terraform, in a COPY -------------------------------------------
#
# The copy is not tidiness. `init` REWRITES `.terraform.lock.hcl` with hashes
# from whichever registry the binary uses, so validating with OpenTofu in place
# would silently replace the terraform-registry hashes CI verifies against.

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# One (root, arm) pair per output line. A single unlabelled "terraform validate ok"
# cannot distinguish a root that passed from a root that was never reached.
for _root in "${TF_ROOTS[@]}"; do
  _src="$ROOT_DIR/deploy/iac/$_root"
  _copy="$WORK/$_root"
  [ -d "$_src" ] || refuse "no terraform root at deploy/iac/$_root"
  mkdir -p "$_copy"
  cp -R "$_src/." "$_copy/"

  tf_init() { (cd "$1" && "$TF_BIN" init -backend=false -input=false >/dev/null); }
  tf_validate() { (cd "$1" && "$TF_BIN" validate >/dev/null); }
  tf_fmt() { (cd "$1" && "$TF_BIN" fmt -check -recursive >/dev/null); }
  tf_lock_untouched() {
    # Per root, not shared: one shared assertion would let a root's ABSENCE of a
    # lockfile read as a pass.
    #
    # THE ABSENT CASE IS A REFUSAL, NOT A PASS. This used to be `[ -f … ] || return 0`,
    # which printed "ok: [resources] the repo's .terraform.lock.hcl was not rewritten"
    # about a file that did not exist -- the comment above claimed the per-root split
    # prevented exactly that, and the early return did it anyway. A conditional arm skips
    # silently while the pass claim keeps its full scope. Both roots now ship a lock, so
    # a missing one is a defect rather than a shape to tolerate, and an unpinned root is
    # what put this gate over its own test's time budget.
    local rel="deploy/iac/$1/.terraform.lock.hcl"
    [ -f "$ROOT_DIR/$rel" ] || {
      echo "[service-iac] $rel is missing; an unpinned root resolves against the registry" >&2
      echo "[service-iac]   on every init, so its cost tracks the registry rather than this" >&2
      echo "[service-iac]   repository. Commit a lock carrying BOTH provider blocks." >&2
      return 1
    }
    git -C "$ROOT_DIR" diff --quiet -- "$rel"
  }

  arm "[$_root] terraform init (no backend)" tf_init "$_copy"
  arm "[$_root] terraform validate" tf_validate "$_copy"
  arm "[$_root] terraform fmt -check" tf_fmt "$_src"
  arm "[$_root] the repo's .terraform.lock.hcl was not rewritten" tf_lock_untouched "$_root"
done

# --- arms 5-6: serverless.yml resolves ----------------------------------------
#
# Placeholder values, deliberately syntactically real: `print` resolves every
# ${env:...} and reports an unresolved variable as an error, so a fake that is
# merely well-formed is enough and no real account is touched.

SLS_PLACEHOLDERS=(
  AWS_REGION=eu-west-1
  "IMAGE_URI=123456789012.dkr.ecr.eu-west-1.amazonaws.com/x@sha256:$(printf '0%.0s' {1..64})"
  SQS_QUEUE_URL=https://sqs.eu-west-1.amazonaws.com/123456789012/q
  SQS_QUEUE_ARN=arn:aws:sqs:eu-west-1:123456789012:q
  INTERNAL_LISTENER_ARN=arn:aws:elasticloadbalancing:eu-west-1:123456789012:listener/app/internal/0123456789abcdef/0123456789abcdef
  LISTENER_RULE_PRIORITY=100
  INTERNAL_ZONE_SUFFIX=svc.example.internal
)
# Read only when `custom.callerVpc` is `estate`. The committed config gets them so a
# service that selected `estate` still resolves here; the caller-VPC arms below set
# and unset them on purpose.
CALLER_VPC_PLACEHOLDERS=(
  ESTATE_CALLER_SG_ID=sg-0c0ffee0c0ffee0c0
  "PRIVATE_SUBNET_IDS=subnet-0aaaaaaaaaaaaaaaa,subnet-0bbbbbbbbbbbbbbbb"
)

# One environment policy for EVERY print, including the live warning control.
# FORCE_COLOR changes the heading's bytes; SLS_LOG_LEVEL=error suppresses it.
SLS_PRINT_ENV=(-u FORCE_COLOR -u SLS_LOG_LEVEL)

# --- every print keeps its two streams apart ----------------------------------
#
# `serverless print` writes the resolved config to stdout and a configuration warning
# to stderr, and exits 0 on a warning. Each print here lands in <name>.out and
# <name>.err. Merged into one file, a warning made the YAML unparseable, and every arm
# that parses the print died in the parser instead of reporting.
#
# $1 is a print's stderr. Returns 1, naming each warning, when the framework wrote
# one: `serverless deploy` prints the same lines and then deploys.
sls_print_has_no_warning() {
  /usr/bin/python3 - "$1" <<'PYWARNING'
import re
import sys
from pathlib import Path

# With FORCE_COLOR in its environment the framework colours this stream even into a
# file. Every line then opens with a CSI sequence, so the heading does not start its
# line and neither does the indentation under it. The prints here run without the
# variable. 7-bit CSI sequences are taken out all the same, colour or not, and nothing else
# is: an OSC sequence, a bare ESC or a byte order mark in front of the heading hides it.
CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def warnings(text):
    """Each block the framework opened with `Warning:`, as one line: the heading and
    the indented lines directly under it. Nothing else on the stream is a finding:
    npx writes there too, on a runner that has not cached the framework."""
    blocks, current = [], None
    for line in CSI.sub("", text).splitlines():
        if line.startswith("Warning:"):
            current = [line.strip()]
            blocks.append(current)
        elif current is not None and line[:1] in (" ", "\t"):
            current.append(line.strip())
        else:
            current = None
    return ["; ".join(block) for block in blocks]


# INLINE CONTROLS, BOTH DIRECTIONS. The first offender is the framework's own text
# (3.40.0) for a `provider.vpc` that resolved to an empty mapping.
INVALID_VPC = (
    "\nWarning: Invalid configuration encountered\n"
    "  at 'provider.vpc': must have required property 'securityGroupIds'\n"
    "  at 'provider.vpc': must have required property 'subnetIds'\n"
    "\nLearn more about configuration validation here: http://slss.io/configuration-validation\n"
    "\n1 deprecation found: run 'serverless doctor' for more details\n"
)
INVALID_VPC_FOUND = [
    "Warning: Invalid configuration encountered; "
    "at 'provider.vpc': must have required property 'securityGroupIds'; "
    "at 'provider.vpc': must have required property 'subnetIds'"
]


def coloured(on):
    """INVALID_VPC as the framework wrote it to a file with FORCE_COLOR set (3.40.0,
    node 26.10.0): each line opens with the colour `on` and closes with ESC[39m."""
    off, grey = "\x1b[39m", "\x1b[90m"
    return (
        f"\n{on}Warning:{off} {on}Invalid configuration encountered{off}\n"
        f"{on}  at 'provider.vpc': must have required property 'securityGroupIds'{off}\n"
        f"{on}  at 'provider.vpc': must have required property 'subnetIds'{off}\n"
        f"{on}{off}\n"
        f"{on}Learn more about configuration validation here: http://slss.io/configuration-validation{off}\n"
        f"\n{on}{grey}1 deprecation found: run 'serverless doctor' for more details{off}{on}{off}\n"
    )


CONTROLS = (
    ("an empty stream", "", []),
    ("an invalid provider.vpc", INVALID_VPC, INVALID_VPC_FOUND),
    ("the same warning under FORCE_COLOR=1", coloured("\x1b[38;2;255;165;0m"), INVALID_VPC_FOUND),
    ("the same warning under FORCE_COLOR=1 and CI=true", coloured("\x1b[93m"), INVALID_VPC_FOUND),
    ("a CSI sequence that is not a colour, before the heading",
     "\x1b[2K\x1b[1GWarning: one\n  at 'a': x\n", ["Warning: one; at 'a': x"]),
    ("7-bit CSI private parameters, intermediate bytes and a non-letter final byte",
     "\x1b[?25l\x1b[0 q\x1b[0@Warning: one\n  at 'a': x\n",
     ["Warning: one; at 'a': x"]),
    ("the heading in capitals is not the framework's", "WARNING: one\n  at 'a': x\n", []),
    ("a longer word that starts like the heading", "Warnings: one\n  at 'a': x\n", []),
    ("an indented heading is not one", "  Warning: one\n  at 'a': x\n", []),
    ("a tab-indented line is under the warning",
     "Warning: one\n\tat 'a': x\n", ["Warning: one; at 'a': x"]),
    ("a line that is not indented ends the warning",
     "Warning: one\nnot indented\n  at 'a': x\n", ["Warning: one"]),
    ("two warnings, the second with nothing under it",
     "Warning: one\n  at 'a': x\nWarning: two\n", ["Warning: one; at 'a': x", "Warning: two"]),
    ("an indented line after a blank one is not under the warning",
     "Warning: one\n\n  later\n", ["Warning: one"]),
    ("npx installing the framework",
     "npm warn exec The following package was not found and will be installed: serverless@3.40.0\n", []),
    ("a deprecation count alone",
     "\n1 deprecation found: run 'serverless doctor' for more details\n", []),
    ("the word inside a line", "npm notice a Warning: inside the line\n", []),
)
broken = [
    f"{label}: got {warnings(text)!r}, want {expected!r}"
    for label, text, expected in CONTROLS
    if warnings(text) != expected
]
if broken:
    for failure in broken:
        print(f"the print-warning classifier is broken: {failure}", file=sys.stderr)
    raise SystemExit(1)

path = Path(sys.argv[1])
# No capture is not "no warning". A print that merged its streams leaves no file here.
if not path.is_file():
    print(
        f"no stderr capture at {path.name}: the print did not keep its streams apart, so a "
        "warning could not be told from the config",
        file=sys.stderr,
    )
    raise SystemExit(1)
found = warnings(path.read_text(encoding="utf-8", errors="replace"))
for warning in found:
    print(
        "serverless print resolved the config with a warning, which `serverless deploy` "
        f"prints and then deploys past: {warning}",
        file=sys.stderr,
    )
raise SystemExit(1 if found else 0)
PYWARNING
}

# $1 is a print's stdout. An arm that parses it calls this first. When the framework
# refuses a print its reason is on stdout in place of the config, and is not YAML, so
# without this the arm's red is a parser traceback that names no finding.
sls_reads_a_config() {
  /usr/bin/python3 - "$1" <<'PYREADS'
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "PyYAML is not importable, so the print could not be PARSED, and this gate does "
        "not fall back to a text scan of a resolved config.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def not_a_config(text):
    """Why `text` is not a resolved serverless config, or None when it is one."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return f"it does not parse as YAML ({str(error).splitlines()[0]})"
    if not isinstance(doc, dict):
        return f"it is {type(doc).__name__}, not a mapping"
    missing = [key for key in ("service", "provider") if key not in doc]
    if missing:
        return f"it has no {' or '.join(missing)} key"
    return None


# INLINE CONTROLS, BOTH DIRECTIONS. The refusal is the framework's own text (3.40.0)
# for a variable nothing sets; the merged one is what the print arms read before the
# streams were kept apart.
CONFIG = "service: example\nprovider:\n  name: aws\nfunctions:\n  api: {}\n"
REFUSAL = (
    "Error:\nCannot resolve serverless.yml: Variables resolution errored with:\n"
    '  - Cannot resolve variable at "functions.api.events.0.alb.listenerArn": '
    'Value not found at "env" source\n'
)
CONTROLS = (
    ("a resolved config", CONFIG, True),
    ("the framework's refusal", REFUSAL, False),
    ("a config with a warning merged in", CONFIG + "\nWarning: Invalid configuration encountered\n"
     "  at 'provider.vpc': must have required property 'subnetIds'\n", False),
    ("an empty print", "", False),
    ("a bare line of text", "nothing to print\n", False),
    ("a list", "- service\n- provider\n", False),
    ("a mapping with no provider", "service: example\n", False),
    ("a mapping with no service", "provider:\n  name: aws\n", False),
)
broken = [
    f"{label}: got {not_a_config(text)!r}, want {'None' if readable else 'a reason'}"
    for label, text, readable in CONTROLS
    if (not_a_config(text) is None) is not readable
]
if broken:
    for failure in broken:
        print(f"the resolved-config reader is broken: {failure}", file=sys.stderr)
    raise SystemExit(1)

path = Path(sys.argv[1])
if not path.is_file():
    print(f"{path.name} is not a resolved config: no print wrote it", file=sys.stderr)
    raise SystemExit(1)
reason = not_a_config(path.read_text(encoding="utf-8", errors="replace"))
if reason:
    print(
        f"{path.name} is not a resolved config: {reason}. The arm that ran this print says "
        "what the framework said; this arm had nothing to read",
        file=sys.stderr,
    )
    raise SystemExit(1)
PYREADS
}

sls_print() {
  local code=0
  # Without two of the caller's variables. FORCE_COLOR: with it the framework colours
  # stderr even into a file, and a coloured heading does not start its line. NO_COLOR
  # does not undo it; node ignores NO_COLOR while FORCE_COLOR is set. SLS_LOG_LEVEL: at
  # `error` the framework writes no warning at all, so the print of a warned config is
  # a clean one.
  ( cd "$SLS_DIR" \
    && env "${SLS_PRINT_ENV[@]}" \
       "${SLS_PLACEHOLDERS[@]}" "${CALLER_VPC_PLACEHOLDERS[@]}" \
       npx --yes serverless@3 print --config serverless.yml --stage dev \
         > "$WORK/sls.out" 2> "$WORK/sls.err" ) || code=$?
  if [ "$code" -ne 0 ]; then
    echo "[service-iac] serverless print exited $code" >&2
    cat "$WORK/sls.out" "$WORK/sls.err" >&2
    return 1
  fi
  sls_live_warning_control
  sls_print_has_no_warning "$WORK/sls.err"
}

# The inline controls prove the classifier; this proves the framework still emits
# what it classifies. Never trust a clean print from a warning-silent framework.
sls_live_warning_control() {
  local dir="$WORK/warning-control" code=0 classified=0
  mkdir -p "$dir"
  cp "$SLS_DIR/"*.yml "$dir/" || refuse "cannot copy the live warning control config"
  # A top-level unknown property needs no knowledge of this service's functions.
  printf '\neilWarningControlUnknown: true\n' >> "$dir/serverless.yml"
  ( cd "$dir" \
    && env "${SLS_PRINT_ENV[@]}" \
       "${SLS_PLACEHOLDERS[@]}" "${CALLER_VPC_PLACEHOLDERS[@]}" \
       npx --yes serverless@3 print --config serverless.yml --stage dev \
         > "$dir.out" 2> "$dir.err" ) || code=$?
  sls_print_has_no_warning "$dir.err" > "$dir.classified" 2>&1 || classified=$?
  if [ "$code" -ne 0 ] || [ "$classified" -ne 1 ] || \
     ! grep -q 'serverless print resolved the config with a warning.*eilWarningControlUnknown' "$dir.classified"; then
    cat "$dir.out" "$dir.err" "$dir.classified" >&2
    refuse "live print-warning control did not find the planted eilWarningControlUnknown warning (print=$code classifier=$classified); no clean-print verdict"
  fi
  echo "[service-iac]   live print-warning control found eilWarningControlUnknown"
}

# ANCHORED on the `name:` KEY and on end-of-line. Two failures this shape avoids: an
# unanchored grep for the api function also matches `foundry-service-template-dev-apiLambdaFunction`
# and half a dozen CloudFormation resource names, so it would pass on a config that
# declares no function at all; and the resolved name is emitted as
# `    name: foundry-service-template-dev-api`, not as a bare line, so a pattern anchored to line start
# WITHOUT the key matches nothing and reports a real config as declaring no functions.
#
# It asks the reader first, like every arm that parses the print: on a refused print a
# bare grep is red with no line of its own.
sls_declares_api() {
  sls_reads_a_config "$WORK/sls.out" || return 1
  grep -qE "^ *name: ${SERVICE}-dev-api$" "$WORK/sls.out"
}

# ARM 6 REPLACES "the resolved config declares both functions", and the replacement is
# stronger rather than weaker. That arm asserted that a scheduled function named
# `drainOutbox` EXISTED. It did, and it did nothing: its entire body was
#
#   /opt/venv/bin/python -c "from ...handlers.outbox_drain import handle_scheduled_drain"
#
# which IMPORTS the symbol and exits. `handle_scheduled_drain(drain)` takes the bound
# drain callable as a REQUIRED argument and no composition root builds one, so the
# command could not have called it even had it tried.
#
# So the old arm did not merely fail to catch the inert function -- it REQUIRED it.
# Deleting the function turned this gate red. That inversion is the part worth
# recording: the check a reader would credit for proving the drain was deployed was the
# thing holding an import-only Lambda in the config.
#
# This is the estate's "a step whose NAME asserts an action must not have an inert body"
# rule (monorepo `.claude/CLAUDE.md`, `scripts/check_inert_step_claims.py`) on a surface
# that gate cannot reach: it enumerates `run:` steps in the MONOREPO's own
# `.github/workflows`, and a serverless function here is neither a `run:` step nor part
# of that repository's git index.
#
# The property is checked over the RESOLVED config rather than over `serverless.yml`'s
# source text, because `${env:...}` substitution and `serverless` defaults both happen
# between the two.
#
# NOTE HONESTLY WHAT THIS ARM DOES AND DOES NOT MEASURE TODAY. After `drainOutbox` was
# removed, NO function here carries an `image.command` at all, so the production half of
# this arm currently inspects zero commands. A probe that matched nothing is not a
# measured negative, so the arm carries INLINE CONTROLS in both directions -- six cases,
# including the exact argv the deleted `drainOutbox` shipped -- and FAILS if the
# classifier does not flag the offenders or wrongly flags the conforming ones. The arm's
# value is forward-looking: it goes red the day an import-only command comes back.
sls_no_inert_function_command() {
  sls_reads_a_config "$WORK/sls.out" || return 1
  /usr/bin/python3 - "$WORK/sls.out" <<'PYINERT'
import ast
import sys

try:
    import yaml
except ImportError:
    print(
        "PyYAML is not importable, so the resolved config could not be PARSED. This arm "
        "REFUSES rather than falling back to a text scan: a `command:` is a YAML list "
        "whose elements are re-wrapped across lines by the resolver, so a regex over "
        "raw text reads a folded scalar as several unrelated lines.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def command_is_inert(command):
    """True when `command` runs `python -c <code>` and <code> only IMPORTS.

    Parsed with `ast`, never matched with a regex: `import x` inside a string, a
    comment, or a call argument is not a top-level import, and only a parse tells
    them apart. An empty program is inert too -- it is the limit case of this shape.
    A command that is not `python -c` at all (`-m`, a console script, no command)
    is NOT this gate's business and must not be flagged.
    """
    if not command:
        return False
    parts = [str(c) for c in command]
    if "-c" not in parts:
        return False
    index = parts.index("-c")
    if index + 1 >= len(parts):
        return False
    try:
        tree = ast.parse(parts[index + 1])
    except SyntaxError:
        return False
    if not tree.body:
        return True
    return all(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body)


# INLINE CONTROLS, BOTH DIRECTIONS. The first is the argv `drainOutbox` actually
# shipped here. The rest are near misses that must stay quiet, because a false red here
# names a real file and a real function, and the natural response to one is to rewrite
# the CONFIG rather than to doubt the detector.
#
# The module is `example_service`, a name no service owns, ON PURPOSE (bead
# an earlier change). Written as this template's package, `new_service.sh` renamed it
# into every emitted service, which then carried a control asserting a module
# layout it did not have -- memory-service's deploy-path test went red on it. The
# classifier is ast-only and never imports, so the name exercises it identically.
CONTROLS = (
    (True, ["/opt/venv/bin/python", "-c",
            "from example_service.transport.events.outbox_drain import handle_scheduled_drain"]),
    (True, ["python", "-c", "import os"]),
    (False, ["/opt/venv/bin/python", "-c",
             "from example_service.transport.events.outbox_drain import handle_scheduled_drain\n"
             "handle_scheduled_drain(build_drain())"]),
    (False, ["/opt/venv/bin/python", "-m", "example_service.transport.events.outbox_drain"]),
    (False, ["python", "-c", "raise SystemExit(main())"]),
    (False, None),
)
control_failures = [
    f"expected inert={expected} got {command_is_inert(command)} for {command!r}"
    for expected, command in CONTROLS
    if command_is_inert(command) is not expected
]
if control_failures:
    for failure in control_failures:
        print(f"the inert-command classifier is broken: {failure}", file=sys.stderr)
    raise SystemExit(1)

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
functions = (doc or {}).get("functions") or {}

# VISIBILITY. A resolved config with no functions at all would leave the loop below
# with nothing to say, and its silence would be a fact about the probe rather than
# about the config.
if not functions:
    print(
        "the resolved config declares no functions, so this arm never reached one; the "
        "absence it would otherwise report is an artifact of the probe",
        file=sys.stderr,
    )
    raise SystemExit(1)

offenders = []
inspected = 0
for name, spec in sorted(functions.items()):
    command = ((spec or {}).get("image") or {}).get("command")
    if command is None:
        continue
    inspected += 1
    if command_is_inert(command):
        offenders.append(
            f"function {name!r} is deployed with an import-only command "
            f"({command!r}): it imports the symbol and exits, so whatever its name "
            "asserts, it does nothing. Call it, or delete the function"
        )

if offenders:
    for offender in offenders:
        print(offender, file=sys.stderr)
    raise SystemExit(1)

print(
    f"[service-iac]   {len(functions)} resolved function(s), {inspected} with an image "
    f"command, 0 inert; 6 classifier controls held"
)
raise SystemExit(0)
PYINERT
}

arm "serverless print resolves the config" sls_print
arm "the resolved config declares the api function" sls_declares_api
arm "no resolved function is deployed with an import-only command" sls_no_inert_function_command

# --- arm 7: the workflow invokes serverless from the config's own directory ---

workflow_serverless_has_working_directory() {
  /usr/bin/python3 - "$WORKFLOW" "$ROOT_DIR" <<'PYWF'
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "PyYAML is not importable, so this arm cannot read the workflow's structure. "
        "It REFUSES rather than falling back to a text scan: the text version of this "
        "arm reported the JOB NAME 'terraform validate + serverless print' as a "
        "serverless step with no working-directory -- a false red naming a real file "
        "and a real line, which is the direction that gets a correct workflow edited "
        "instead of a broken detector.",
        file=sys.stderr,
    )
    raise SystemExit(2)

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
root = Path(sys.argv[2])

# Only a step's `run` COMMAND counts. Names, comments and job titles are prose
# ABOUT serverless, not invocations of it, and reading structure is the only
# thing that tells the two apart.
seen = 0
offenders = []
misplaced = []
for job_name, job in (doc.get("jobs") or {}).items():
    job_dir = ((job.get("defaults") or {}).get("run") or {}).get("working-directory")
    for step in job.get("steps") or []:
        run = step.get("run") or ""
        if not any(w in run for w in ("serverless deploy", "serverless print", "serverless@3")):
            continue
        seen += 1
        label = f"{job_name}: {step.get('name') or run.splitlines()[0]}"
        step_dir = step.get("working-directory") or job_dir
        if not step_dir:
            offenders.append(label)
        # SET IS NOT ENOUGH. The template moved `iac/` to `deploy/iac/` and this step
        # kept `working-directory: iac`: set, so the presence test passed, and naming a
        # directory that no longer exists, so the deploy could not have run. The claim
        # in this arm's name is "from the config's OWN directory", so it asks for the
        # config there.
        elif not (root / step_dir / "serverless.yml").is_file():
            misplaced.append(f"{label} (working-directory {step_dir!r} holds no serverless.yml)")

# POSITIVE CONTROL. A workflow with no serverless invocation at all leaves this
# arm an empty offender list -- byte-identical to a correct one. A green from a
# probe that matched nothing is not a measured negative.
if seen == 0:
    print(
        "no step in this workflow invokes serverless, so this arm matched nothing and its "
        "silence is an artifact of the probe rather than a property of the workflow",
        file=sys.stderr,
    )
    raise SystemExit(1)

if offenders:
    print(
        "serverless step(s) without a working-directory: " + "; ".join(offenders)
        + " -- `serverless --config <subdir>/serverless.yml` from a parent exits 1 with "
        "'Service configuration is expected to be placed in a root of a service'",
        file=sys.stderr,
    )
    raise SystemExit(1)
if misplaced:
    print(
        "serverless step(s) whose working-directory is not the config's directory: "
        + "; ".join(misplaced),
        file=sys.stderr,
    )
    raise SystemExit(1)
raise SystemExit(0)
PYWF
}

arm "the workflow runs serverless from the config's own directory" \
  workflow_serverless_has_working_directory

# --- verdict -----------------------------------------------------------------


# --- arm 13: no durable resource is declared in the RELEASE root ---------------
#
# The split in arms 1-12 is structural: it proves two roots exist and both validate.
# It does NOT prove anyone kept them apart. This arm is the refusal that makes the
# split load-bearing rather than decorative.
#
# TWO RULES, and the second is the one people forget.
#   1. A durable resource type may not be declared in deploy/iac/app. A release root is
#      replaced wholesale on every deploy; a bucket or a key declared there can be
#      destroyed by a rollback.
#   2. deploy/iac/app may not read the resources root's remote state. HashiCorp's own
#      guidance, and this estate's 2026-09-12 ruling, is to publish a narrow output
#      contract rather than grant consumers full state access -- a consumer reading
#      state couples a release to a state file it does not own.
#
# THE CONTROLS ARE INLINE, NOT A FIXTURE FILE, and that is forced rather than chosen.
# `new_service.sh` enumerates the template with `git ls-files`, so ANY tracked fixture
# under this directory is copied into every emitted service -- where it would then be
# scanned by that service's own production run of this gate and reported as a real
# finding. That is exactly the leak `check_gate_fixture_isolation.py` exists to catch,
# one layer up. Inline cases cannot be emitted, and they still fire in both directions.
no_durable_resource_in_release_root() {
  /usr/bin/python3 - "$ROOT_DIR/deploy/iac/app" "$ROOT_DIR/deploy/iac/resources" <<'PY'
import pathlib
import re
import sys

# Declared, reviewed, and deliberately NOT exhaustive-by-guess: each entry is a type
# whose deletion loses data or identity. An unrecognised type is ALLOWED in the release
# root, because a deny-list that guesses would block ordinary release resources; the
# list grows when a real service needs it, which is a reviewed diff rather than a silent
# default.
DURABLE = {
    "aws_s3_bucket", "aws_kms_key", "aws_kms_alias",
    "aws_db_instance", "aws_rds_cluster", "aws_rds_cluster_instance",
    "aws_dynamodb_table", "aws_sqs_queue", "aws_sns_topic",
    "aws_efs_file_system", "aws_elasticache_cluster", "aws_elasticache_replication_group",
    "aws_secretsmanager_secret", "aws_ecr_repository", "aws_cognito_user_pool",
}

RESOURCE = re.compile(r'^\s*resource\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_-]+)"', re.M)
REMOTE_STATE = re.compile(r'^\s*data\s+"terraform_remote_state"\s+"([A-Za-z0-9_-]+)"', re.M)


def findings(text: str) -> list[str]:
    out = []
    for kind, name in RESOURCE.findall(text):
        if kind in DURABLE:
            out.append(f'durable resource {kind}.{name} declared in the release root')
    for name in REMOTE_STATE.findall(text):
        out.append(f'terraform_remote_state.{name} read from the release root')
    return out


# --- inline controls, both directions, run EVERY time -------------------------
OFFENDERS = [
    ('resource "aws_s3_bucket" "data" {}', 1),
    ('resource "aws_kms_key" "k" {}', 1),
    ('data "terraform_remote_state" "res" {}', 1),
    ('resource "aws_db_instance" "db" {}\nresource "aws_sqs_queue" "q" {}', 2),
]
CONFORMING = [
    'resource "aws_ecs_service" "this" {}',
    'resource "aws_lb_listener_rule" "this" {}',
    'resource "aws_iam_role" "task" {}',
    'resource "aws_cloudwatch_log_group" "this" {}',
    'variable "sqs_queue_arn" { type = string }',
    '# resource "aws_s3_bucket" "commented_out" {}'.replace("# ", "# "),
]
controls = 0
for src, expected in OFFENDERS:
    got = len(findings(src))
    if got != expected:
        print(f"[control] offender not detected as expected: {src!r} -> {got}, want {expected}")
        sys.exit(1)
    controls += 1
for src in CONFORMING:
    got = findings(src)
    if got:
        print(f"[control] conforming input flagged: {src!r} -> {got}")
        sys.exit(1)
    controls += 1

app = pathlib.Path(sys.argv[1])
resources = pathlib.Path(sys.argv[2])

tf = sorted(app.rglob("*.tf"))
if not tf:
    print(f"[refuse] no .tf files under {app}; this arm would pass by scanning nothing")
    sys.exit(1)

problems = []
for path in tf:
    for message in findings(path.read_text(encoding="utf-8")):
        problems.append(f"  {path.name}: {message}")

# The other direction on REAL input: the resources root must actually contain some of
# what the release root is forbidden. Without this, deleting every durable resource from
# the service would leave this arm green while the contract it enforces meant nothing.
res_tf = sorted(resources.rglob("*.tf"))
res_durable = sum(
    1
    for path in res_tf
    for kind, _ in RESOURCE.findall(path.read_text(encoding="utf-8"))
    if kind in DURABLE
)

if problems:
    print(f"{len(problems)} durable-resource violation(s) in the release root:")
    print("\n".join(problems))
    sys.exit(1)

print(
    f"  {len(tf)} release .tf file(s) scanned, 0 durable declarations, 0 remote-state reads; "
    f"{res_durable} durable resource(s) correctly in the resources root; "
    f"{controls} inline control(s) held"
)
PY
}

arm "no durable resource or remote-state read in the release root" no_durable_resource_in_release_root

# --- arm 14: no non-HTTP function competes with the adapter's runtime client --
#
# This kit's Dockerfile ships the Lambda Web Adapter at
# /opt/extensions/lambda-adapter UNCONDITIONALLY, as an EXTENSION the Lambda
# service starts regardless of the function's CMD. It is a runtime CLIENT that
# polls the Runtime API and forwards each invocation over HTTP to a process
# listening on AWS_LWA_PORT. A non-HTTP function's `image.command` therefore has
# exactly two ways to be wrong: a bare `python -m <module>` with no runtime
# client at all runs its `if __name__ == "__main__":` body once against an EMPTY
# event at cold start and the process exits (RIE proof, comms-email b9f99f3: 502
# Runtime.ExitError) -- and R.1's own fix, `python -m awslambdaric
# <module>.handler`, is a SECOND runtime client living beside the adapter; both
# poll `/next` against the same Runtime API, and measured under RIE v1.37 every
# invoke then answers in under a millisecond with a null body, with the real
# handler NEVER dispatched to (confirmed by its side effect -- a migration --
# never landing; the Lambda dispatch change).
#
# THE FIX IS TO ADD NO SECOND CLIENT. LWA's `AWS_LWA_PASS_THROUGH_PATH` forwards
# the raw invocation event, unmodified, as a POST to a path on the app the
# adapter already proxies to -- so a non-HTTP function declares NO
# `image.command` (it runs the same default CMD the HTTP function does) and
# instead sets `AWS_LWA_PASS_THROUGH_PATH` + `FOUNDRY_FUNCTION` in its
# `environment`. `lambda_dispatch.mount_non_http_dispatch` (this template's
# `composition/lambda_dispatch.py`) reads both at app-boot and mounts one route
# that imports and calls the named `<name>_main.handler`. RIE proof (v1.37,
# adapter present, isolated synthetic app): HTTP 200 with a real body, and a
# written marker file proving the handler genuinely ran.
#
# HTTP EXEMPTION IS READ FROM THE FUNCTION'S OWN EVENTS, not from the name
# `api`: the template only ever emits one HTTP-triggered function, but a
# service is free to name it anything, and a name-based exemption would stop
# working silently the day it is renamed.
#
# AN `alb` EVENT IS AN HTTP TRIGGER TOO. The template's function is reached through
# a rule on the shared internal load balancer, and the adapter serves a load
# balancer's request exactly as it serves API Gateway's. Reading only
# `httpApi`/`http` classified that function as non-HTTP the day its ingress became
# private, and demanded a pass-through path from the one function that needs none.
#
# R.2 REDESIGN. The R.1 shape this arm used to require --
# `image.command: [..., -m, awslambdaric, <module>.handler]` -- is ITSELF the
# defect. `awslambdaric` is a SECOND Lambda Runtime API client living beside
# the Lambda Web Adapter extension the Dockerfile installs unconditionally;
# both poll `GET .../runtime/invocation/next` against the same
# `AWS_LAMBDA_RUNTIME_API`. Measured under RIE v1.37 with both present: every
# invoke answers in under a millisecond with a null body, and the real handler
# is never dispatched to (confirmed by the side effect it should have produced
# -- a migration -- never landing). A 200 status alone is not evidence of
# anything here; asserting only that was ARM4's own mistake.
#
# THE CORRECT SHAPE keeps the adapter as the ONLY runtime client: a non-HTTP
# function declares NO `image.command` at all (so it runs the same default CMD
# the HTTP function does) and instead sets two environment variables that
# `lambda_dispatch.mount_non_http_dispatch` reads at app-boot time:
#   AWS_LWA_PASS_THROUGH_PATH: /_lambda/dispatch   (LWA forwards the raw event here)
#   FOUNDRY_FUNCTION: <name>                       (which `<name>_main.handler` to call)
# Measured, RIE v1.37, adapter present, this exact mechanism: HTTP 200 with a
# real body, and a written side-effect marker proving the dispatched code
# actually ran -- see the Lambda dispatch change proof.
#
# NOTE HONESTLY WHAT THIS ARM DOES AND DOES NOT MEASURE TODAY, same as arm 6
# above: this template emits exactly one function (`api`, HTTP), so its own
# resolved config has ZERO non-HTTP functions to inspect. The arm's value here
# is forward-looking and is proved by inline controls, one fixture per clause,
# in both directions -- including the exact shape comms-core/comms-email
# shipped in R.1 (now itself an offender: it still declares image.command).
sls_non_http_function_does_not_compete_with_adapter() {
  sls_reads_a_config "$WORK/sls.out" || return 1
  /usr/bin/python3 - "$WORK/sls.out" <<'PYADAPTER'
import sys

try:
    import yaml
except ImportError:
    print(
        "PyYAML is not importable, so the resolved config could not be PARSED. This arm "
        "REFUSES rather than falling back to a text scan, for the same reason arm 6 does: "
        "a `command:` is a YAML list re-wrapped across lines by the resolver.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def is_http_function(spec):
    """True when this function is invoked over HTTP (a load balancer, or API
    Gateway v1 or v2) -- the shapes the Lambda Web Adapter serves directly, with
    no pass-through needed. Read from the function's OWN `events`, never from a
    hardcoded function name."""
    for event in (spec or {}).get("events") or []:
        if isinstance(event, dict) and ({"alb", "httpApi", "http"} & event.keys()):
            return True
    return False


def non_http_problems(name, spec):
    """Every way a non-HTTP function can fail to dispatch through the adapter
    correctly. Each clause is independent and each is proved by its own
    fixture below, never by adjacency to another clause's fixture."""
    problems = []

    command = ((spec or {}).get("image") or {}).get("command")
    if command:
        problems.append(
            f"function {name!r} is not HTTP-triggered and declares image.command={command!r}. "
            "An explicit command here either runs a SECOND Lambda Runtime API client beside "
            "the Lambda Web Adapter extension -- which the Dockerfile installs unconditionally "
            "-- so the invoke is answered with a null body before the real handler ever runs "
            "(an earlier change ARM4), or it is a bare one-shot with no runtime client at all, "
            "which exits before answering the first invoke (ARM1, 502 Runtime.ExitError). "
            "Drop image.command and declare AWS_LWA_PASS_THROUGH_PATH + FOUNDRY_FUNCTION in "
            "environment instead, so the adapter stays the only runtime client."
        )

    env = (spec or {}).get("environment") or {}
    if not env.get("AWS_LWA_PASS_THROUGH_PATH"):
        problems.append(
            f"function {name!r} is not HTTP-triggered and does not declare "
            "AWS_LWA_PASS_THROUGH_PATH. Without it the adapter treats every invoke as an "
            "API-Gateway-shaped HTTP request instead of forwarding the raw event, so this "
            "function's real payload never reaches the app."
        )
    if not env.get("FOUNDRY_FUNCTION"):
        problems.append(
            f"function {name!r} is not HTTP-triggered and does not declare FOUNDRY_FUNCTION. "
            "Without it the dispatch route has no way to choose which <name>_main.handler to "
            "call for this function."
        )
    return problems


# INLINE CONTROLS, BOTH DIRECTIONS, one fixture per clause, run every time
# regardless of what this service's own config declares today.
CONTROLS = (
    # (label, is_http, spec, expected problem count)
    ("http function, no command, no env -- exempt entirely",
     True, {"events": [{"httpApi": "*"}]}, 0),
    ("load-balancer function, the template's own shape -- exempt entirely",
     True, {"events": [{"alb": {"listenerArn": "arn:aws:elasticloadbalancing:x",
                                "priority": 100,
                                "conditions": {"host": ["svc.example.internal"]}}}]}, 0),
    ("scheduled function only -- an event that is not HTTP stays non-HTTP",
     False, {"events": [{"schedule": "rate(1 minute)"}],
             "environment": {"AWS_LWA_PASS_THROUGH_PATH": "/_lambda/dispatch",
                              "FOUNDRY_FUNCTION": "drain"}}, 0),
    ("non-http, R.1's old awslambdaric command -- now itself an offender",
     False, {"image": {"command": ["/opt/venv/bin/python", "-m", "awslambdaric",
                                    "comms_email.composition.migrate_main.handler"]},
             "environment": {"AWS_LWA_PASS_THROUGH_PATH": "/_lambda/dispatch",
                              "FOUNDRY_FUNCTION": "migrate"}}, 1),
    ("non-http, bare -m command, no pass-through env -- both clauses fire",
     False, {"image": {"command": ["python", "-m", "pkg.module"]}}, 3),
    ("non-http, no command, missing AWS_LWA_PASS_THROUGH_PATH",
     False, {"environment": {"FOUNDRY_FUNCTION": "migrate"}}, 1),
    ("non-http, no command, missing FOUNDRY_FUNCTION",
     False, {"environment": {"AWS_LWA_PASS_THROUGH_PATH": "/_lambda/dispatch"}}, 1),
    ("non-http, correctly wired -- conforming",
     False, {"environment": {"AWS_LWA_PASS_THROUGH_PATH": "/_lambda/dispatch",
                              "FOUNDRY_FUNCTION": "migrate"}}, 0),
    ("http function with a stray command -- arm 6's business, not this arm's",
     True, {"events": [{"http": "GET /x"}],
            "image": {"command": ["python", "-m", "pkg.module"]}}, 0),
)
control_failures = []
for label, expected_http, spec, expected_count in CONTROLS:
    got_http = is_http_function(spec)
    if got_http != expected_http:
        control_failures.append(f"{label}: is_http_function got {got_http}, want {expected_http}")
        continue
    got_problems = [] if got_http else non_http_problems("fixture", spec)
    if len(got_problems) != expected_count:
        control_failures.append(
            f"{label}: got {len(got_problems)} problem(s) {got_problems!r}, want {expected_count}"
        )

if control_failures:
    for failure in control_failures:
        print(f"the adapter-dispatch classifier is broken: {failure}", file=sys.stderr)
    raise SystemExit(1)

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
functions = (doc or {}).get("functions") or {}

if not functions:
    print(
        "the resolved config declares no functions, so this arm never reached one; the "
        "absence it would otherwise report is an artifact of the probe",
        file=sys.stderr,
    )
    raise SystemExit(1)

offenders = []
non_http = 0
for name, spec in sorted(functions.items()):
    if is_http_function(spec):
        continue
    non_http += 1
    offenders.extend(non_http_problems(name, spec))

if offenders:
    for offender in offenders:
        print(offender, file=sys.stderr)
    raise SystemExit(1)

print(
    f"[service-iac]   {len(functions)} resolved function(s), {non_http} non-HTTP, 0 "
    f"adapter-competing, 0 missing pass-through env; 9 classifier controls held"
)
raise SystemExit(0)
PYADAPTER
}

arm "no non-HTTP function competes with the adapter's runtime client" sls_non_http_function_does_not_compete_with_adapter

# --- arms 15-20: a service that calls another private service ------------------
#
# The shared internal load balancer admits HTTPS from the estate's
# caller group, so a service that calls another private service has to carry that
# group. Both targets take it as an opt-in that is OFF by default:
#
#   ECS     `estate_caller_sg_id` in deploy/iac/app, empty by default
#   Lambda  `custom.callerVpc: estate` in serverless.yml, `none` by default
#
# ARMS 17 TO 19 PROVE A DIRECTION, NOT THE COMMITTED VALUE. A service is free to
# commit `estate`, so they force `custom.callerVpc` each way in a COPY and read what
# the framework resolves. Arms 16 and 20 read the committed value: that it is one of
# the two, and that `estate` has its two variables declared.
#
# What the print arms do NOT show is the compiled CloudFormation. That a null
# `provider.vpc` compiles to no `VpcConfig` and no VPC access policy, that a
# function's own `vpc` replaces the provider's field by field, and that a `VpcConfig`
# written under `resources` replaces whatever the switch resolved, are properties of
# the framework, measured with an offline `serverless package` at 3.40.0. The arms
# read the three places the resolved config can say so: `provider.vpc`, each
# function's `vpc`, and `resources`. A plugin that rewrites the compiled stack is
# out of their sight.
#
# What arm 15 does NOT show is that nothing ELSE admits the caller group into the
# service: a `terraform test` reads the resources it names and cannot enumerate the
# rest. `tests/shape/test_private_ingress.py` parses every Terraform file under
# deploy/iac and holds that.

# Each run named here must report `pass`. Named, not counted: `terraform test` in a
# root with no test file prints "Success! 0 passed, 0 failed." at exit 0, and a count
# would also be satisfied by five runs that are not these.
CALLER_GROUP_RUNS=(
  unset_leaves_only_the_services_own_group
  an_eight_digit_id_is_accepted
  a_seventeen_digit_id_is_accepted
  the_bare_prefix_is_refused
  one_hex_digit_is_refused
  a_twelve_digit_id_is_refused
  a_forty_digit_id_is_refused
  a_group_name_is_refused
  a_list_of_groups_is_refused
  set_adds_the_caller_group_beside_the_services_own
  the_caller_group_admits_nothing_into_the_service
)

tf_caller_group_runs_pass() {
  local out="$WORK/tf-test.out" name missing=0
  if ! (cd "$1" && "$TF_BIN" test -no-color > "$out" 2>&1); then
    cat "$out" >&2
    return 1
  fi
  for name in "${CALLER_GROUP_RUNS[@]}"; do
    if ! grep -qF "run \"$name\"... pass" "$out"; then
      echo "[service-iac] terraform test did not report run \"$name\" as pass" >&2
      missing=1
    fi
  done
  [ "$missing" -eq 0 ] || { cat "$out" >&2; return 1; }
}

sls_caller_vpc_is_declared() {
  sls_reads_a_config "$WORK/sls.out" || return 1
  /usr/bin/python3 - "$WORK/sls.out" <<'PYDECLARED'
import sys

import yaml

declared = ((yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("custom") or {}).get(
    "callerVpc"
)
if declared not in ("none", "estate"):
    print(
        f"custom.callerVpc is {declared!r}, not `none` or `estate`. serverless.yml reads "
        "caller_vpc.yml through a fallback, so any other value deploys with NO VPC "
        "attachment and no error: a misspelt `estate` is a caller that times out.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PYDECLARED
}

# Print the config with `custom.callerVpc` forced to $1, in a copy. $2 is `with` or
# `without` the two variables `estate` reads. The return code is the framework's.
#
# The two streams land in $WORK/caller-vpc-$1-$2.out and .err, apart like every
# print here (see sls_print_has_no_warning).
sls_print_caller_vpc() {
  local mode="$1" variables="$2" dir="$WORK/caller-vpc-$1-$2"
  mkdir -p "$dir" || return 2
  cp "$SLS_DIR/serverless.yml" "$SLS_DIR/caller_vpc.yml" "$dir/" || return 2
  /usr/bin/python3 - "$dir/serverless.yml" "$mode" <<'PYFORCE' || return 2
import sys
from pathlib import Path

import yaml


def forced(text, mode):
    """`text` with the value of the top-level `custom.callerVpc` replaced by `mode`,
    or None when there is not exactly one such scalar.

    The value is found in the PARSED node tree and replaced at its own character
    span. A pattern over the line was this step's first shape, and it missed every
    valid spelling it had not been written for: a trailing comment, two spaces after
    the colon, a re-indented `custom:`. Three arms then went red on a correct config.
    """
    root = yaml.compose(text)
    if not isinstance(root, yaml.MappingNode):
        return None
    switches = [
        value
        for key, custom in root.value
        if key.value == "custom" and isinstance(custom, yaml.MappingNode)
        for name, value in custom.value
        if name.value == "callerVpc"
    ]
    if len(switches) != 1 or not isinstance(switches[0], yaml.ScalarNode):
        return None
    start, end = switches[0].start_mark.index, switches[0].end_mark.index
    return text[:start] + mode + text[end:]


# INLINE CONTROLS, BOTH DIRECTIONS, one per spelling. None is "nothing to force".
CONTROLS = (
    ("the template's own spelling",
     "custom:\n  callerVpc: none\n", "estate", "custom:\n  callerVpc: estate\n"),
    ("a trailing comment, kept",
     "custom:\n  callerVpc: estate # calls governance\n", "none",
     "custom:\n  callerVpc: none # calls governance\n"),
    ("two spaces after the colon",
     "custom:\n  callerVpc:  none\n", "estate", "custom:\n  callerVpc:  estate\n"),
    ("a re-indented custom block",
     "custom:\n    callerVpc: none\n", "estate", "custom:\n    callerVpc: estate\n"),
    ("a quoted value",
     'custom:\n  callerVpc: "none"\n', "estate", "custom:\n  callerVpc: estate\n"),
    ("the same key under another parent is left alone",
     "provider:\n  callerVpc: none\ncustom:\n  callerVpc: none\n", "estate",
     "provider:\n  callerVpc: none\ncustom:\n  callerVpc: estate\n"),
    ("no switch at all", "custom:\n  exposure: private\n", "estate", None),
    ("the key only under another parent", "provider:\n  callerVpc: none\n", "estate", None),
    ("a switch that is a mapping", "custom:\n  callerVpc:\n    mode: none\n", "estate", None),
    ("the switch written twice", "custom:\n  callerVpc: none\n  callerVpc: estate\n", "none", None),
)
broken = [
    f"{label}: got {forced(source, mode)!r}, want {expected!r}"
    for label, source, mode, expected in CONTROLS
    if forced(source, mode) != expected
]
if broken:
    for failure in broken:
        print(f"the step that forces custom.callerVpc is broken: {failure}", file=sys.stderr)
    raise SystemExit(1)

path, mode = Path(sys.argv[1]), sys.argv[2]
try:
    result = forced(path.read_text(encoding="utf-8"), mode)
except yaml.YAMLError as error:
    print(f"serverless.yml does not parse as YAML, so nothing was forced: {error}", file=sys.stderr)
    raise SystemExit(1)
# The arm reads a direction it forced. With nothing forced the committed value would
# stay in place and the arm would report on that instead.
if result is None:
    print(
        "serverless.yml has no single scalar `custom.callerVpc` to force: the key is "
        "missing, written twice, or not a plain value",
        file=sys.stderr,
    )
    raise SystemExit(1)
path.write_text(result, encoding="utf-8")
PYFORCE
  local caller=()
  [ "$variables" = "with" ] && caller=("${CALLER_VPC_PLACEHOLDERS[@]}")
  # `${caller[@]+...}`: bash 3.2, still /bin/bash on macOS, treats an empty array as
  # unbound under `set -u`. FORCE_COLOR and SLS_LOG_LEVEL are removed for the reasons
  # sls_print gives.
  ( cd "$dir" \
    && env "${SLS_PRINT_ENV[@]}" -u ESTATE_CALLER_SG_ID -u PRIVATE_SUBNET_IDS \
       "${SLS_PLACEHOLDERS[@]}" ${caller[@]+"${caller[@]}"} \
       npx --yes serverless@3 print --config serverless.yml --stage dev > "$dir.out" 2> "$dir.err" )
}

sls_show_caller_vpc_print() { cat "$WORK/caller-vpc-$1.out" "$WORK/caller-vpc-$1.err" >&2; }

# $1 is the forced value; the resolved config must be what that value means, for the
# provider AND for every function. A function's own `vpc` replaces the provider's
# (framework 3.40.0, compile/functions.js: each of securityGroupIds and subnetIds is
# the function's when it lists one, else the provider's; `vpc: null` takes the
# function out of the VPC), so reading `provider.vpc` alone let `none` be attached
# and `estate` lack the caller group while both arms printed ok.
#
# `resources` is the third place. The framework merges it into the stack AFTER it has
# compiled the functions (3.40.0, measured: `resources.extensions.<LogicalId>` and a
# raw `resources.Resources.<LogicalId>` each replace the generated function's
# `VpcConfig`, under either value of the switch), so a `VpcConfig` written there is an
# attachment the switch does not govern. It is refused under both values.
sls_caller_vpc_resolves() {
  /usr/bin/python3 - "$1" "$2" <<'PYRESOLVED'
import sys

import yaml

CALLER_GROUP = "sg-0c0ffee0c0ffee0c0"
PRIVATE_SUBNETS = {"Fn::Split": [",", "subnet-0aaaaaaaaaaaaaaaa,subnet-0bbbbbbbbbbbbbbbb"]}
FUNCTION_TYPE = "AWS::Lambda::Function"


def attachment_problems(mode, doc):
    """Every way a resolved config is not what `mode` means, as (clause, message).
    Each clause is independent and is proved by its own control below."""
    provider = (doc.get("provider") or {}).get("vpc")
    functions = sorted((doc.get("functions") or {}).items())
    problems = []
    # `resources` is one mapping, or a list of them. A level that is not a mapping is
    # a level this arm cannot read, and what it cannot read it does not pass.
    declared = doc.get("resources")
    for section in declared if isinstance(declared, list) else [declared]:
        if section is None:
            continue
        if not isinstance(section, dict):
            problems.append((
                "resources-unreadable",
                f"custom.callerVpc {mode}: resources is not a mapping this arm can read: "
                f"{section!r}",
            ))
            continue
        for part in ("extensions", "Resources"):
            entries = section.get(part)
            if entries is None:
                continue
            if not isinstance(entries, dict):
                problems.append((
                    "resources-unreadable",
                    f"custom.callerVpc {mode}: resources.{part} is not a mapping this arm "
                    f"can read: {entries!r}",
                ))
                continue
            for name, resource in sorted(entries.items()):
                properties = resource.get("Properties") if isinstance(resource, dict) else resource
                if properties is None:
                    continue
                if not isinstance(properties, dict):
                    problems.append((
                        "resources-unreadable",
                        f"custom.callerVpc {mode}: resources.{part}.{name} is not a resource "
                        f"this arm can read: {properties!r}",
                    ))
                # An extension names a generated resource and carries no Type; a raw
                # resource with no Type is merged over the generated one of that name.
                elif "VpcConfig" in properties and resource.get("Type") in (None, FUNCTION_TYPE):
                    problems.append((
                        "resources-vpc",
                        f"custom.callerVpc {mode}: resources.{part}.{name} sets VpcConfig, "
                        "which replaces the function's attachment in the compiled stack and "
                        f"which the switch does not govern: {properties['VpcConfig']!r}. "
                        "Attach through caller_vpc.yml",
                    ))
    if mode == "none":
        if provider is not None:
            problems.append((
                "provider-vpc",
                f"custom.callerVpc none must resolve to no provider.vpc, got {provider!r}",
            ))
        for name, spec in functions:
            # The key alone counts: even `vpc: null` adds the VPC access policy to the role.
            if "vpc" in (spec or {}):
                problems.append((
                    "function-vpc",
                    f"custom.callerVpc none: function {name!r} carries its own vpc, which the "
                    f"switch does not govern: {spec['vpc']!r}. Attach through caller_vpc.yml",
                ))
        return problems

    attached = provider if isinstance(provider, dict) else {}
    groups = attached.get("securityGroupIds")
    if not isinstance(groups, list) or CALLER_GROUP not in groups:
        problems.append((
            "provider-groups",
            f"custom.callerVpc estate: securityGroupIds does not carry the caller group: {groups!r}",
        ))
    subnets = attached.get("subnetIds")
    if subnets != PRIVATE_SUBNETS:
        problems.append((
            "provider-subnets",
            f"custom.callerVpc estate: subnetIds is not the private subnets, split on commas: {subnets!r}",
        ))
    for name, spec in functions:
        if "vpc" not in (spec or {}):
            continue
        own = spec["vpc"]
        own_groups = own.get("securityGroupIds") if isinstance(own, dict) else None
        if own is None or own is False:
            problems.append((
                "function-opt-out",
                f"custom.callerVpc estate: function {name!r} opts out of the VPC with "
                f"`vpc: {own!r}`, so it deploys without the caller group",
            ))
        elif not isinstance(own, dict):
            problems.append((
                "function-unreadable",
                f"custom.callerVpc estate: function {name!r} has a vpc this arm cannot read: {own!r}",
            ))
        # A function that lists no groups of its own deploys with the provider's.
        elif own_groups and (not isinstance(own_groups, list) or CALLER_GROUP not in own_groups):
            problems.append((
                "function-groups",
                f"custom.callerVpc estate: function {name!r} carries its own securityGroupIds "
                f"without the caller group: {own_groups!r}",
            ))
    return problems


# INLINE CONTROLS, BOTH DIRECTIONS, one per clause. The template declares one function
# with no vpc of its own and no resource but its outputs, so on its own config the
# function clauses and the resources clauses read nothing.
ESTATE = {"securityGroupIds": [CALLER_GROUP], "subnetIds": PRIVATE_SUBNETS}
OTHER_GROUP = {"securityGroupIds": ["sg-0deadbeefdeadbeef0"], "subnetIds": ["subnet-0cccccccccccccccc"]}
OWN_VPC_CONFIG = {"Properties": {"VpcConfig": {"SecurityGroupIds": ["sg-0deadbeefdeadbeef0"],
                                               "SubnetIds": ["subnet-0cccccccccccccccc"]}}}
CONTROLS = (
    ("none: nothing attached",
     "none", {"provider": {"vpc": None}, "functions": {"api": {}}}, []),
    ("none: provider.vpc is an empty mapping",
     "none", {"provider": {"vpc": {}}, "functions": {"api": {}}}, ["provider-vpc"]),
    ("none: a function carries its own vpc",
     "none", {"provider": {}, "functions": {"api": {"vpc": OTHER_GROUP}}}, ["function-vpc"]),
    ("none: a function carries `vpc: null`",
     "none", {"provider": {}, "functions": {"api": {"vpc": None}}}, ["function-vpc"]),
    ("estate: the template's shape",
     "estate", {"provider": {"vpc": ESTATE}, "functions": {"api": {}}}, []),
    ("estate: provider.vpc is not a mapping",
     "estate", {"provider": {"vpc": False}, "functions": {"api": {}}},
     ["provider-groups", "provider-subnets"]),
    ("estate: the provider's groups lack the caller group",
     "estate", {"provider": {"vpc": {**ESTATE, "securityGroupIds": ["sg-0deadbeefdeadbeef0"]}},
                "functions": {"api": {}}}, ["provider-groups"]),
    ("estate: the provider's subnets are not the split list",
     "estate", {"provider": {"vpc": {**ESTATE, "subnetIds": ["subnet-0aaaaaaaaaaaaaaaa"]}},
                "functions": {"api": {}}}, ["provider-subnets"]),
    ("estate: a function's own groups lack the caller group",
     "estate", {"provider": {"vpc": ESTATE}, "functions": {"api": {"vpc": OTHER_GROUP}}},
     ["function-groups"]),
    ("estate: a function's own groups hold the caller group",
     "estate", {"provider": {"vpc": ESTATE},
                "functions": {"api": {"vpc": {"securityGroupIds": ["sg-0deadbeefdeadbeef0", CALLER_GROUP],
                                              "subnetIds": ["subnet-0cccccccccccccccc"]}}}}, []),
    ("estate: a function overrides only its subnets and keeps the provider's groups",
     "estate", {"provider": {"vpc": ESTATE},
                "functions": {"api": {"vpc": {"subnetIds": ["subnet-0cccccccccccccccc"]}}}}, []),
    ("estate: a function opts out with `vpc: null`",
     "estate", {"provider": {"vpc": ESTATE}, "functions": {"api": {"vpc": None}}},
     ["function-opt-out"]),
    ("estate: a function's vpc is not a mapping",
     "estate", {"provider": {"vpc": ESTATE}, "functions": {"api": {"vpc": "${file(./vpc.yml)}"}}},
     ["function-unreadable"]),
    ("none: an extension gives the function a VpcConfig",
     "none", {"provider": {}, "functions": {"api": {}},
              "resources": {"extensions": {"ApiLambdaFunction": OWN_VPC_CONFIG}}}, ["resources-vpc"]),
    ("estate: an extension replaces the function's VpcConfig",
     "estate", {"provider": {"vpc": ESTATE}, "functions": {"api": {}},
                "resources": {"extensions": {"ApiLambdaFunction": OWN_VPC_CONFIG}}}, ["resources-vpc"]),
    ("none: a raw resource under the generated name, with no Type",
     "none", {"provider": {}, "functions": {"api": {}},
              "resources": {"Resources": {"ApiLambdaFunction": OWN_VPC_CONFIG}}}, ["resources-vpc"]),
    ("none: a raw function resource, in a list of resource sections",
     "none", {"provider": {}, "functions": {"api": {}},
              "resources": [{"Outputs": {}},
                            {"Resources": {"Extra": {"Type": FUNCTION_TYPE, **OWN_VPC_CONFIG}}}]},
     ["resources-vpc"]),
    ("none: an extension that sets no VpcConfig",
     "none", {"provider": {}, "functions": {"api": {}},
              "resources": {"extensions": {"ApiLambdaFunction": {"Properties": {"Timeout": 5}}},
                            "Outputs": {}}}, []),
    ("estate: a VpcConfig on a resource that is not a function",
     "estate", {"provider": {"vpc": ESTATE}, "functions": {"api": {}},
                "resources": {"Resources": {"Build": {"Type": "AWS::CodeBuild::Project",
                                                      **OWN_VPC_CONFIG}}}}, []),
    ("none: an extension with no Properties",
     "none", {"provider": {}, "functions": {"api": {}},
              "resources": {"extensions": {"ApiLambdaFunction": {"DependsOn": ["Queue"]}}}}, []),
    ("none: resources is not a mapping",
     "none", {"provider": {}, "functions": {"api": {}}, "resources": "${file(./resources.yml)}"},
     ["resources-unreadable"]),
    ("estate: the extensions are a list",
     "estate", {"provider": {"vpc": ESTATE}, "functions": {"api": {}},
                "resources": {"extensions": [{"ApiLambdaFunction": OWN_VPC_CONFIG}]}},
     ["resources-unreadable"]),
    ("none: an extension is not a mapping",
     "none", {"provider": {}, "functions": {"api": {}},
              "resources": {"extensions": {"ApiLambdaFunction": "${file(./vpc.yml)}"}}},
     ["resources-unreadable"]),
    ("none: a raw resource's Properties is not a mapping",
     "none", {"provider": {}, "functions": {"api": {}},
              "resources": {"Resources": {"ApiLambdaFunction": {"Properties": "${file(./vpc.yml)}"}}}},
     ["resources-unreadable"]),
)
broken = [
    f"{label}: got {[clause for clause, _ in attachment_problems(mode, doc)]}, want {expected}"
    for label, mode, doc, expected in CONTROLS
    if [clause for clause, _ in attachment_problems(mode, doc)] != expected
]
if broken:
    for failure in broken:
        print(f"the caller-VPC classifier is broken: {failure}", file=sys.stderr)
    raise SystemExit(1)

mode = sys.argv[1]
doc = yaml.safe_load(open(sys.argv[2], encoding="utf-8")) or {}

# VISIBILITY. "No vpc" is the pass condition of one direction, so first prove this is
# the resolved config and that the forced value reached the resolver.
if not doc.get("functions") or (doc.get("custom") or {}).get("callerVpc") != mode:
    print(
        f"the output is not a resolved config with custom.callerVpc {mode!r}; this arm "
        "would be reporting on something else",
        file=sys.stderr,
    )
    raise SystemExit(1)

own_vpc = sum(1 for spec in doc["functions"].values() if "vpc" in (spec or {}))
problems = attachment_problems(mode, doc)
for _, message in problems:
    print(message, file=sys.stderr)
if problems:
    raise SystemExit(1)
print(
    f"[service-iac]   custom.callerVpc {mode}: {len(doc['functions'])} resolved function(s), "
    f"{own_vpc} with a vpc of its own; {len(CONTROLS)} controls held"
)
raise SystemExit(0)
PYRESOLVED
}

sls_caller_vpc_none_attaches_nothing() {
  sls_print_caller_vpc none without || { sls_show_caller_vpc_print none-without; return 1; }
  sls_caller_vpc_resolves none "$WORK/caller-vpc-none-without.out"
}

sls_caller_vpc_estate_attaches_the_caller_group() {
  sls_print_caller_vpc estate with || { sls_show_caller_vpc_print estate-with; return 1; }
  # A warning about the estate attachment is on this print alone while the committed
  # switch is `none`. The forced-`none` print has no such case: its `provider.vpc` is
  # null or it is that arm's finding, and any other warning is the committed print's.
  sls_caller_vpc_resolves estate "$WORK/caller-vpc-estate-with.out" \
    && sls_print_has_no_warning "$WORK/caller-vpc-estate-with.err"
}

# `estate` with neither variable set must stop in the framework's variable resolver,
# naming BOTH properties. Each is checked: a default added to one variable would leave
# the other refusing, and the exit code alone would not move.
sls_caller_vpc_estate_needs_its_variables() {
  local out="$WORK/caller-vpc-estate-without.out" code=0 property
  sls_print_caller_vpc estate without || code=$?
  if [ "$code" -ne 1 ]; then
    echo "[service-iac] expected the framework to refuse (exit 1), got exit $code" >&2
    sls_show_caller_vpc_print estate-without
    return 1
  fi
  for property in "provider.vpc.securityGroupIds.0" "provider.vpc.subnetIds.Fn::Split.1"; do
    if ! grep -qF "Cannot resolve variable at \"$property\"" "$out"; then
      echo "[service-iac] the refusal does not name $property" >&2
      sls_show_caller_vpc_print estate-without
      return 1
    fi
  done
}

# `estate` reads two variables from caller_vpc.yml, and `service-kit/ci/deploy_env.py`
# looks for `${env:NAME}` in serverless.yml only. So with `estate` committed and
# neither declared in deploy/deploy_env.json, its coverage check passes, the image is
# pushed, and the framework refuses at `serverless deploy`. This arm moves that
# refusal to before the build. The workflow is a kit placement, so deploy_env.json is
# the only place a service can supply the two values.
#
# The template commits `none`, so on its own config this arm has nothing to demand;
# the five inline controls are what show it can.
sls_caller_vpc_estate_declares_its_variables() {
  sls_reads_a_config "$WORK/sls.out" || return 1
  /usr/bin/python3 - "$WORK/sls.out" "$ROOT_DIR/deploy/deploy_env.json" <<'PYDECLARES'
import json
import sys

import yaml

REQUIRED = ("ESTATE_CALLER_SG_ID", "PRIVATE_SUBNET_IDS")


def undeclared(switch, variables):
    """The names `estate` reads that deploy_env.json does not declare. None when the
    switch is not `estate`: caller_vpc.yml is then never read."""
    if switch != "estate":
        return []
    return [name for name in REQUIRED if name not in variables]


SSM = {"ssm": "/example/{stage}/value"}
CONTROLS = (
    ("none, nothing declared", "none", {}, []),
    ("estate, nothing declared", "estate", {}, ["ESTATE_CALLER_SG_ID", "PRIVATE_SUBNET_IDS"]),
    ("estate, only the group declared", "estate", {"ESTATE_CALLER_SG_ID": SSM}, ["PRIVATE_SUBNET_IDS"]),
    ("estate, only the subnets declared", "estate", {"PRIVATE_SUBNET_IDS": SSM}, ["ESTATE_CALLER_SG_ID"]),
    ("estate, both declared", "estate", {"ESTATE_CALLER_SG_ID": SSM, "PRIVATE_SUBNET_IDS": SSM}, []),
)
broken = [
    f"{label}: got {undeclared(switch, variables)}, want {expected}"
    for label, switch, variables, expected in CONTROLS
    if undeclared(switch, variables) != expected
]
if broken:
    for failure in broken:
        print(f"the declared-variables check is broken: {failure}", file=sys.stderr)
    raise SystemExit(1)

switch = ((yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("custom") or {}).get(
    "callerVpc"
)
try:
    spec = json.load(open(sys.argv[2], encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    print(f"deploy/deploy_env.json could not be read: {error}", file=sys.stderr)
    raise SystemExit(1)
variables = spec.get("variables") if isinstance(spec, dict) else None
if not isinstance(variables, dict):
    print("deploy/deploy_env.json has no 'variables' object", file=sys.stderr)
    raise SystemExit(1)

missing = undeclared(switch, variables)
if missing:
    print(
        f"custom.callerVpc is estate and deploy/deploy_env.json does not declare "
        f"{', '.join(missing)}. caller_vpc.yml reads both with no default and the deploy "
        "job's own check reads serverless.yml only, so the framework would refuse at "
        "`serverless deploy`, after the image is pushed. Declare them in deploy_env.json.",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(
    f"[service-iac]   custom.callerVpc is {switch!r}; deploy_env.json declares "
    f"{len(variables)} variable(s); {len(CONTROLS)} controls held"
)
raise SystemExit(0)
PYDECLARES
}

arm "[app] terraform test: the tasks carry the estate's caller group only when it is named" \
  tf_caller_group_runs_pass "$WORK/app"
arm "custom.callerVpc is none or estate" sls_caller_vpc_is_declared
arm "custom.callerVpc none attaches no function to a VPC" sls_caller_vpc_none_attaches_nothing
arm "custom.callerVpc estate attaches the caller group and the private subnets, and no function drops the group" \
  sls_caller_vpc_estate_attaches_the_caller_group
arm "custom.callerVpc estate without its two variables is refused" \
  sls_caller_vpc_estate_needs_its_variables
arm "custom.callerVpc estate has its two variables declared in deploy/deploy_env.json" \
  sls_caller_vpc_estate_declares_its_variables

if [ "$RAN" -lt "$EXPECTED_ARMS" ]; then
  echo "[service-iac] REFUSED: only $RAN arm(s) reached the accumulator; expected $EXPECTED_ARMS" >&2
  exit 2
fi

if [ "$FAILED" -gt 0 ]; then
  echo "[service-iac] FAIL: $FAILED of $RAN arm(s) red; all $RAN were executed" >&2
  exit 1
fi

echo "[service-iac] PASS: all $RAN arm(s) executed and green ($TF_BIN + serverless@3)"
