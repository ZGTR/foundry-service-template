"""The kit must load without pytest-xdist, and nothing asserted that.

WHAT WENT WRONG. `real_profile.py` defines `pytest_configure_node`, which is
xdist's hookspec. pluggy validates hook NAMES when a plugin is registered, so
with no xdist the name is unknown and the run dies with
`PluginValidationError: unknown hook 'pytest_configure_node'` at exit 3 --
before a single test executes. Not a skip, not a failing test: an INTERNALERROR
over the whole suite. The module docstring claimed "a run with no xdist takes
the same path in-process", and there was no such path.

It was reachable. `pytest-xdist>=3.6` is in `requirements/test.lock`, which is
what CI installs, and was absent from `pyproject.toml`'s `dev` extra -- so
installing `.[aws,jwt,dev]` produced a service whose suite could not start at
all. Two lists for one thing, and the stale one bricked the suite.

WHY `-p no:xdist` IS THE RIGHT VEHICLE. It reproduces the failure exactly while
xdist is INSTALLED, so this test runs in ordinary CI with no second virtualenv:
pluggy's complaint is about a missing hookSPEC, and a blocked plugin
contributes none. Measured both ways -- in an xdist-free venv the unfixed hook
gives exit 3 and the fixed one gives exit 0, and under `-p no:xdist` with xdist
installed the unfixed hook gives the identical exit 3 and error text.

That equivalence is also why the obvious fix is wrong. Guarding the definition
on `find_spec("xdist")` clears the not-installed case and leaves `-p no:xdist`
at exit 3, because the module is importable when the plugin is blocked.
`optionalhook=True` keys on the spec rather than the module.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
KIT_TESTING = REPOSITORY_ROOT / "service-kit" / "testing"
KIT_VENDOR = REPOSITORY_ROOT / "service-kit" / "vendor"


def _kit_on_the_path() -> dict[str, str]:
    """PYTHONPATH for the subprocess, because its rootdir is not this repository.

    The parent run gets `foundry_testkit` from `[tool.pytest.ini_options]
    pythonpath`, and that is read from the rootdir pytest resolves -- which for a
    subprocess pointed at `tmp_path` is `tmp_path`, not here. Inheriting the
    parent's `sys.path` would be wrong for the same reason it is wrong elsewhere
    in this suite: it makes the test's subject depend on how the parent happened
    to be launched.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(KIT_TESTING), str(KIT_VENDOR), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return env

#: The exact text pluggy emits. Asserted so a run that fails for some OTHER
#: reason cannot be read as this defect reproducing, and vice versa.
PLUGGY_COMPLAINT = "unknown hook 'pytest_configure_node'"


def _run_with_xdist_blocked(tmp_path: Path, *, plugin: str) -> subprocess.CompletedProcess[str]:
    """Collect a one-test tree with the kit plugin loaded and xdist blocked."""
    (tmp_path / "test_trivial.py").write_text("def test_trivial():\n    assert True\n")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            "-p",
            plugin,
            "--collect-only",
            "-q",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=REPOSITORY_ROOT,
        env=_kit_on_the_path(),
    )


def test_the_real_profile_plugin_loads_with_xdist_blocked(tmp_path: Path) -> None:
    """The arm the defect would fail.

    `--collect-only` is deliberate: the failure is at plugin REGISTRATION, so it
    happens before any test runs and there is no need to bring a database up to
    observe it. Keeping the real profile's resources out of this test is what
    makes it cheap enough to sit in the default tier.
    """
    result = _run_with_xdist_blocked(tmp_path, plugin="foundry_testkit.real_profile")

    assert PLUGGY_COMPLAINT not in (result.stdout + result.stderr), (
        "registering foundry_testkit.real_profile with xdist blocked produced pluggy's "
        f"unknown-hook error, so the plugin still hard-requires xdist:\n{result.stdout[-2000:]}\n"
        f"{result.stderr[-2000:]}"
    )
    assert result.returncode == 0, (
        f"collection exited {result.returncode} with xdist blocked:\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )


def test_the_vehicle_can_actually_detect_the_defect(tmp_path: Path) -> None:
    """The positive control, and without it the arm above proves nothing.

    A `-p no:xdist` run that never reached pluggy's validation -- a typo in the
    plugin name, a pytest that ignored the flag, a subprocess that failed for an
    unrelated reason -- would pass the assertion above by not containing the
    error string. So an UNFIXED hook of the same shape is registered here and
    required to produce exactly the complaint the other arm rules out.

    The fixture is a module written into `tmp_path` rather than a tracked file,
    because a tracked plugin module carrying an unguarded xdist hook is exactly
    the defect under test and any tool that imported it would trip over it.
    """
    plugin_dir = tmp_path / "vehicle"
    plugin_dir.mkdir()
    (plugin_dir / "unguarded_xdist_hook.py").write_text(
        "def pytest_configure_node(node) -> None:\n    pass\n"
    )
    (tmp_path / "test_trivial.py").write_text("def test_trivial():\n    assert True\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            "-p",
            "unguarded_xdist_hook",
            "--collect-only",
            "-q",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=plugin_dir,
    )

    assert PLUGGY_COMPLAINT in (result.stdout + result.stderr), (
        "the -p no:xdist vehicle did NOT reproduce the unknown-hook error against a "
        "deliberately unguarded hook, so it cannot be trusted to have cleared the real "
        f"plugin either:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )
    assert result.returncode != 0, "the unguarded control exited 0; it should have failed"
