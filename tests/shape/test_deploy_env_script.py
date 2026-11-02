"""`service-kit/ci/deploy_env.py` reads deploy configuration from SSM and Secrets Manager.

memory-service's serverless.yml resolves DATABASE_URL, AUTH_JWKS_URI and AUTH_ISSUER
with no default, and the deploy step never set them. These arms run the
real script with a stub `aws` that is the only program on PATH. The stub answers from a
JSON store standing in for Parameter Store and Secrets Manager, and logs every call, so
an absent source, a value that leaks into output, or a command that runs without its
configuration is visible as state rather than inferred from a log line.

No AWS call is made. Whether the parameters exist in a real account, and whether the
deploy role may read them, is the production arm and is not claimed here.

Every arm here reads a declaration and a serverless config it wrote itself, under that
stub. None reads `deploy/`, which belongs to the service: the arms over the template's
own `deploy/` files are in `test_template_deploy_declaration.py`, which a service is not
given.

The stub's directory is the whole PATH of a run, and the stub is a `/bin/sh` file that
starts the interpreter by quoted path. It was a Python file whose first line named the
interpreter, behind which the inherited PATH was left: a first line is cut at its first
space, so under an interpreter whose path holds one the stub could not be started and the
run went on to the next `aws` on PATH, which on a developer's machine is the real CLI.
The last two tests hold the two halves.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "service-kit" / "ci" / "deploy_env.py"

SECRET = "mysql+pymysql://svc:s3cr3t-pw@db.internal:3306/svc"
JWKS = "https://issuer.example/.well-known/jwks.json"
SPEC = {
    "variables": {
        "DATABASE_URL": {"secretsmanager": "svc/{stage}/database-url"},
        "AUTH_JWKS_URI": {"ssm": "/svc/{stage}/auth-jwks-uri"},
    }
}
STORE = {"svc/dev/database-url": SECRET, "/svc/dev/auth-jwks-uri": JWKS}
SERVERLESS = """\
provider:
  environment:
    IMAGE: ${env:IMAGE_URI}
    DATABASE_URL: ${env:DATABASE_URL}
    AUTH_JWKS_URI: ${env:AWS_REGION, "eu-west-1"}${env:AUTH_JWKS_URI}
    REGION: ${env:AWS_REGION, "eu-west-1"}
    # LEGACY: ${env:NOT_REQUIRED_BECAUSE_COMMENTED}
"""

#: The two values `deploy/iac/caller_vpc.yml` consumes as written.
CALLER_VPC = ("ESTATE_CALLER_SG_ID", "PRIVATE_SUBNET_IDS")
CALLER_SPEC = {
    "variables": {
        "ESTATE_CALLER_SG_ID": {"ssm": "/foundry/{stage}/network/estate-caller-sg-id"},
        "PRIVATE_SUBNET_IDS": {"ssm": "/foundry/{stage}/network/private-subnet-ids"},
    }
}
GROUP = "sg-0c0ffee0c0ffee0c0"
SUBNETS = "subnet-0aaaaaaaaaaaaaaaa,subnet-0bbbbbbbbbbbbbbbb"


def caller_store(group: str = GROUP, subnets: str = SUBNETS) -> dict[str, str]:
    return {
        "/foundry/dev/network/estate-caller-sg-id": group,
        "/foundry/dev/network/private-subnet-ids": subnets,
    }


STUB_AWS = """\
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write("aws " + " ".join(args) + "\\n")
if os.environ.get("STUB_AWS_ERROR"):
    print(os.environ["STUB_AWS_ERROR"], file=sys.stderr); sys.exit(254)
store = json.load(open(os.environ["STUB_STORE"]))
if args[:2] == ["ssm", "get-parameter"]:
    ident = args[args.index("--name") + 1]
    if ident not in store:
        print("An error occurred (ParameterNotFound) when calling the GetParameter operation",
              file=sys.stderr); sys.exit(254)
    entry = store[ident]
    kind, value = (entry["Type"], entry["Value"]) if isinstance(entry, dict) else ("String", entry)
    print(json.dumps({"Parameter": {"Name": ident, "Type": kind, "Value": value}})); sys.exit(0)
if args[:2] == ["secretsmanager", "get-secret-value"]:
    ident = args[args.index("--secret-id") + 1]
    if ident not in store:
        print("An error occurred (ResourceNotFoundException) when calling the "
              "GetSecretValue operation", file=sys.stderr); sys.exit(254)
    print(json.dumps({"Name": ident, "SecretString": store[ident]})); sys.exit(0)
print("stub aws: unexpected call " + " ".join(args), file=sys.stderr); sys.exit(99)
"""


def write_stub(bin_dir: Path, python: str = sys.executable) -> Path:
    """Write the stub `aws` into `bin_dir`: a `/bin/sh` file that starts `python`, by
    quoted path, on the stub's program beside the directory."""
    program = bin_dir.parent / "stub_aws.py"
    program.write_text(STUB_AWS)
    aws = bin_dir / "aws"
    aws.write_text(f'#!/bin/sh\nexec {shlex.quote(python)} {shlex.quote(str(program))} "$@"\n')
    aws.chmod(0o755)
    return aws


#: The command `exec` hands off to: records the environment it was given.
CHILD = [
    sys.executable,
    "-c",
    (
        "import json, os, sys; "
        "json.dump(dict(os.environ), open(os.environ['CHILD_ENV'], 'w')); "
        "sys.exit(int(os.environ.get('CHILD_EXIT', '0')))"
    ),
]


@dataclass
class Stubs:
    tmp: Path

    def write(self, spec=SPEC, store=STORE, serverless=SERVERLESS) -> None:
        (self.tmp / "deploy_env.json").write_text(
            spec if isinstance(spec, str) else json.dumps(spec)
        )
        (self.tmp / "store.json").write_text(json.dumps(store))
        (self.tmp / "serverless.yml").write_text(serverless)

    def calls(self) -> list[str]:
        log = self.tmp / "aws.log"
        return log.read_text().splitlines() if log.is_file() else []

    def child_env(self) -> dict[str, str] | None:
        path = self.tmp / "child.json"
        return json.loads(path.read_text()) if path.is_file() else None

    def run(
        self,
        mode: str,
        *rest: str,
        stage: str = "dev",
        inherited: Mapping[str, str] | None = None,
        **env: str,
    ):
        """Run the script. `inherited` is the environment the run starts from, pytest's own
        when not given; the run's PATH is the stub's directory whatever it holds."""
        inherited = os.environ if inherited is None else inherited
        full_env = {
            **inherited,
            # The stub is the only program on PATH: the script and the command it hands
            # off to are both started by the interpreter's own path.
            "PATH": str(self.tmp / "bin"),
            "STUB_LOG": str(self.tmp / "aws.log"),
            "STUB_STORE": str(self.tmp / "store.json"),
            "CHILD_ENV": str(self.tmp / "child.json"),
            "IMAGE_URI": "registry/svc@sha256:" + "a" * 64,
            **env,
        }
        for name in ("DATABASE_URL", "AUTH_JWKS_URI", "AUTH_ISSUER", *CALLER_VPC):
            if name not in env:
                full_env.pop(name, None)
        argv = [
            sys.executable,
            str(SCRIPT),
            "--spec",
            str(self.tmp / "deploy_env.json"),
            "--serverless",
            str(self.tmp / "serverless.yml"),
            mode,
            stage,
            *rest,
        ]
        return subprocess.run(argv, capture_output=True, text=True, env=full_env, timeout=60)


@pytest.fixture
def stubs(tmp_path: Path) -> Stubs:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir)
    made = Stubs(tmp_path)
    made.write()
    return made


def test_check_reads_every_declared_source_for_the_stage_and_prints_no_value(stubs) -> None:
    result = stubs.run("check")
    assert result.returncode == 0, result.stderr
    assert stubs.calls() == [
        "aws secretsmanager get-secret-value --secret-id svc/dev/database-url --output json",
        "aws ssm get-parameter --name /svc/dev/auth-jwks-uri --with-decryption --output json",
    ]
    assert "2 declared variable(s) resolved" in result.stdout
    visible = [ln for ln in result.stdout.splitlines() if not ln.startswith("::add-mask::")]
    assert not any(SECRET in ln or JWKS in ln for ln in visible), result.stdout
    assert f"::add-mask::{SECRET}" in result.stdout.splitlines()


def test_every_line_of_a_multi_line_value_is_masked(stubs) -> None:
    stubs.write(store={**STORE, "svc/dev/database-url": "line-one\nline-two"})
    result = stubs.run("check")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "::add-mask::line-one" in lines and "::add-mask::line-two" in lines


def test_check_refuses_naming_every_absent_source_not_only_the_first(stubs) -> None:
    stubs.write(store={})
    result = stubs.run("check")
    assert result.returncode == 2
    assert "DATABASE_URL: secretsmanager svc/dev/database-url could not be read" in result.stderr
    assert "AUTH_JWKS_URI: ssm /svc/dev/auth-jwks-uri could not be read" in result.stderr
    assert "ParameterNotFound" in result.stderr


def test_check_refuses_a_source_that_reads_empty(stubs) -> None:
    stubs.write(store={**STORE, "/svc/dev/auth-jwks-uri": "   "})
    result = stubs.run("check")
    assert result.returncode == 2
    assert "AUTH_JWKS_URI: ssm /svc/dev/auth-jwks-uri read empty" in result.stderr


def test_check_refuses_any_other_aws_error(stubs) -> None:
    result = stubs.run("check", STUB_AWS_ERROR="An error occurred (AccessDeniedException)")
    assert result.returncode == 2
    assert "AccessDeniedException" in result.stderr


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (None, "no deploy configuration declaration"),
        ("{not json", "is not valid JSON"),
        ({"vars": {}}, "has no 'variables' object"),
        ({"variables": {"lower": {"ssm": "/x"}}}, "is not an environment variable name"),
        ({"variables": {"FOO\n": {"ssm": "/x"}}}, "is not an environment variable name"),
        ({"variables": {"X": {"ssm": "/x", "secretsmanager": "x"}}}, "exactly one of"),
        ({"variables": {"X": {"vault": "x"}}}, "exactly one of"),
        ({"variables": {"X": {"ssm": "/has space"}}}, "no usable ssm id"),
    ],
    ids=[
        "missing",
        "bad-json",
        "no-variables",
        "bad-name",
        "name-trailing-newline",
        "two-sources",
        "unknown",
        "space",
    ],
)
def test_a_malformed_declaration_refuses_before_any_read(stubs, spec, message) -> None:
    if spec is None:
        (stubs.tmp / "deploy_env.json").unlink()
    else:
        stubs.write(spec=spec)
    result = stubs.run("check")
    assert result.returncode == 2
    assert message in result.stderr
    assert stubs.calls() == []


@pytest.mark.parametrize("stage", ["Dev;rm", "dev\n"], ids=["shell", "trailing-newline"])
def test_a_bad_stage_refuses(stubs, stage) -> None:
    result = stubs.run("check", stage=stage)
    assert result.returncode == 2
    assert "is not a lower-case stage name" in result.stderr
    assert stubs.calls() == []


def test_a_value_holding_a_percent_escape_is_masked_as_itself(stubs) -> None:
    """The runner unescapes `%25` to `%` before registering a mask."""
    stubs.write(store={**STORE, "svc/dev/database-url": "p%25ss\rword"})
    result = stubs.run("check")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "::add-mask::p%2525ss" in lines and "::add-mask::word" in lines
    assert "::add-mask::p%25ss" not in lines


def test_exec_hands_the_values_to_the_command_and_returns_its_exit_code(stubs) -> None:
    result = stubs.run("exec", "--", *CHILD, CHILD_EXIT="7")
    assert result.returncode == 7, result.stderr
    seen = stubs.child_env()
    assert seen is not None, "the command never ran"
    assert (seen["DATABASE_URL"], seen["AUTH_JWKS_URI"]) == (SECRET, JWKS)
    assert seen["IMAGE_URI"].startswith("registry/svc@sha256:")


def test_exec_refuses_a_no_default_variable_nobody_supplies(stubs) -> None:
    stubs.write(serverless=SERVERLESS + "    ISSUER: ${env:AUTH_ISSUER}\n")
    result = stubs.run("exec", "--", *CHILD)
    assert result.returncode == 2
    assert "neither deploy_env.json declares them nor the step sets them: AUTH_ISSUER" in (
        result.stderr
    )
    assert "NOT_REQUIRED_BECAUSE_COMMENTED" not in result.stderr
    assert "AWS_REGION" not in result.stderr
    assert stubs.child_env() is None, "the command ran without its configuration"


def test_exec_refuses_an_empty_step_value(stubs) -> None:
    result = stubs.run("exec", "--", *CHILD, IMAGE_URI="")
    assert result.returncode == 2
    assert "IMAGE_URI" in result.stderr
    assert stubs.child_env() is None


def test_exec_refuses_a_declared_variable_also_set_as_plain_text(stubs) -> None:
    result = stubs.run("exec", "--", *CHILD, DATABASE_URL="mysql://plain:text@db/svc")
    assert result.returncode == 2
    assert "plain text in the workflow: DATABASE_URL" in result.stderr
    assert stubs.calls() == [], "a shadowed declaration must refuse before reading anything"
    assert stubs.child_env() is None


def test_exec_refuses_when_a_source_is_absent_and_never_runs_the_command(stubs) -> None:
    stubs.write(store={"svc/dev/database-url": SECRET})
    result = stubs.run("exec", "--", *CHILD)
    assert result.returncode == 2
    assert "AUTH_JWKS_URI" in result.stderr
    assert stubs.child_env() is None


def test_exec_without_a_command_refuses(stubs) -> None:
    result = stubs.run("exec")
    assert result.returncode == 2
    assert "exec needs a command" in result.stderr


# -- the caller-VPC values have a shape ---------------------------


@pytest.mark.parametrize(
    ("group", "subnets"),
    [
        (GROUP, SUBNETS),
        ("sg-0c0ffee0", "subnet-0aaaaaaa"),
        (GROUP, "subnet-0aaaaaaaaaaaaaaaa"),
    ],
    ids=["17-hex-two-subnets", "8-hex-one-subnet", "one-subnet"],
)
def test_caller_vpc_values_in_their_shape_resolve(stubs, group, subnets) -> None:
    stubs.write(spec=CALLER_SPEC, store=caller_store(group, subnets))
    result = stubs.run("check")
    assert result.returncode == 0, result.stderr
    assert "2 declared variable(s) resolved" in result.stdout


#: What the b2 QA probe compiled into a template unrefused (a1-proof/b2/qa/tpl/
#: sls-probe.summary), and its neighbours: each would reach CloudFormation or Lambda.
BAD_GROUPS = {
    "group-name": "foundry-estate-caller",
    "upper-case": "sg-0C0FFEE0C0FFEE0C0",
    "16-hex": "sg-0c0ffee0c0ffee0c",
    "two-groups": f"{GROUP},sg-0c0ffee0c0ffee0c1",
    "trailing-newline": GROUP + "\n",
    "leading-space": " " + GROUP,
}
BAD_SUBNETS = {
    "spaced": "subnet-0aaaaaaaaaaaaaaaa, subnet-0bbbbbbbbbbbbbbbb",
    "trailing-comma": "subnet-0aaaaaaaaaaaaaaaa,",
    "empty-item": "subnet-0aaaaaaaaaaaaaaaa,,subnet-0bbbbbbbbbbbbbbbb",
    "a-group": GROUP,
    "subnet-name": "private-a,private-b",
}


@pytest.mark.parametrize("value", BAD_GROUPS.values(), ids=BAD_GROUPS.keys())
def test_check_refuses_a_caller_group_that_is_not_one_group_id(stubs, value) -> None:
    stubs.write(spec=CALLER_SPEC, store=caller_store(group=value))
    result = stubs.run("check")
    assert result.returncode == 2
    assert (
        "ESTATE_CALLER_SG_ID: ssm /foundry/dev/network/estate-caller-sg-id is not one "
        "security group id" in result.stderr
    )
    assert "PRIVATE_SUBNET_IDS" not in result.stderr, "a good value was refused with it"
    assert value.strip() not in result.stderr, "the refusal quoted the value"
    assert len(stubs.calls()) == 2, "the refusal came before the read, not from it"


@pytest.mark.parametrize("value", BAD_SUBNETS.values(), ids=BAD_SUBNETS.keys())
def test_check_refuses_a_subnet_list_that_is_not_comma_separated_subnet_ids(stubs, value) -> None:
    stubs.write(spec=CALLER_SPEC, store=caller_store(subnets=value))
    result = stubs.run("check")
    assert result.returncode == 2
    assert (
        "PRIVATE_SUBNET_IDS: ssm /foundry/dev/network/private-subnet-ids is not subnet ids"
        in result.stderr
    )
    assert "comma-separated with no spaces" in result.stderr
    assert "ESTATE_CALLER_SG_ID" not in result.stderr, "a good value was refused with it"
    assert value not in result.stderr, "the refusal quoted the value"


def test_both_misshapen_values_are_named_in_one_refusal(stubs) -> None:
    stubs.write(spec=CALLER_SPEC, store=caller_store("a-name", "private-a"))
    result = stubs.run("check")
    assert result.returncode == 2
    assert "ESTATE_CALLER_SG_ID" in result.stderr and "PRIVATE_SUBNET_IDS" in result.stderr


def test_exec_never_runs_the_command_over_a_misshapen_caller_value(stubs) -> None:
    stubs.write(
        spec=CALLER_SPEC,
        store=caller_store(subnets=BAD_SUBNETS["spaced"]),
        serverless="provider:\n  environment:\n    IMAGE: ${env:IMAGE_URI}\n",
    )
    result = stubs.run("exec", "--", *CHILD)
    assert result.returncode == 2
    assert "PRIVATE_SUBNET_IDS" in result.stderr
    assert stubs.child_env() is None, "the command ran over a misshapen value"


@pytest.mark.parametrize("mode", ["check", "exec"])
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ESTATE_CALLER_SG_ID", ""),
        ("ESTATE_CALLER_SG_ID", "a-name"),
        ("PRIVATE_SUBNET_IDS", BAD_SUBNETS["spaced"]),
    ],
    ids=["empty-group", "group-name", "spaced-subnets"],
)
def test_a_misshapen_step_value_refuses_before_any_read(stubs, mode, name, value) -> None:
    """Undeclared, set by the step: still the value caller_vpc.yml would consume."""
    rest = ("--", *CHILD) if mode == "exec" else ()
    result = stubs.run(mode, *rest, **{name: value})
    assert result.returncode == 2
    assert f"a step value is not the shape the kit reads it in: {name} is not" in result.stderr
    assert stubs.calls() == []
    assert stubs.child_env() is None


def test_a_step_value_in_its_shape_reaches_the_command(stubs) -> None:
    result = stubs.run("exec", "--", *CHILD, ESTATE_CALLER_SG_ID=GROUP, PRIVATE_SUBNET_IDS=SUBNETS)
    assert result.returncode == 0, result.stderr
    seen = stubs.child_env()
    assert seen is not None
    assert (seen["ESTATE_CALLER_SG_ID"], seen["PRIVATE_SUBNET_IDS"]) == (GROUP, SUBNETS)


# -- ids that name an earlier variable ---------------------------

DEPLOYMENT = "comms-foundry"
CHAINED = {
    "variables": {
        "COMMS_DEPLOYMENT": {"ssm": "/comms/{stage}/deployment"},
        "COMMS_EVENTS_QUEUE": {"ssm": "/{COMMS_DEPLOYMENT}/core/events-queue-arn"},
    }
}
QUEUE = "arn:aws:sqs:eu-west-1:000000000000:comms-foundry-core-events"
CHAINED_STORE = {
    "/comms/dev/deployment": DEPLOYMENT,
    f"/{DEPLOYMENT}/core/events-queue-arn": QUEUE,
}


def _visible(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("::add-mask::"))


def test_an_id_naming_an_earlier_variable_reads_under_its_value(stubs) -> None:
    stubs.write(spec=CHAINED, store=CHAINED_STORE, serverless="provider: {}\n")
    result = stubs.run("exec", "--", *CHILD)
    assert result.returncode == 0, result.stderr
    assert stubs.calls() == [
        "aws ssm get-parameter --name /comms/dev/deployment --with-decryption --output json",
        f"aws ssm get-parameter --name /{DEPLOYMENT}/core/events-queue-arn --with-decryption "
        "--output json",
    ]
    seen = stubs.child_env()
    assert seen is not None
    assert (seen["COMMS_DEPLOYMENT"], seen["COMMS_EVENTS_QUEUE"]) == (DEPLOYMENT, QUEUE)
    assert DEPLOYMENT not in _visible(result.stdout) and QUEUE not in _visible(result.stdout)


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        (
            {"LATER": {"ssm": "/{FIRST}/x"}, "FIRST": {"ssm": "/first"}},
            "LATER names {FIRST}, which is not a variable declared before it",
        ),
        ({"X": {"ssm": "/{NOPE}/x"}}, "X names {NOPE}, which is not a variable declared"),
        (
            {"SECRET": {"secretsmanager": "s"}, "X": {"ssm": "/{SECRET}/x"}},
            "X names {SECRET}, a secretsmanager variable",
        ),
        ({"X": {"ssm": "/{service}/x"}}, "placeholder other than {stage} or {NAME}"),
        ({"X": {"ssm": "/{stage/x"}}, "placeholder other than {stage} or {NAME}"),
        ({"X": {"ssm": "/stage}/x"}}, "placeholder other than {stage} or {NAME}"),
    ],
    ids=["forward", "undeclared", "secret", "lower-case", "open-brace", "close-brace"],
)
def test_a_bad_reference_refuses_before_any_read(stubs, variables, message) -> None:
    stubs.write(spec={"variables": variables})
    result = stubs.run("check")
    assert result.returncode == 2
    assert message in result.stderr
    assert stubs.calls() == []


def test_a_reference_that_did_not_resolve_is_not_read_and_both_are_named(stubs) -> None:
    stubs.write(spec=CHAINED, store={})
    result = stubs.run("check")
    assert result.returncode == 2
    assert "COMMS_DEPLOYMENT: ssm /comms/dev/deployment could not be read" in result.stderr
    assert (
        "COMMS_EVENTS_QUEUE: ssm /{COMMS_DEPLOYMENT}/core/events-queue-arn names "
        "COMMS_DEPLOYMENT, which did not resolve, so it was not read"
    ) in result.stderr
    assert stubs.calls() == [
        "aws ssm get-parameter --name /comms/dev/deployment --with-decryption --output json"
    ]


def test_a_failed_read_names_the_declared_id_and_the_value_is_already_masked(stubs) -> None:
    stubs.write(spec=CHAINED, store={"/comms/dev/deployment": DEPLOYMENT})
    result = stubs.run("check")
    assert result.returncode == 2
    assert "ssm /{COMMS_DEPLOYMENT}/core/events-queue-arn could not be read" in result.stderr
    assert DEPLOYMENT not in result.stderr
    assert f"::add-mask::{DEPLOYMENT}" in result.stdout.splitlines()


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"Type": "SecureString", "Value": DEPLOYMENT}, "is a SecureString; only a plain String"),
        ("comms foundry", "its value is not an id segment"),
        ("comms-foundry/{stage}", "its value is not an id segment"),
        ("comms-foundry\n", "its value is not an id segment"),
    ],
    ids=["secure-string", "space", "brace", "trailing-newline"],
)
def test_a_referenced_value_that_cannot_be_an_id_refuses_unread(stubs, entry, message) -> None:
    stubs.write(spec=CHAINED, store={**CHAINED_STORE, "/comms/dev/deployment": entry})
    result = stubs.run("check")
    assert result.returncode == 2
    assert "COMMS_DEPLOYMENT: another id names it, and" in result.stderr
    assert message in result.stderr
    assert "COMMS_EVENTS_QUEUE" in result.stderr and "did not resolve" in result.stderr
    assert len(stubs.calls()) == 1
    assert "comms" not in result.stderr.replace("/comms/dev/deployment", "")
    # A value refused as an id segment was still read, so it is masked before the refusal.
    value = entry["Value"] if isinstance(entry, dict) else entry
    assert f"::add-mask::{value.splitlines()[0]}" in result.stdout.splitlines()


def test_a_run_can_start_no_aws_but_the_stub(stubs, tmp_path) -> None:
    """A second `aws` is put first on a copy of pytest's own environment, and shown to be
    the one that environment's PATH starts. A run from that environment hands its command a
    PATH that is the stub's directory alone, whichever environment the PATH would otherwise
    have come from. Then the stub is taken away and the script is run from that
    environment: it finds no `aws` at all, and the second one is not started again.

    The first assertion is the one a PATH read from pytest's own `os.environ` fails: that
    PATH never holds the second `aws`, so without it the run would reach whatever `aws`
    pytest's PATH holds before any later assertion went red."""
    other = tmp_path / "other"
    other.mkdir()
    record = tmp_path / "other-aws.log"
    (other / "aws").write_text(f'#!/bin/sh\necho "$*" >> {shlex.quote(str(record))}\nexit 97\n')
    (other / "aws").chmod(0o755)
    first = {**os.environ, "PATH": f"{other}{os.pathsep}{os.environ['PATH']}"}
    assert subprocess.run(["aws", "--version"], check=False, timeout=60, env=first).returncode == 97
    assert record.read_text().splitlines() == ["--version"]

    result = stubs.run("exec", "--", *CHILD, inherited=first)
    assert result.returncode == 0, result.stdout + result.stderr
    seen = stubs.child_env()
    assert seen is not None, "the command never ran"
    assert seen["PATH"] == str(tmp_path / "bin"), "a run's PATH is not the stub's directory alone"
    read = stubs.calls()

    (tmp_path / "bin" / "aws").unlink()
    result = stubs.run("check", inherited=first)
    assert record.read_text().splitlines() == ["--version"], "a run started another aws"
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSED: the aws CLI is not on PATH, so no source can be read\n" == result.stderr
    assert stubs.calls() == read


def test_the_stub_starts_under_an_interpreter_whose_path_holds_a_space(tmp_path) -> None:
    """The interpreter here is a file in a directory named `py space` that starts the real
    one, and the stub's directory and program sit beside it. The stub written for it
    answers a read. Written as a file whose first line named that interpreter, it could
    not be started at all."""
    spaced = tmp_path / "py space" / "python"
    spaced.parent.mkdir()
    spaced.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    spaced.chmod(0o755)
    bin_dir = spaced.parent / "bin"
    bin_dir.mkdir()
    aws = write_stub(bin_dir, python=str(spaced))
    (tmp_path / "store.json").write_text(json.dumps(STORE))
    try:
        answer = subprocess.run(
            [str(aws), "ssm", "get-parameter", "--name", "/svc/dev/auth-jwks-uri"],
            capture_output=True,
            text=True,
            timeout=60,
            env={
                "PATH": str(bin_dir),
                "STUB_LOG": str(tmp_path / "aws.log"),
                "STUB_STORE": str(tmp_path / "store.json"),
            },
        )
    except OSError as exc:
        raise AssertionError(f"the stub could not be started under {spaced}: {exc}") from exc
    assert answer.returncode == 0, answer.stderr
    assert json.loads(answer.stdout)["Parameter"]["Value"] == JWKS
    assert (tmp_path / "aws.log").read_text().splitlines() == [
        "aws ssm get-parameter --name /svc/dev/auth-jwks-uri"
    ]
