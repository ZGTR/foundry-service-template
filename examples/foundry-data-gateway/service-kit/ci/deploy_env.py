"""Deploy configuration from SSM and Secrets Manager, never from a plaintext step value.

    python3 service-kit/ci/deploy_env.py check <stage>
    python3 service-kit/ci/deploy_env.py exec <stage> -- <command> [args...]
    (either takes --spec deploy/deploy_env.json --serverless deploy/iac/serverless.yml)

Kit-owned for the same reason as `smoke.py`: `.github/workflows/ci.yml` is a kit
placement, byte-identical in every service apart from the service name, so it cannot
name one service's variables. memory-service's `serverless.yml` needs DATABASE_URL,
AUTH_JWKS_URI and AUTH_ISSUER with no default; the template's needs none of them.
Which variables a service needs, and where each one lives, are facts about that
service, so they live in `deploy/deploy_env.json`, service-owned like the rest of
`deploy/`:

    {
      "variables": {
        "DATABASE_URL":  {"secretsmanager": "memory-service/{stage}/database-url"},
        "AUTH_JWKS_URI": {"ssm": "/memory-service/{stage}/auth-jwks-uri"}
      }
    }

Each variable names exactly one source. `{stage}` is replaced by the stage argument.
A missing or malformed declaration is a REFUSAL, never an empty one: a service that
needs nothing ships `{"variables": {}}` and says so.

An id may also name an EARLIER variable, `{NAME}`, and is resolved in declaration
order. A product-neutral service cannot write its deployment
root's prefix as a literal, so it declares one variable and builds the rest on it:

    "COMMS_DEPLOYMENT":   {"ssm": "/comms/{stage}/deployment"},
    "COMMS_EVENTS_QUEUE": {"ssm": "/{COMMS_DEPLOYMENT}/core/events-queue-arn"}

Only an `ssm` variable may be named, and only a plain String parameter whose value is
an id segment (letters, digits, `_ . - /`). A secret never becomes part of an id: ids
reach argv, CloudTrail and AWS error text. A reference to a later or undeclared
variable, or to any other `{...}`, is refused before anything is read, and a variable
whose reference did not resolve is refused without being read. Messages name the id
as declared, with `{NAME}` still in it, and each value is masked the moment it
resolves, so an error that quotes a resource ARN is masked too.

Two variables have a SHAPE the kit checks, because `deploy/iac/caller_vpc.yml` consumes
them as written: `ESTATE_CALLER_SG_ID` goes into SecurityGroupIds
untouched and `PRIVATE_SUBNET_IDS` is split on `,` with no trim. Unchecked, an empty
group compiled to `[""]`, a list written `subnet-a, subnet-b` to an id with a leading
space, and a parameter holding a group NAME to a group that does not exist -- each
refused by CloudFormation or Lambda only at deploy, after the image push. So a value
under either name that is not `sg-`/`subnet-` and 8 or 17 lower-case hex digits (the
subnets comma-separated, no spaces) is refused here, whether it was read from its
declared source or set by the step. The message names the shape, never the value.

`check` runs BEFORE the image is pushed. It reads every declared source and refuses,
naming every variable that did not resolve, so an absent parameter stops the job
before anything reaches ECR or Lambda. `exec` wraps the deploy and the rollback: it
resolves the same sources, confirms that every `${env:NAME}` in serverless.yml that
carries no default is either declared here or already set and non-empty in the step
environment, and then replaces itself with the command, whose environment is the
only place the values ever exist. Nothing is written to disk or to $GITHUB_ENV.

Every resolved value is registered with `::add-mask::` before anything else runs, one
line at a time because the runner masks per line. Values are never printed.

A declared variable that is ALSO set in the step environment is refused rather than
overridden: two sources for one value means somebody put it in the workflow as plain
text, which is the thing this script exists to prevent.

EXIT CODES, the vocabulary of `service-kit/gates/gate_exit_codes.py`:

    0  every declared variable resolved (`check`); `exec` returns the command's code
    2  refusal -- no or malformed declaration, a bad stage, a source that could not be
       read or read empty, a value not in the shape the kit reads it in, an uncovered
       serverless.yml variable, or a declared variable shadowed by a step value.
       Nothing is deployed.

Stdlib only, because the deploy job installs nothing. Reads through the `aws` CLI the
job already has, with the credentials `configure-aws-credentials` set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC = ROOT / "deploy" / "deploy_env.json"
DEFAULT_SERVERLESS = ROOT / "deploy" / "iac" / "serverless.yml"
REFUSAL = 2

SOURCES = ("ssm", "secretsmanager")
NAME = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")
STAGE = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")
#: Every `{...}` in an id: `{stage}` or `{NAME}` of an earlier variable, nothing else.
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
#: What a referenced value may be, since it becomes part of another id.
ID_SEGMENT = re.compile(r"\A[A-Za-z0-9_./-]+\Z")
#: `${env:NAME}` with no `, default` -- the serverless variable grammar, read per line
#: so a commented-out reference is not demanded.
REQUIRED_ENV = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
#: An EC2 resource id's suffix: 8 hex digits for the old form, 17 for the current one.
_EC2_SUFFIX = r"(?:[0-9a-f]{8}|[0-9a-f]{17})"
#: {variable: (pattern, what it must be)} for the values the kit itself consumes in a
#: fixed shape: `deploy/iac/caller_vpc.yml` reads both, as written.
SHAPES = {
    "ESTATE_CALLER_SG_ID": (
        re.compile(rf"\Asg-{_EC2_SUFFIX}\Z"),
        "one security group id, sg- and 8 or 17 lower-case hex digits",
    ),
    "PRIVATE_SUBNET_IDS": (
        re.compile(rf"\Asubnet-{_EC2_SUFFIX}(?:,subnet-{_EC2_SUFFIX})*\Z"),
        "subnet ids (subnet- and 8 or 17 lower-case hex digits), comma-separated with "
        "no spaces",
    ),
}


class Refusal(Exception):
    """Anything that must stop the deploy before it starts."""


def load_spec(path: Path, stage: str) -> dict[str, tuple[str, str]]:
    """Return {variable: (source, id)}, in declaration order, `{stage}` replaced and any
    `{NAME}` of an earlier `ssm` variable left for `resolve`."""
    if not STAGE.match(stage):
        raise Refusal(f"stage {stage!r} is not a lower-case stage name")
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise Refusal(f"no deploy configuration declaration at {path}") from exc
    except json.JSONDecodeError as exc:
        raise Refusal(f"{path} is not valid JSON: {exc}") from exc
    variables = raw.get("variables") if isinstance(raw, dict) else None
    if not isinstance(variables, dict):
        raise Refusal(f"{path} has no 'variables' object")
    resolved: dict[str, tuple[str, str]] = {}
    for name, source in variables.items():
        if not NAME.match(name):
            raise Refusal(f"{path}: {name!r} is not an environment variable name")
        keys = sorted(source) if isinstance(source, dict) else []
        if len(keys) != 1 or keys[0] not in SOURCES:
            raise Refusal(f"{path}: {name} must name exactly one of {SOURCES}, got {keys}")
        ident = source[keys[0]]
        if not isinstance(ident, str) or not ident or any(c.isspace() for c in ident):
            raise Refusal(f"{path}: {name} has no usable {keys[0]} id")
        for ref in references(ident):
            if ref not in resolved:
                raise Refusal(
                    f"{path}: {name} names {{{ref}}}, which is not a variable declared "
                    "before it"
                )
            if resolved[ref][0] != "ssm":
                raise Refusal(
                    f"{path}: {name} names {{{ref}}}, a secretsmanager variable; a secret "
                    "never becomes part of an id"
                )
        resolved[name] = (keys[0], ident.replace("{stage}", stage))
    return resolved


def references(ident: str) -> list[str]:
    """The earlier variables an id names; refuses any `{...}` that is neither
    `{stage}` nor a variable name, and a lone brace."""
    names = [token for token in PLACEHOLDER.findall(ident) if token != "stage"]
    bad = [token for token in names if not NAME.match(token)]
    if bad or "{" in PLACEHOLDER.sub("", ident) or "}" in PLACEHOLDER.sub("", ident):
        raise Refusal(f"id {ident!r} has a placeholder other than {{stage}} or {{NAME}}")
    return names


def required_by(serverless: Path) -> list[str]:
    """Every `${env:NAME}` serverless.yml resolves with no default."""
    try:
        lines = serverless.read_text().splitlines()
    except FileNotFoundError as exc:
        raise Refusal(f"no serverless config at {serverless}") from exc
    found = {
        name
        for line in lines
        if not line.lstrip().startswith("#")
        for name in REQUIRED_ENV.findall(line)
    }
    return sorted(found)


def _aws(source: str, ident: str) -> subprocess.CompletedProcess[str]:
    if source == "ssm":
        argv = ["aws", "ssm", "get-parameter", "--name", ident, "--with-decryption"]
    else:
        argv = ["aws", "secretsmanager", "get-secret-value", "--secret-id", ident]
    return subprocess.run([*argv, "--output", "json"], capture_output=True, text=True)


def _value(source: str, answer: dict) -> object:
    if source == "ssm":
        return (answer.get("Parameter") or {}).get("Value")
    return answer.get("SecretString")


def _substituted(ident: str, values: dict[str, str]) -> str | None:
    """`ident` with each `{NAME}` replaced, or None when a named variable did not resolve."""
    names = references(ident)
    if any(name not in values for name in names):
        return None
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), ident)


def resolve(spec: dict[str, tuple[str, str]]) -> dict[str, str]:
    """Read every source in declaration order, masking each value as it resolves;
    refuse naming EVERY variable that did not resolve."""
    referenced = {ref for _, ident in spec.values() for ref in references(ident)}
    values: dict[str, str] = {}
    problems: list[str] = []
    for name, (source, declared) in spec.items():
        ident = _substituted(declared, values)
        if ident is None:
            missing = [ref for ref in references(declared) if ref not in values]
            problems.append(
                f"{name}: {source} {declared} names {', '.join(missing)}, which did not "
                "resolve, so it was not read"
            )
            continue
        try:
            reply = _aws(source, ident)
        except FileNotFoundError:
            raise Refusal("the aws CLI is not on PATH, so no source can be read") from None
        if reply.returncode != 0:
            first = (reply.stderr.strip().splitlines() or ["no output"])[0]
            problems.append(f"{name}: {source} {declared} could not be read ({first})")
            continue
        try:
            answer = json.loads(reply.stdout)
        except json.JSONDecodeError:
            answer = {}
        value = _value(source, answer)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{name}: {source} {declared} read empty or not as a string")
            continue
        mask({name: value})
        wrong = misshapen(name, value)
        if wrong:
            problems.append(f"{name}: {source} {declared} {wrong}")
            continue
        if name in referenced:
            kind = (answer.get("Parameter") or {}).get("Type", "String")
            if kind != "String":
                problems.append(
                    f"{name}: another id names it, and ssm {declared} is a {kind}; only a "
                    "plain String may become part of an id"
                )
                continue
            if not ID_SEGMENT.match(value):
                problems.append(
                    f"{name}: another id names it, and its value is not an id segment "
                    "(letters, digits, _ . - /)"
                )
                continue
        values[name] = value
    if problems:
        raise Refusal("deploy configuration did not resolve: " + "; ".join(problems))
    return values


def misshapen(name: str, value: str) -> str | None:
    """Why `value` is not the shape the kit consumes `name` in, or None when it is (or
    when the kit consumes `name` in no fixed shape). Never quotes the value."""
    if name not in SHAPES:
        return None
    pattern, shape = SHAPES[name]
    return None if pattern.match(value) else f"is not {shape}"


def mask(values: dict[str, str]) -> None:
    """Register each non-blank line of each value as a secret with the Actions runner.

    The runner UNESCAPES a command's data before using it (`%25` -> `%`, `%0D` -> CR,
    `%0A` -> LF), so a value is escaped the same way first. Unescaped, a password
    holding `%25` would register as one holding `%` and never be masked."""
    for value in values.values():
        for line in value.splitlines():
            if line.strip():
                print(f"::add-mask::{_command_data(line)}", flush=True)


def _command_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def check_coverage(required: list[str], declared: dict, environ: dict[str, str]) -> None:
    shadowed = sorted(name for name in declared if name in environ)
    if shadowed:
        raise Refusal(
            "declared in deploy_env.json AND set as a step value, so one of them is "
            f"plain text in the workflow: {', '.join(shadowed)}"
        )
    uncovered = [name for name in required if name not in declared and not environ.get(name)]
    if uncovered:
        raise Refusal(
            "serverless.yml needs these with no default, and neither deploy_env.json "
            f"declares them nor the step sets them: {', '.join(uncovered)}"
        )


def check_step_shapes(declared: dict, environ: dict[str, str]) -> None:
    """Refuse a step-set value the kit consumes in a fixed shape that is not in it. A
    declared one is judged as it resolves, in `resolve`."""
    stepped = []
    for name in SHAPES:
        wrong = misshapen(name, environ[name]) if name in environ else None
        if wrong and name not in declared:
            stepped.append(f"{name} {wrong}")
    if stepped:
        raise Refusal(f"a step value is not the shape the kit reads it in: {'; '.join(stepped)}")


def summary(spec: dict[str, tuple[str, str]]) -> str:
    named = ", ".join(f"{name} from {source}" for name, (source, _) in spec.items())
    head = f"deploy_env: {len(spec)} declared variable(s) resolved"
    return f"{head} ({named})" if named else head


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("check", "exec"))
    parser.add_argument("stage")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--serverless", type=Path, default=DEFAULT_SERVERLESS)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    # CI tooling, not a service package: the step environment IS this script's input
    # (what the deploy step already sets) and its output (what the command receives).
    # Read once, here, rather than exempting the kit directory in every service's
    # pyproject.
    environ = dict(os.environ)  # noqa: TID251
    try:
        if args.mode == "exec" and not command:
            raise Refusal("exec needs a command after --")
        if args.mode == "check" and command:
            raise Refusal("check takes no command")
        spec = load_spec(args.spec, args.stage)
        check_step_shapes(spec, environ)
        if args.mode == "exec":
            check_coverage(required_by(args.serverless), spec, environ)
        values = resolve(spec)
    except Refusal as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return REFUSAL
    print(summary(spec), flush=True)
    if args.mode == "check":
        return 0
    try:
        os.execvpe(command[0], command, {**environ, **values})
    except OSError as exc:
        print(f"REFUSED: could not run {command[0]!r}: {exc.strerror}", file=sys.stderr)
    return REFUSAL


if __name__ == "__main__":
    sys.exit(main())
