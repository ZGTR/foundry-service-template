"""`service-kit/ci/lambda_release.sh` records a rollback target, reads the URL, and rolls back.

The deploy job's rollback used to be `echo "placeholder -- point Lambda back at the
previous image digest"` under a step named "Rollback on red smoke". These arms run
the real script with a stub `aws` and a stub `npx` first on PATH. The stubs share
one state file standing in for the function's running image: `aws lambda
get-function` reads it and `npx serverless@3 deploy` writes `IMAGE_URI` into it,
so a rollback that redeploys the wrong image, or claims success without the
redeploy landing, is visible as state rather than inferred from a log line.

No AWS call is made and nothing is deployed; the CI and production arms of this
script need Actions billing and a deployed stack respectively, and are not
claimed here.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "service-kit" / "ci" / "lambda_release.sh"

OLD = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/svc@sha256:" + "a" * 64
NEW = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/svc@sha256:" + "b" * 64
FUNCTION = "svc-dev-api"
NOT_FOUND = (
    "An error occurred (ResourceNotFoundException) when calling the GetFunction "
    "operation: Function not found"
)

STUB_AWS = """#!/usr/bin/env bash
echo "aws $*" >> "$STUB_LOG"
case "$1 $2" in
  "lambda get-function")
    if [ -n "${STUB_AWS_ERROR:-}" ]; then echo "$STUB_AWS_ERROR" >&2; exit 254; fi
    if [ ! -f "$STUB_STATE" ]; then echo "$STUB_NOT_FOUND" >&2; exit 254; fi
    cat "$STUB_STATE"; exit 0 ;;
  "cloudformation describe-stacks")
    if [ -n "${STUB_CFN_ERROR:-}" ]; then echo "$STUB_CFN_ERROR" >&2; exit 254; fi
    printf '%s\\n' "${STUB_API_URL:-}"; exit 0 ;;
esac
echo "stub aws: unexpected call $*" >&2
exit 99
"""

STUB_NPX = """#!/usr/bin/env bash
echo "npx $* | cwd=$PWD | IMAGE_URI=${IMAGE_URI:-}" >> "$STUB_LOG"
if [ -n "${STUB_NPX_EXIT:-}" ]; then exit "$STUB_NPX_EXIT"; fi
if [ -z "${STUB_NPX_NO_EFFECT:-}" ]; then printf '%s\\n' "$IMAGE_URI" > "$STUB_STATE"; fi
exit 0
"""


@dataclass
class Stubs:
    bin_dir: Path
    log: Path
    state: Path

    def running(self, image: str) -> None:
        self.state.write_text(image + "\n")

    def calls(self, tool: str) -> list[str]:
        if not self.log.is_file():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.startswith(tool + " ")]

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        full_env = {
            **os.environ,
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_LOG": str(self.log),
            "STUB_STATE": str(self.state),
            "STUB_NOT_FOUND": NOT_FOUND,
            **env,
        }
        return subprocess.run(
            ["bash", str(RELEASE), *args], capture_output=True, text=True, env=full_env, timeout=60
        )


@pytest.fixture
def stubs(tmp_path: Path) -> Stubs:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("aws", STUB_AWS), ("npx", STUB_NPX)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return Stubs(bin_dir, tmp_path / "calls.log", tmp_path / "running_image")


def test_the_script_parses_and_is_executable() -> None:
    assert os.access(RELEASE, os.X_OK), f"{RELEASE} lost its execute bit"
    assert subprocess.run(["bash", "-n", str(RELEASE)]).returncode == 0


def test_previous_image_records_the_running_digest(stubs: Stubs) -> None:
    stubs.running(OLD)
    result = stubs.run("previous-image", FUNCTION)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"image_uri={OLD}\n", "stdout is appended to GITHUB_OUTPUT verbatim"
    assert stubs.calls("aws") == [
        f"aws lambda get-function --function-name {FUNCTION} "
        "--query Code.ResolvedImageUri --output text"
    ]


def test_previous_image_on_a_first_deploy_records_an_empty_target(stubs: Stubs) -> None:
    result = stubs.run("previous-image", FUNCTION)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "image_uri=\n"
    assert "first deploy" in result.stderr


def test_previous_image_refuses_any_other_aws_error(stubs: Stubs) -> None:
    """A deploy that cannot name its rollback target must not start."""
    stubs.running(OLD)
    result = stubs.run("previous-image", FUNCTION, STUB_AWS_ERROR="AccessDeniedException")
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == "", "nothing may reach GITHUB_OUTPUT on a refusal"
    assert "AccessDeniedException" in result.stderr


def test_previous_image_refuses_a_function_without_a_digest(stubs: Stubs) -> None:
    stubs.running("None")
    result = stubs.run("previous-image", FUNCTION)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""


def test_api_url_reads_the_stack_output(stubs: Stubs) -> None:
    url = "https://abc123.execute-api.eu-west-1.amazonaws.com"
    result = stubs.run("api-url", "svc-dev", STUB_API_URL=url)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"api_url={url}\n"
    assert "OutputKey=='ApiUrl'" in stubs.calls("aws")[0]


@pytest.mark.parametrize(
    "env",
    [{"STUB_API_URL": ""}, {"STUB_API_URL": "None"}, {"STUB_CFN_ERROR": "Stack does not exist"}],
    ids=["empty", "none", "describe-fails"],
)
def test_api_url_refuses_when_the_url_cannot_be_read(stubs: Stubs, env: dict[str, str]) -> None:
    result = stubs.run("api-url", "svc-dev", **env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == "", "an empty api_url must never reach the smoke step"


def test_rollback_redeploys_the_recorded_digest_and_reads_it_back(stubs: Stubs) -> None:
    stubs.running(NEW)
    result = stubs.run("rollback", OLD, "dev", FUNCTION)
    assert result.returncode == 0, result.stdout + result.stderr

    deploys = stubs.calls("npx")
    assert deploys == [
        f"npx --yes serverless@3 deploy --stage dev --config serverless.yml "
        f"| cwd={ROOT / 'deploy' / 'iac'} | IMAGE_URI={OLD}"
    ], deploys
    assert stubs.state.read_text().strip() == OLD, "the function must end up running OLD"
    assert f"ROLLED BACK {FUNCTION} to {OLD}" in result.stdout


def test_rollback_after_a_first_deploy_refuses_and_claims_nothing(stubs: Stubs) -> None:
    stubs.running(NEW)
    result = stubs.run("rollback", "", "dev", FUNCTION)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "NO ROLLBACK HAPPENED" in result.stderr
    assert "ROLLED BACK" not in result.stdout
    assert stubs.calls("npx") == [], "nothing may be deployed when there is no target"


def test_rollback_refuses_a_target_that_is_not_a_digest(stubs: Stubs) -> None:
    stubs.running(NEW)
    result = stubs.run(
        "rollback", "123456789012.dkr.ecr.eu-west-1.amazonaws.com/svc:latest", "dev", FUNCTION
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "not a digest pin" in result.stderr
    assert stubs.calls("npx") == []


def test_rollback_fails_when_the_redeploy_fails(stubs: Stubs) -> None:
    stubs.running(NEW)
    result = stubs.run("rollback", OLD, "dev", FUNCTION, STUB_NPX_EXIT="1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ROLLED BACK" not in result.stdout


def test_rollback_fails_when_the_redeploy_did_not_land(stubs: Stubs) -> None:
    """The deploy command exiting 0 is transport; the function running OLD is the outcome."""
    stubs.running(NEW)
    result = stubs.run("rollback", OLD, "dev", FUNCTION, STUB_NPX_NO_EFFECT="1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"runs '{NEW}'" in result.stderr
    assert "ROLLED BACK" not in result.stdout


@pytest.mark.parametrize("args", [[], ["deploy"], ["rollback", OLD, "dev"]])
def test_a_malformed_call_refuses(stubs: Stubs, args: list[str]) -> None:
    result = stubs.run(*args)
    assert result.returncode == 2, result.stdout + result.stderr
    assert stubs.calls("npx") == []
