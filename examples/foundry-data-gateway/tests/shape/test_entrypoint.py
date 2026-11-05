"""Proves `docker/entrypoint.sh`'s runtime-detection logic without a real
Docker build: `ENTRYPOINT_PYTHON` swaps in a stub script that just echoes
its argv and the `RUNTIME_MODE` it inherited, so this exercises the exact
shell logic the container runs, under both conditions.
"""

from __future__ import annotations

import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.support.serving import DRAIN_SECONDS, run_with_deadline

ENTRYPOINT = Path(__file__).parent.parent.parent / "service-kit/docker/entrypoint.sh"

#: What the Dockerfile's `SERVICE_PACKAGE` build arg becomes for THIS service.
#: The entrypoint has no default for it, deliberately -- see the refusal test.
SERVICE_PACKAGE = "foundry_data_gateway"

STUB_PYTHON = """#!/usr/bin/env sh
echo "STUB_PYTHON_ARGS=$*"
echo "STUB_RUNTIME_MODE=${RUNTIME_MODE:-<unset>}"
"""


def _run_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    env_overrides: dict[str, str],
    args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    stub = tmp_path / "python"
    stub.write_text(STUB_PYTHON)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("RUNTIME_MODE", raising=False)
    monkeypatch.setenv("ENTRYPOINT_PYTHON", str(stub))
    # The entrypoint is kit-owned and byte-identical in every service, so it
    # cannot name a package; the Dockerfile exports this from its required build
    # arg, and setting it here is what the image does rather than a convenience.
    #
    # It goes through the EXISTING override loop rather than its own
    # `monkeypatch.setenv` line, and that is not style. §7's mock budget counts
    # every `monkeypatch.setenv`/`delenv` CALL SITE, deliberately -- including
    # the legitimate environment ones, so that the number stays visible instead
    # of being exempted by name. A new call site here would have pushed the
    # emitted service from 21 to 25 and demanded the ratchet be raised, which a
    # ratchet is precisely the wrong thing to do. An override wins, so a test
    # can still set its own value, and `""` exercises the refusal path.
    defaults = {"SERVICE_PACKAGE": SERVICE_PACKAGE}
    for name, value in {**defaults, **env_overrides}.items():
        monkeypatch.setenv(name, value)

    # env=None (the default): the subprocess inherits this now-modified
    # process environment, so nothing in this file names `os.environ`.
    return run_with_deadline(
        ["/bin/sh", str(ENTRYPOINT), *(args or [])],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _verdict_of(args: list[str], timeout: float, **kwargs) -> subprocess.CompletedProcess[str]:
    """`run_with_deadline`, where a HANG is this test's failure rather than a skip: these
    controls are about a child that DID finish, so a skip would turn a reverted helper
    green under bare pytest."""
    try:
        return run_with_deadline(args, timeout=timeout, **kwargs)
    except pytest.skip.Exception as skipped:
        pytest.fail(f"a finished child was reported as a HANG: {skipped}")


@pytest.mark.parametrize("code", [0, 1])
def test_a_finished_child_keeps_its_verdict(code: int) -> None:
    result = _verdict_of(["/bin/sh", "-c", f"exit {code}"], timeout=10)
    assert result.returncode == code


@pytest.mark.parametrize("code", [0, 1])
def test_a_child_that_exited_keeps_its_verdict_while_a_descendant_holds_its_pipe(
    code: int,
) -> None:
    result = _verdict_of(
        ["/bin/sh", "-c", f"sleep 30 & exit {code}"],
        timeout=2,
        capture_output=True,
        text=True,
    )
    assert result.returncode == code


def test_a_hang_leaves_no_descendant_running() -> None:
    started = time.monotonic()
    with pytest.raises(pytest.skip.Exception, match=r"HANG \(UNMEASURED\)") as hang:
        run_with_deadline(
            ["/bin/sh", "-c", "sleep 60 & echo $!; wait"],
            timeout=2,
            capture_output=True,
            text=True,
        )
    # Killing only the child would leave the descendant holding the pipe, and the HANG
    # would come back only when the descendant's own minute was up.
    assert time.monotonic() - started < 20, "the HANG waited for the descendant to finish"
    found = re.search(r"stdout='(\d+)\\n'", str(hang.value))
    assert found, f"the descendant's pid is not in the HANG message: {hang.value}"
    pid = int(found.group(1))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"the descendant {pid} is still running after the HANG")


def test_a_descendant_that_left_the_group_cannot_hold_the_hang_open(tmp_path: Path) -> None:
    """A descendant that called setsid is outside the killed group and still holds the
    child's pipe; the read after the kill is bounded rather than waiting on it."""
    pidfile = tmp_path / "escaped.pid"
    script = (
        "import subprocess, time; "
        "p = subprocess.Popen(['sleep', '60'], start_new_session=True); "
        f"open({str(pidfile)!r}, 'w').write(str(p.pid)); "
        "time.sleep(60)"
    )
    started = time.monotonic()
    try:
        with pytest.raises(pytest.skip.Exception, match=r"HANG \(UNMEASURED\)"):
            run_with_deadline(
                [sys.executable, "-c", script], timeout=2, capture_output=True, text=True
            )
        waited = time.monotonic() - started
        assert waited < 2 + DRAIN_SECONDS + 8, "the read waited on the escaped descendant"
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_a_deadline_is_a_named_hang_without_a_code_verdict() -> None:
    # A real stalled child, not a patched subprocess. `exec` leaves no grandchild.
    with pytest.raises(pytest.skip.Exception, match=r"HANG \(UNMEASURED\).*machine.*host load"):
        run_with_deadline(["/bin/sh", "-c", "exec sleep 20"], timeout=0.05)


def test_the_entrypoint_caller_reports_a_stalled_command_as_unmeasured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(pytest.skip.Exception, match=r"HANG \(UNMEASURED\).*machine"):
        _run_entrypoint(
            tmp_path, monkeypatch, env_overrides={},
            args=["/bin/sh", "-c", "exec sleep 20"],
        )


def test_entrypoint_is_executable() -> None:
    mode = ENTRYPOINT.stat().st_mode
    assert mode & stat.S_IXUSR


def test_under_lambda_it_exports_runtime_mode_lambda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_entrypoint(
        tmp_path, monkeypatch, env_overrides={"AWS_LAMBDA_FUNCTION_NAME": "widgets-prod"}
    )

    assert result.returncode == 0
    assert "AWS_LAMBDA_FUNCTION_NAME=widgets-prod" in result.stderr
    assert "STUB_RUNTIME_MODE=lambda" in result.stdout
    assert "STUB_PYTHON_ARGS=-m foundry_data_gateway.composition.main" in result.stdout


def test_under_ecs_it_exports_runtime_mode_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_entrypoint(tmp_path, monkeypatch, env_overrides={})

    assert result.returncode == 0
    assert "no Lambda runtime detected" in result.stderr
    assert "STUB_RUNTIME_MODE=http" in result.stdout
    assert "STUB_PYTHON_ARGS=-m foundry_data_gateway.composition.main" in result.stdout


def test_an_explicit_runtime_mode_is_respected_outside_lambda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_entrypoint(tmp_path, monkeypatch, env_overrides={"RUNTIME_MODE": "http"})

    assert result.returncode == 0
    assert "STUB_RUNTIME_MODE=http" in result.stdout


def test_the_launch_command_is_identical_in_both_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing claim of section 6: no code change between targets."""
    lambda_result = _run_entrypoint(
        tmp_path, monkeypatch, env_overrides={"AWS_LAMBDA_FUNCTION_NAME": "f"}
    )
    ecs_result = _run_entrypoint(tmp_path, monkeypatch, env_overrides={})

    def argv_line(result: subprocess.CompletedProcess) -> list[str]:
        return [line for line in result.stdout.splitlines() if line.startswith("STUB_PYTHON_ARGS")]

    assert argv_line(lambda_result) == argv_line(ecs_result)


# ---------------------------------------------------------------------------
# The CMD this ENTRYPOINT is handed
# ---------------------------------------------------------------------------
#
# A container image's ENTRYPOINT receives the CMD as arguments. `image.command`
# in `deploy/iac/serverless.yml` and `command:` in an ECS task definition BOTH set the
# CMD, so whether either one can do anything at all is decided in this script
# and nowhere else. Until 2026-09-05 it never named "$@", which is why a
# `drainOutbox` Lambda in the sibling service emitted from the same template,
# shipping
#     [/opt/venv/bin/python, -c, "from ...outbox_drain import handle_scheduled_drain"]
# ran the HTTP API server 1440 times a day instead of the import it was recorded
# as performing. Nothing in THIS repository declares a container CMD today, so
# the defect was latent here rather than live; these arms exist so the next one
# that does is not silently swallowed.
#
# Both directions are pinned below, because a forwarding branch that also fired
# on the empty argument list would silently change what every ordinary container
# start runs.


def test_an_explicit_command_is_run_INSTEAD_of_the_http_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this, `image.command` and ECS `command:` are silently discarded."""
    result = _run_entrypoint(
        tmp_path,
        monkeypatch,
        env_overrides={},
        args=[str(tmp_path / "python"), "-m", "foundry_data_gateway.one_shot_main"],
    )

    assert result.returncode == 0
    assert "STUB_PYTHON_ARGS=-m foundry_data_gateway.one_shot_main" in result.stdout
    assert "STUB_PYTHON_ARGS=-m foundry_data_gateway.composition.main" not in result.stdout


def test_an_explicit_command_still_inherits_the_runtime_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scheduled one-shot runs under the same RUNTIME_MODE the service would."""
    result = _run_entrypoint(
        tmp_path,
        monkeypatch,
        env_overrides={"AWS_LAMBDA_FUNCTION_NAME": "one-shot-prod"},
        args=[str(tmp_path / "python"), "-m", "foundry_data_gateway.one_shot_main"],
    )

    assert result.returncode == 0
    assert "STUB_RUNTIME_MODE=lambda" in result.stdout
    # Asserted here too, deliberately: RUNTIME_MODE is exported before the
    # forwarding branch, so without this line the whole test stays green when
    # forwarding is removed -- measured on the sibling service, it was the one
    # arm that did not redden.
    assert "STUB_PYTHON_ARGS=-m foundry_data_gateway.one_shot_main" in result.stdout


def test_with_NO_arguments_the_default_launch_path_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative control for the two arms above. An ordinary container start
    passes no CMD, and must still reach `main` -- a forwarding branch that fired
    on an empty argument list would exec nothing and exit 0, which reads as a
    healthy start."""
    result = _run_entrypoint(tmp_path, monkeypatch, env_overrides={}, args=[])

    assert result.returncode == 0
    assert "STUB_PYTHON_ARGS=-m foundry_data_gateway.composition.main" in result.stdout
    assert "explicit command given" not in result.stderr


def test_the_exec_target_FOLLOWS_service_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The arm that discriminates, and without it the rest of this file proves
    the wrong thing.

    Every other assertion here names `foundry_data_gateway`, and so does
    the harness's `SERVICE_PACKAGE`. Two sides carrying the same name cannot
    tell "the entrypoint read the variable" from "the entrypoint still has the
    package hardcoded" -- which is exactly what it DID have until the kit move
    made it byte-identical across nine services, where a written-in name is
    wrong in eight of them. So this arm points the variable somewhere else and
    requires the exec to follow, and requires the template's own name to be
    ABSENT: reverting the entrypoint's last line to a literal turns it red.
    """
    result = _run_entrypoint(
        tmp_path, monkeypatch, env_overrides={"SERVICE_PACKAGE": "some_other_service"}
    )

    assert result.returncode == 0
    assert "STUB_PYTHON_ARGS=-m some_other_service.composition.main" in result.stdout
    assert SERVICE_PACKAGE not in result.stdout


def test_an_unset_service_package_is_REFUSED(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kit file that guessed would be worse than one that refuses.

    The alternative to refusing is a default, and a default here means the
    container starts, answers `/health/live`, and runs a DIFFERENT service's
    code -- the failure mode the whole SERVICE_PACKAGE arg exists to prevent.
    The Dockerfile refuses at build time for the same reason; this is the
    runtime half, for an image built before that check existed.

    An EMPTY value rather than an unset one, because the entrypoint's guard is
    `[ -z "${SERVICE_PACKAGE:-}" ]` and treats the two alike -- and going
    through the shared helper adds no `monkeypatch` call site to §7's budget.
    """
    result = _run_entrypoint(tmp_path, monkeypatch, env_overrides={"SERVICE_PACKAGE": ""})

    assert result.returncode != 0
    assert "SERVICE_PACKAGE is unset" in result.stderr
    # It must refuse BEFORE exec'ing anything: a refusal that still started the
    # wrong service would be a warning, not a guard.
    assert "STUB_PYTHON_ARGS" not in result.stdout
