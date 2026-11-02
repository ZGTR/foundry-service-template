"""The template's own `deploy/deploy_env.json` covers the template's own `serverless.yml`.

Template-only: `scripts/new_service.sh` names this file in `TEMPLATE_ONLY`, so a service
is not given it. Every arm runs `service-kit/ci/deploy_env.py` over this repository's
`deploy/` files and asserts what THOSE files say. `deploy/` is the service's own, so in
a service that had declared a variable these arms asserted the template's content of a
file the service had changed. They lived in `test_deploy_env_script.py`, which ships: a
service that followed the README's estate opt-in had one red test, and that test
started the real `aws` CLI twice from inside pytest.

The declaration reads the estate's shared listener and private zone from the two
parameters the internal-edge stack publishes under `/foundry/<Stage>/network/`.
The names and the values below are written by hand; none is read
from the declaration they judge.

A stand-in `aws` is the only program on PATH for every run here. It is a `/bin/sh` file,
so no interpreter's path is in its first line. It records its arguments, answers the
reads it was given an answer for, and exits 97 on anything else. The last two tests are
its controls. With one variable declared in a scratch spec, the stand-in is the `aws`
the script starts. With the stand-in taken away and another `aws` first on the PATH
pytest itself has, the script finds none.

No AWS call is made.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "service-kit" / "ci" / "deploy_env.py"

LISTENER = "arn:aws:elasticloadbalancing:eu-west-1:000000000000:listener/app/i/a/b"
ZONE = "svc.example.internal"
#: What the internal-edge stack publishes for stage `dev`, as the stand-in answers it.
EDGE = {
    "/foundry/dev/network/internal-listener-arn": LISTENER,
    "/foundry/dev/network/internal-zone-suffix": ZONE,
}
#: What the script asks for, in the declaration's order.
EDGE_READS = [
    "ssm get-parameter --name /foundry/dev/network/internal-listener-arn "
    "--with-decryption --output json",
    "ssm get-parameter --name /foundry/dev/network/internal-zone-suffix "
    "--with-decryption --output json",
]
#: What the deploy step sets beside the image: this service's row in the service
#: directory. Hand-written, so a variable added to serverless.yml without a step value
#: or a declaration turns the first test below red instead of being absorbed.
STEP_VALUES = {"LISTENER_RULE_PRIORITY": "100"}
#: Every name the tests set or leave out, dropped from what pytest inherited.
INGRESS_NAMES = ("INTERNAL_LISTENER_ARN", "INTERNAL_ZONE_SUFFIX", "LISTENER_RULE_PRIORITY")

#: An answer is a file under $STAND_IN_ANSWERS at the parameter's own name.
STAND_IN_AWS = """\
#!/bin/sh
echo "$*" >> "$STAND_IN_RECORD"
if [ "$1 $2 $3" = "ssm get-parameter --name" ] && [ -f "$STAND_IN_ANSWERS$4" ]; then
    /bin/cat "$STAND_IN_ANSWERS$4"
    exit 0
fi
exit 97
"""

#: The command `exec` hands off to once the declaration is covered: it writes down the
#: three values it was handed, and beside them the PATH it was given.
CHILD = [
    sys.executable,
    "-c",
    (
        "import json, os; json.dump({name: os.environ.get(name) for name in "
        f"{INGRESS_NAMES!r}}}, open(os.environ['CHILD_SAW'], 'w')); "
        "open(os.environ['CHILD_SAW'] + '.path', 'w').write(os.environ['PATH'])"
    ),
]


@dataclass
class StandIn:
    """An `aws` that records what it was asked and answers only what it was given."""

    tmp: Path

    def answers(self, table: dict[str, str]) -> None:
        root = self.tmp / "answers"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir()
        for name, value in table.items():
            assert name.startswith("/"), name
            answer = root / name[1:]
            answer.parent.mkdir(parents=True, exist_ok=True)
            answer.write_text(
                json.dumps({"Parameter": {"Name": name, "Type": "String", "Value": value}})
            )

    def calls(self) -> list[str]:
        record = self.tmp / "aws-calls.log"
        return record.read_text().splitlines() if record.is_file() else []

    def child_saw(self) -> dict[str, str | None] | None:
        seen = self.tmp / "child.json"
        return json.loads(seen.read_text()) if seen.is_file() else None

    def child_path(self) -> str | None:
        seen = self.tmp / "child.json.path"
        return seen.read_text() if seen.is_file() else None

    def run(
        self,
        mode: str,
        step_env: dict[str, str],
        *options: str,
        stage: str = "dev",
        environ: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """`environ` is the environment the run starts from, pytest's own when not given;
        the run's PATH is the stand-in's directory whatever it holds."""
        environ = os.environ if environ is None else environ
        inherited = {k: v for k, v in environ.items() if k not in INGRESS_NAMES}
        command = ["--", *CHILD] if mode == "exec" else []
        return subprocess.run(
            [sys.executable, str(SCRIPT), *options, mode, stage, *command],
            capture_output=True,
            text=True,
            timeout=60,
            env={
                **inherited,
                # The stand-in is the only program on PATH: the script and the command
                # it hands off to are both started by the interpreter's own path.
                "PATH": str(self.tmp / "bin"),
                "STAND_IN_RECORD": str(self.tmp / "aws-calls.log"),
                "STAND_IN_ANSWERS": str(self.tmp / "answers"),
                "CHILD_SAW": str(self.tmp / "child.json"),
                "IMAGE_URI": "registry/svc@sha256:" + "a" * 64,
                **step_env,
            },
        )


@pytest.fixture
def stand_in(tmp_path: Path) -> StandIn:
    (tmp_path / "bin").mkdir()
    aws = tmp_path / "bin" / "aws"
    aws.write_text(STAND_IN_AWS)
    aws.chmod(0o755)
    made = StandIn(tmp_path)
    made.answers(EDGE)
    return made


def test_the_templates_own_declaration_covers_its_serverless_config(stand_in: StandIn) -> None:
    """With the rule priority as the one step value and the edge's two parameters
    published, the deploy is covered: the script reads exactly those two, in order, and
    the command it hands off to receives all three."""
    result = stand_in.run("exec", STEP_VALUES)
    assert result.returncode == 0, result.stderr
    assert (
        "2 declared variable(s) resolved "
        "(INTERNAL_LISTENER_ARN from ssm, INTERNAL_ZONE_SUFFIX from ssm)"
    ) in result.stdout, result.stdout
    assert stand_in.calls() == EDGE_READS
    assert stand_in.child_saw() == {
        "INTERNAL_LISTENER_ARN": LISTENER,
        "INTERNAL_ZONE_SUFFIX": ZONE,
        "LISTENER_RULE_PRIORITY": "100",
    }


def test_a_deploy_with_no_rule_priority_is_refused_before_anything_is_read(
    stand_in: StandIn,
) -> None:
    """The rule priority is the one ingress value the step still sets. serverless.yml
    reads it with no fallback, so a deploy without it stops here, naming it, before the
    script asks for a parameter and before anything reaches Lambda."""
    result = stand_in.run("exec", {})
    assert result.returncode == 2, result.stdout + result.stderr
    assert (
        "neither deploy_env.json declares them nor the step sets them: LISTENER_RULE_PRIORITY\n"
    ) in result.stderr, result.stderr
    assert stand_in.calls() == [], "a refused deploy started the aws CLI"
    assert stand_in.child_saw() is None, "the command ran in a refused deploy"


@pytest.mark.parametrize("name", ["INTERNAL_LISTENER_ARN", "INTERNAL_ZONE_SUFFIX"])
def test_an_edge_value_that_is_also_a_step_value_is_refused(stand_in: StandIn, name: str) -> None:
    """What the workflow did before an earlier change: it passed both from repository
    variables. With the declaration in place a step value for either is that value in
    plain text beside its declared source, and the script refuses it by name."""
    result = stand_in.run("exec", {**STEP_VALUES, name: "typed-into-the-workflow"})
    assert result.returncode == 2, result.stdout + result.stderr
    assert f"so one of them is plain text in the workflow: {name}\n" in result.stderr, result.stderr
    assert stand_in.calls() == [], "a refused deploy started the aws CLI"
    assert stand_in.child_saw() is None, "the command ran in a refused deploy"


def test_an_edge_that_is_not_applied_stops_the_check_naming_both_parameters(
    stand_in: StandIn,
) -> None:
    """`check` is the step before the push. With neither parameter published it asks for
    both and refuses, naming each variable with the parameter it could not read."""
    stand_in.answers({})
    result = stand_in.run("check", {})
    assert result.returncode == 2, result.stdout + result.stderr
    for variable, parameter in (
        ("INTERNAL_LISTENER_ARN", "/foundry/dev/network/internal-listener-arn"),
        ("INTERNAL_ZONE_SUFFIX", "/foundry/dev/network/internal-zone-suffix"),
    ):
        assert f"{variable}: ssm {parameter} could not be read (no output)" in result.stderr, (
            result.stderr
        )
    assert stand_in.calls() == EDGE_READS


def test_the_stage_of_the_deploy_is_the_stage_in_both_parameter_names(stand_in: StandIn) -> None:
    """The edge stack publishes under its own Stage, and a deploy to `prod` must read
    `prod`'s edge. The stand-in holds `dev`'s two parameters only, so a declaration that
    wrote a stage out would resolve here and be handed to a `prod` deploy."""
    result = stand_in.run("check", {}, stage="prod")
    assert result.returncode == 2, result.stdout + result.stderr
    assert stand_in.calls() == [
        "ssm get-parameter --name /foundry/prod/network/internal-listener-arn "
        "--with-decryption --output json",
        "ssm get-parameter --name /foundry/prod/network/internal-zone-suffix "
        "--with-decryption --output json",
    ]


def test_the_stand_in_is_the_aws_the_script_starts(stand_in: StandIn, tmp_path: Path) -> None:
    """The control for every `calls() == []` above, down the same `exec` path. One
    variable is declared in a scratch spec, beside the template's own serverless config,
    and the listener and the zone are step values so that the scratch spec covers it:
    the script asks the stand-in for the one variable, once, and refuses on the
    stand-in's answer. A stand-in the script could not see would leave those assertions
    true of any declaration."""
    spec = tmp_path / "one_read.json"
    spec.write_text(json.dumps({"variables": {"PROBE": {"ssm": "/svc/{stage}/probe"}}}))
    covered = {**STEP_VALUES, "INTERNAL_LISTENER_ARN": LISTENER, "INTERNAL_ZONE_SUFFIX": ZONE}
    result = stand_in.run("exec", covered, "--spec", str(spec))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "PROBE: ssm /svc/dev/probe could not be read (no output)" in result.stderr, result.stderr
    assert stand_in.calls() == [
        "ssm get-parameter --name /svc/dev/probe --with-decryption --output json"
    ]
    assert stand_in.child_saw() is None, "the command ran in a refused deploy"


def test_a_run_can_start_no_aws_but_the_stand_in(stand_in: StandIn, tmp_path: Path) -> None:
    """Another `aws` is put first on a copy of pytest's own environment, and shown to be
    the one that environment's PATH starts. A deploy run from that environment hands its
    command a PATH that is the stand-in's directory alone. Then the stand-in is taken away
    and the check is run from that environment: the script finds no `aws` at all, and the
    other one is not started again. So a run here cannot reach the CLI of the machine it
    runs on, whatever this file's stand-in does.

    The PATH assertion is the one a PATH read from pytest's own `os.environ` fails: that
    PATH never holds the other `aws`, so without it the run would reach whatever `aws`
    pytest's PATH holds before any later assertion went red."""
    other = tmp_path / "other"
    other.mkdir()
    record = tmp_path / "other-aws.log"
    (other / "aws").write_text(f'#!/bin/sh\necho "$*" >> "{record}"\nexit 97\n')
    (other / "aws").chmod(0o755)
    first = {**os.environ, "PATH": f"{other}{os.pathsep}{os.environ['PATH']}"}
    assert subprocess.run(["aws", "--version"], check=False, timeout=60, env=first).returncode == 97
    assert record.read_text().splitlines() == ["--version"]

    result = stand_in.run("exec", STEP_VALUES, environ=first)
    assert result.returncode == 0, result.stdout + result.stderr
    assert stand_in.child_path() == str(tmp_path / "bin"), (
        "a run's PATH is not the stand-in's directory alone"
    )
    read = stand_in.calls()

    (tmp_path / "bin" / "aws").unlink()
    result = stand_in.run("check", {}, environ=first)
    assert record.read_text().splitlines() == ["--version"], "a run started another aws"
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stderr == "REFUSED: the aws CLI is not on PATH, so no source can be read\n"
    assert stand_in.calls() == read
