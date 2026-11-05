"""`--collect-only` must not bring the real profile's resources up.

WHAT IT COST. `real_profile.pytest_configure` ran a full `docker compose up
--wait` and `down -v` in every pytest process, including one that only
collects. `prove_seeded_tests.py` counts each arm's selection with
`--collect-only` before every baseline, mutant and restore run, so half of its
Docker cycles were for runs that execute no test: about 10 s each, fifteen per
`--profile fake` proof.

HOW THIS OBSERVES IT. A fake `docker` placed first on PATH records every
invocation and exits 1, so no container starts wherever this runs, CI
included, and the bring-up it would have attempted is still visible. The
control arm runs the same tree WITHOUT `--collect-only` and requires the
recorder to have been called: without it, an arm that never reached the plugin
-- a wrong plugin name, a PATH the subprocess did not use -- would pass the
absence assertion by recording nothing.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
KIT_TESTING = REPOSITORY_ROOT / "service-kit" / "testing"
KIT_VENDOR = REPOSITORY_ROOT / "service-kit" / "vendor"


#: The default recorder: every docker call fails, so nothing ever starts.
REFUSE_EVERYTHING = "exit 1\n"


def _run(
    tmp_path: Path,
    *extra: str,
    docker: str = REFUSE_EVERYTHING,
    env_extra: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run a one-test tree with the kit plugin loaded and a recording `docker`.

    `docker` is the shell body that follows the recording line, so an arm can
    decide which subcommand succeeds; `env_extra` is applied after the escapes
    below are popped.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "docker-calls.log"
    fake = bin_dir / "docker"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{record}"\n{docker}')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    tests = tmp_path / "tree"
    tests.mkdir()
    (tests / "test_trivial.py").write_text("def test_trivial():\n    assert True\n")

    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    env["PYTHONPATH"] = os.pathsep.join(
        [str(KIT_TESTING), str(KIT_VENDOR), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    # Neither escape may be inherited: one skips the bring-up for a reason of its
    # own, the other turns the refused bring-up into a usage error.
    env.pop("FOUNDRY_TEST_DATABASE_URL", None)
    env.pop("REQUIRE_REAL_RESOURCES", None)
    env.pop("FOUNDRY_KEEP_RESOURCES", None)
    env.update(env_extra or {})

    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "-p", "no:cacheprovider",
            "-p", "foundry_testkit.real_profile",
            "-q", *extra, str(tests),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=REPOSITORY_ROOT,
        env=env,
    )
    return result, record.read_text() if record.exists() else ""


def test_collect_only_does_not_bring_resources_up(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "--collect-only")

    assert result.returncode == 0, f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    assert calls == "", (
        "a --collect-only run invoked docker, so it still pays a bring-up for a run "
        f"that executes no test:\n{calls}"
    )


def test_the_recorder_sees_a_bring_up_when_tests_run(tmp_path: Path) -> None:
    """The positive control: the same vehicle, tests executed, docker reached."""
    result, calls = _run(tmp_path)

    assert result.returncode == 0, f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    assert calls, (
        "the recording docker was never invoked by a run that executes tests, so this "
        "vehicle cannot see a bring-up and its collect-only arm proves nothing:\n"
        f"{result.stdout[-2000:]}"
    )


# -- a start that fails AFTER `up` still tears down ---------
#
# Measured 2026-09-27: compose projects named `shp-foundry-probe-service-<hash>`
# still running days after the pytest basetemp that held their compose file was
# rotated away. The plugin claimed its stack only after a COMPLETE start, so an
# `up --wait` that timed out on a loaded host, or a port it then refused, left
# containers running that `pytest_unconfigure` never knew it owned. These arms
# drive the real plugin through a real pytest process; the recorder is the only
# fake, so the down they look for is the one the plugin itself issues.

#: `up` succeeds and every published port reads as 0 -- the refusal
#: `_resolve_published_port` raises AFTER containers would exist.
UP_THEN_REFUSE_THE_PORT = 'case "$*" in *" port "*) echo "0.0.0.0:0";; esac\nexit 0\n'


def _downs(calls: str) -> list[str]:
    from foundry_testkit.real_profile import compose_project_name

    project = compose_project_name(REPOSITORY_ROOT)
    return [
        line for line in calls.splitlines()
        if line.startswith(f"compose -p {project} ") and line.endswith(" down -v --remove-orphans")
    ]


def test_a_failed_up_is_still_torn_down(tmp_path: Path) -> None:
    """`up` exits non-zero: the arm is skipped, and the half-started stack is not left."""
    result, calls = _run(tmp_path)

    assert result.returncode == 0, f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    assert " up -d --wait " in calls, f"the arm never reached `up`, so it proves nothing:\n{calls}"
    assert _downs(calls), f"`up` was issued and nothing tore it down:\n{calls}"


def test_a_start_that_raises_after_up_is_still_torn_down(tmp_path: Path) -> None:
    """`up` succeeds, then the start raises: the case the leaked stacks came from."""
    result, calls = _run(tmp_path, docker=UP_THEN_REFUSE_THE_PORT)

    assert result.returncode == 0, f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    assert "publishes no usable host port" in result.stdout, result.stdout[-2000:]
    assert _downs(calls), f"the stack `up` started was left running:\n{calls}"


def test_a_required_arm_that_cannot_start_still_tears_down(tmp_path: Path) -> None:
    """`REQUIRE_REAL_RESOURCES=1` turns the failure into a `UsageError` raised
    INSIDE `pytest_configure`; `pytest_unconfigure` must still run."""
    result, calls = _run(
        tmp_path, docker=UP_THEN_REFUSE_THE_PORT, env_extra={"REQUIRE_REAL_RESOURCES": "1"}
    )

    assert result.returncode == 4, f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    assert _downs(calls), f"a usage error left the stack `up` started running:\n{calls}"


def test_keep_resources_still_keeps_them(tmp_path: Path) -> None:
    """The negative control: the one escape that is meant to leave a stack up.

    Without it, the arms above cannot tell "tears down on failure" from "tears
    down unconditionally", and the recorder is never shown to report an absence.
    """
    result, calls = _run(
        tmp_path, docker=UP_THEN_REFUSE_THE_PORT, env_extra={"FOUNDRY_KEEP_RESOURCES": "1"}
    )

    assert result.returncode == 0, f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    assert " up -d --wait " in calls, calls
    assert _downs(calls) == [], f"FOUNDRY_KEEP_RESOURCES=1 was ignored:\n{calls}"
