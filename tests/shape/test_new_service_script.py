"""The test `new_service.sh` cited for months without it existing.

The script's header named this file as the proof that its output works. `test -f`
said ABSENT. A citation naming a test reads exactly like a test that ran, and
nothing goes red, because a missing test is missing rather than failing -- so the
one script the whole service extraction depends on had no mechanical evidence it
had ever produced a working package. See
`knowledge/wiki/a-citation-is-not-a-proof.md`.

The assertion that matters is the PASSED COUNT. A scaffolder that emitted an
empty directory, or one whose tests no longer collect under the new module name,
would exit 0 from pytest with "no tests ran" -- the precise green this file
exists to refuse.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from foundry_testkit.real_profile import DATABASE_URL_ENV, _tear_down, compose_project_name

from tests.support.serving import run_with_deadline

TEMPLATE_DIR = Path(__file__).resolve().parents[2]
SCRIPT = TEMPLATE_DIR / "scripts" / "new_service.sh"
MONOREPO_MARKER = "_monorepo_sibling_marker.py"


# `new_service.sh` emits a whole service and takes minutes, not seconds: measured
# 299s on a loaded workstation (load average ~5) at two different commits, where the
# former 180s limit turned 20 of this file's 22 tests into TimeoutExpired -- a red
# that reads as a code defect and says nothing about the script. 900s is a 3x margin
# over that measurement. FOUNDRY_NEW_SERVICE_TIMEOUT overrides it for a slower host.
NEW_SERVICE_TIMEOUT = int(os.environ.get("FOUNDRY_NEW_SERVICE_TIMEOUT", "900"))


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=False, timeout=NEW_SERVICE_TIMEOUT
    )


def _emitted_service_environment() -> dict[str, str]:
    """This process's environment for a command run INSIDE an emitted service.

    Without `FOUNDRY_TEST_DATABASE_URL`. When this suite runs as a guest on a
    shared server, the variable would make the emitted service's own suite a second
    guest on the server this run's controller holds. The kit refuses that (one run
    per server), and before it refused, the nested run dropped this run's template
    database. The emitted service brings up its own stack instead, which is also
    what these tests are about: that a fresh emit can stand on its own.
    """
    return {key: value for key, value in os.environ.items() if key != DATABASE_URL_ENV}


@pytest.fixture(autouse=True)
def _no_emitted_stack_outlives_its_test(tmp_path: Path) -> Iterator[None]:
    yield
    _down_emitted_stacks(tmp_path)


def _down_emitted_stacks(tmp_path: Path) -> None:
    """Tear down whatever an emitted service's own suite left running.

    Several tests here run an emitted service's pytest, whose kit plugin starts a
    compose project named after the emitted directory. The plugin tears it down in
    `pytest_unconfigure`, which never runs when `subprocess.run(timeout=...)`
    SIGKILLs that child -- and once pytest rotates this basetemp the compose file
    is gone, so nothing can `down` the stack afterwards. Measured 2026-09-27: four
    `shp-foundry-probe-service-<hash>` projects still running days later. The
    autouse fixture above is the caller that can kill the run, so it owns that
    teardown, through the kit's own scoped `_tear_down`.
    """
    if shutil.which("docker") is None:
        return
    for compose_file in tmp_path.glob("*/deploy/compose.yml"):
        root = compose_file.parents[1]
        project = compose_project_name(root)
        listed = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
            capture_output=True, text=True, check=False, timeout=60,
        )
        if listed.stdout.strip():
            _tear_down(project, root)


def test_a_stack_an_emitted_suite_left_running_is_torn_down(tmp_path: Path) -> None:
    """The finalizer, against a recording `docker` that reports ONE live project.

    `leaked` stands for an emitted service whose suite was killed with its stack
    up; `idle` for one whose suite never started anything, which must cost no
    `down`. Without the idle half, a finalizer that downed everything would pass.
    It runs in a child process, so the fake `docker` reaches it through an
    explicit PATH rather than a patched one.
    """
    record = tmp_path / "docker-calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        f'#!/bin/sh\necho "$@" >> "{record}"\n'
        'case "$*" in "ps "*"shp-foundry-leaked-"*) echo 0123abcd;; esac\nexit 0\n'
    )
    fake.chmod(0o755)
    emitted = tmp_path / "emitted"
    for name in ("leaked", "idle"):
        root = emitted / name
        (root / "deploy").mkdir(parents=True)
        (root / "deploy" / "compose.yml").write_text("services: {}\n")
        (root / "pyproject.toml").write_text(f'[project]\nname = "foundry-{name}"\n')

    env = {key: value for key, value in os.environ.items() if key != "FOUNDRY_KEEP_RESOURCES"}
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    env["PYTHONPATH"] = os.pathsep.join(
        str(path) for path in (
            Path(__file__).parent,
            TEMPLATE_DIR / "service-kit" / "testing",
            TEMPLATE_DIR / "service-kit" / "vendor",
            TEMPLATE_DIR,
        )
    )
    child = subprocess.run(
        [sys.executable, "-c",
         "import sys, pathlib, test_new_service_script as t; "
         "t._down_emitted_stacks(pathlib.Path(sys.argv[1]))", str(emitted)],
        capture_output=True, text=True, check=False, timeout=120, env=env,
    )
    assert child.returncode == 0, child.stderr

    calls = record.read_text().splitlines()
    downs = [line for line in calls if line.endswith(" down -v --remove-orphans")]
    leaked = compose_project_name(emitted / "leaked")
    assert [line.split()[2] for line in downs] == [leaked], record.read_text()


def test_the_script_this_package_cites_actually_exists() -> None:
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_a_foundry_name_is_accepted_and_an_unprefixed_one_is_not(tmp_path: Path) -> None:
    """Both directions of the validator.

    The positive control is the half that regressed: an earlier rename moved
    every package name and left `^az-` here, so every name the extraction needs was
    refused by the one script it needs.
    """
    accepted = _run([str(SCRIPT), "foundry-probe-service", str(tmp_path / "accepted")])
    assert accepted.returncode == 0, f"a foundry-* name must be accepted:\n{accepted.stderr}"

    refused = _run([str(SCRIPT), "no-prefix-here", str(tmp_path / "refused")])
    assert refused.returncode != 0, "an unprefixed name must still be refused"
    assert "foundry-<kebab-case>" in refused.stderr


def _monorepo_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Stand the template up where the Foundry monorepo pins it, whatever checkout runs this.

    The template is its own repository AND a submodule at
    ., and the script behaves
    differently in the two. Building the monorepo shape here -- a sibling
    foundry-contracts package plus a symlink to THIS working tree -- runs the
    monorepo branch from a standalone clone too, so neither branch is a skip in
    the other checkout. The sibling carries a marker module the template's own
    vendor/ does not, which is how the tests tell the two sources apart.
    """
    packages = tmp_path / "mono" / "platform" / "packages"
    sibling = packages / "foundry-contracts" / "src" / "foundry_contracts"
    shutil.copytree(
        TEMPLATE_DIR / "service-kit" / "vendor" / "foundry_contracts",
        sibling,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (sibling / MONOREPO_MARKER).write_text("SOURCE = 'monorepo sibling'\n")
    (packages / "foundry-service-template").symlink_to(TEMPLATE_DIR, target_is_directory=True)
    # A real repository, because monorepo mode vendors from git at HEAD rather than
    # from the working tree: the record it writes names a commit.
    mono = tmp_path / "mono"
    for args in (["init", "-q"], ["add", "platform/packages/foundry-contracts"],
                 ["commit", "-q", "-m", "sibling contracts"]):
        done = _run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                     "-C", str(mono), *args])
        assert done.returncode == 0, done.stderr
    return packages, packages / "foundry-service-template" / "scripts" / "new_service.sh"


def test_the_destination_may_not_be_inside_packages(tmp_path: Path) -> None:
    """The defect: a correctly-renamed copy in the wrong place."""
    packages, script = _monorepo_layout(tmp_path)
    result = _run([str(script), "foundry-probe-service", str(packages / "foundry-probe-x")])
    assert result.returncode != 0, "emitting into packages/ must be refused"
    assert "self-contained" in result.stderr
    assert not (packages / "foundry-probe-x").exists(), "the refused emit still wrote files"


def test_monorepo_mode_vendors_the_sibling_contracts_package(tmp_path: Path) -> None:
    """Inside the monorepo the sibling package is the authority, not the template's copy."""
    _, script = _monorepo_layout(tmp_path)
    dest = tmp_path / "svc"
    result = _run([str(script), "foundry-probe-service", str(dest)])
    assert result.returncode == 0, result.stderr
    assert "(monorepo mode)" in result.stdout, result.stdout
    assert (dest / "service-kit" / "vendor" / "foundry_contracts" / MONOREPO_MARKER).is_file(), (
        "monorepo mode kept the template's vendored copy instead of the sibling package"
    )


def test_monorepo_mode_records_what_it_vendored(tmp_path: Path) -> None:
    """Bytes and VENDOR.json record are written in ONE step.

    Before, monorepo mode `cp`-ed the sibling's bytes over the template's copy and left
    the VENDOR.json that arrived with the template describing the template's copy, so
    an emitted service could start life with its own digest gate red. The emitted
    service's gate is the reader, run exactly as the service would run it. The second
    half: an UNCOMMITTED edit to the sibling must not be vendored, because the record
    names a commit and that commit does not contain it.
    """
    packages, script = _monorepo_layout(tmp_path)
    sibling = packages / "foundry-contracts" / "src" / "foundry_contracts"
    (sibling / "_uncommitted_edit.py").write_text("DRAFT = True\n")
    dest = tmp_path / "svc"
    result = _run([str(script), "foundry-probe-service", str(dest)])
    assert result.returncode == 0, result.stderr
    vendored = dest / "service-kit" / "vendor" / "foundry_contracts"
    assert (vendored / MONOREPO_MARKER).is_file(), "the committed sibling was not vendored"
    assert not (vendored / "_uncommitted_edit.py").exists(), (
        "an uncommitted edit was vendored under a revision that does not contain it"
    )
    gate = _run(["python3", str(dest / "service-kit" / "gates" / "check_vendor_digests.py")])
    assert gate.returncode == 0, gate.stdout + gate.stderr
    record = json.loads((dest / "service-kit" / "vendor" / "VENDOR.json").read_text())
    head = _run(["git", "-C", str(tmp_path / "mono"), "rev-parse", "HEAD"]).stdout.strip()
    assert record["packages"]["foundry_contracts"]["monorepo_revision"] == head


def test_standalone_mode_vendors_the_templates_own_copy(tmp_path: Path) -> None:
    """A standalone clone has no sibling package; its own vendor/ must travel instead.

    Skipped, by name, only when THIS checkout is the monorepo submodule -- the
    monorepo branch is covered above in every checkout, and this branch in every
    standalone one, which is where the template's own CI runs.
    """
    if (TEMPLATE_DIR.parent / "foundry-contracts" / "src" / "foundry_contracts").is_dir():
        pytest.skip("this checkout is the monorepo submodule; standalone mode is unreachable here")
    dest = tmp_path / "svc"
    result = _run([str(SCRIPT), "foundry-probe-service", str(dest)])
    assert result.returncode == 0, result.stderr
    assert "(standalone mode)" in result.stdout, result.stdout
    vendored = dest / "service-kit" / "vendor" / "foundry_contracts"
    assert (vendored / "principal.py").is_file(), "the template's own vendor/ did not travel"
    assert not (vendored / MONOREPO_MARKER).exists()


def test_the_copy_excludes_untracked_build_output(tmp_path: Path) -> None:
    """Enumerating tracked files, rather than deny-listing caches, is the point.

    `.ruff_cache` is present in the template tree today and was absent from the
    old --exclude list, so it was copied into every emitted service.
    """
    dest = tmp_path / "svc"
    result = _run([str(SCRIPT), "foundry-probe-service", str(dest)])
    assert result.returncode == 0, result.stderr

    leaked = [
        p.relative_to(dest)
        for p in dest.rglob("*")
        if any(
            part in {".ruff_cache", ".pytest_cache", "__pycache__", ".terraform", ".venv"}
            for part in p.parts
        )
    ]
    assert leaked == [], f"untracked build output was copied into the service: {leaked}"


def test_no_reference_to_the_template_name_survives(tmp_path: Path) -> None:
    """Everything the SERVICE owns is renamed; the kit it does not own is left alone.

    `service-kit/` is excluded from the rename by design (01e7f76, merged as
    c412cbc): the kit is template-owned and hash-pinned by MANIFEST.json, so a
    rewritten byte there forks it and the emitted service fails check_kit_drift on
    arrival. What the kit names is its ORIGIN -- sync.py's DEFAULT_TEMPLATE_URL, the
    drift gate's TEMPLATE_MODULE, which it needs to normalise a placement's rename
    back -- and renaming those was the defect, not the fix. So the survivor scan
    covers the service-owned tree, and the next test proves the kit arrived intact.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    scanned = [
        p for p in dest.rglob("*") if p.is_file() and p.relative_to(dest).parts[0] != "service-kit"
    ]
    # A floor, so an exclusion that swallowed the tree cannot pass as a clean scan.
    assert len(scanned) > 50, f"only {len(scanned)} service-owned file(s) scanned"
    survivors = [
        p.relative_to(dest)
        for p in scanned
        if "foundry_service_template" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert survivors == [], f"template module name survived the rename in: {survivors}"
    assert (dest / "src" / "foundry_probe_service").is_dir()


def test_the_emitted_kit_is_untouched_and_passes_its_own_drift_gate(tmp_path: Path) -> None:
    """The other half of the exclusion above, asserted rather than assumed.

    Two instruments, because they fail for different reasons. Byte identity with
    the template's tracked kit says the RENAME left the kit alone, independent of
    the manifest. The emitted service's own check_kit_drift says the manifest the
    template ships DESCRIBES the kit it ships -- a template whose kit changed
    without `sync.py --emit-manifest` emits services that are red on arrival, and
    that gate only ran in CI, which has had no runner on this account.
    """
    dest = tmp_path / "svc"
    emitted = _run([str(SCRIPT), "foundry-probe-service", str(dest)])
    assert emitted.returncode == 0, emitted.stderr

    tracked = _run(["git", "ls-files", "-z", "service-kit"], cwd=TEMPLATE_DIR).stdout.split("\0")
    kit_files = [rel for rel in tracked if rel]
    assert len(kit_files) > 50, f"only {len(kit_files)} tracked kit file(s) -- is this a checkout?"
    # Monorepo mode replaces the vendored contracts with the sibling package on
    # purpose, so only that subtree may differ, and only in that mode.
    replaced = "service-kit/vendor/foundry_contracts/"
    monorepo = "(monorepo mode)" in emitted.stdout
    rewritten = [
        rel
        for rel in kit_files
        if not (monorepo and rel.startswith(replaced))
        and (dest / rel).read_bytes() != (TEMPLATE_DIR / rel).read_bytes()
    ]
    assert rewritten == [], f"new_service.sh rewrote template-owned kit file(s): {rewritten}"

    drift = subprocess.run(
        [sys.executable, str(dest / "service-kit" / "gates" / "check_kit_drift.py")],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert drift.returncode == 0, (
        f"the emitted service fails check_kit_drift on arrival (exit {drift.returncode}); "
        f"a stale MANIFEST.json is the usual cause -- re-run `sync.py --emit-manifest`:\n"
        f"{drift.stdout}\n{drift.stderr}"
    )


#: The seeded + e2e tier, by path. Every one of these was absent from the template
#: for six revisions after it was extracted into its own repository, and no signal
#: anywhere went red -- a missing test tier is silent by construction, which is
#: exactly why the set is named here rather than described.
SEEDED_TIER = (
    "service-kit/gates/check_seeded_tests.py",
    "scripts/prove_seeded_tests.py",
    "service-kit/testing/foundry_testkit/seeded_case.py",
    # The store inspector, both halves. The kit half is generic and the
    # `tests/support/` half is the service's own questions, and a service that
    # received one without the other has a seeded tier that cannot read its store
    # back -- which is a gap nothing else in this file would notice.
    "service-kit/testing/foundry_testkit/store_rows.py",
    "tests/support/store_rows.py",
    "tests/support/environment.py",
    "tests/seeded/test_widgets_api.py",
    "tests/e2e/widget_lifecycle/test_journey_create_to_list.py",
)


def test_the_emitted_service_carries_the_seeded_tier_and_its_gate_is_wired(
    tmp_path: Path,
) -> None:
    """The tier travels, and the gate that guards it can still say no.

    The existence half alone would be satisfied by six files that the emitted
    service's own gate never reads. So this also RUNS the emitted
    `check_seeded_tests.py` and then takes `tests/seeded/` away and runs it
    again: a gate green on a tree it cannot see is green for the wrong reason,
    and "found nothing" is this gate's own pass condition.

    The mutation is a directory rename ON DISK, because that is the medium this
    gate's verdict reads -- it walks the filesystem with `Path.is_dir`/`rglob`,
    and an emitted service is not a git checkout at all, so there is no index for
    an index-shaped control to touch.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    missing = [rel for rel in SEEDED_TIER if not (dest / rel).is_file()]
    assert missing == [], f"the seeded tier did not reach the emitted service: {missing}"
    assert "emitted the seeded + e2e test tier" in _run(
        [str(SCRIPT), "foundry-probe-service", str(tmp_path / "svc2")]
    ).stdout

    def gate() -> subprocess.CompletedProcess[str]:
        env = _emitted_service_environment()
        env["PYTHONPATH"] = os.pathsep.join(
        [
            str(dest / "src"),
            str(dest / "service-kit" / "vendor"),
            str(dest / "service-kit" / "testing"),
            str(dest),
        ]
    )
        return subprocess.run(
            [sys.executable, str(dest / "service-kit" / "gates" / "check_seeded_tests.py")],
            cwd=dest, capture_output=True, text=True, check=False, timeout=300, env=env,
        )

    green = gate()
    assert green.returncode == 0, f"emitted gate red on a correct emit:\n{green.stdout}"

    seeded = dest / "tests" / "seeded"
    seeded.rename(dest / "tests" / "_seeded_moved_away")
    try:
        red = gate()
    finally:
        (dest / "tests" / "_seeded_moved_away").rename(seeded)
    assert red.returncode != 0, (
        "the emitted gate stayed green with tests/seeded/ removed, so its green "
        f"verdict on the real tree proves nothing:\n{red.stdout}"
    )
    assert gate().returncode == 0, "the restore arm is red; the mutation was not undone"


def _passed_count_of_emitted_suite(
    dest: Path, tests: str = "tests", *, first_on_path: Path | None = None
) -> tuple[int, subprocess.CompletedProcess[str]]:
    """Run an already-emitted service's own suite and return its real passed count.

    Shared by every "the emit actually works" test below, whatever prefix produced
    it: the emitted repo is not installed, so its src/ and foundry-contracts are
    put on the path to run the suite in place, and the passed count is read from
    pytest's own summary line rather than the exit code alone, because an empty
    emit exits 0 with "no tests ran" -- the exact green this file exists to refuse.

    `tests` is the directory run, relative to the service. `first_on_path` is a
    directory put first on that run's PATH, for a stand-in command.
    """
    env = _emitted_service_environment()
    if first_on_path is not None:
        env["PATH"] = f"{first_on_path}{os.pathsep}{env['PATH']}"
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(dest / "src"),
            str(dest / "service-kit" / "vendor"),
            str(dest / "service-kit" / "testing"),
            str(dest),
        ]
    )

    run = run_with_deadline(
        [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", str(dest / tests)],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        env=env,
    )
    # The emitted entrypoint's deadline may have fired. A green pytest with that
    # named skip is not proof the whole emitted suite worked. Preserve a genuine
    # failure beside a hang, however: the child DID supply that verdict.
    if run.returncode == 0 and "HANG (UNMEASURED)" in run.stdout + run.stderr:
        pytest.skip(
            f"HANG (UNMEASURED): emitted suite has no complete verdict:\n{run.stdout}\n{run.stderr}"
        )
    match = re.search(r"(\d+) passed", run.stdout)
    assert match, f"no passed count in the emitted suite's output:\n{run.stdout}\n{run.stderr}"
    return int(match.group(1)), run


@pytest.mark.parametrize("wrong", [False, True])
def test_an_emitted_hang_is_not_a_pass_and_does_not_hide_a_failure(
    tmp_path: Path, wrong: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "test_probe.py").write_text(
        "import pytest\n"
        "def test_finished(): assert True\n"
        "def test_hang(): pytest.skip('HANG (UNMEASURED): machine deadline')\n"
        + ("def test_wrong(): assert False\n" if wrong else "")
    )
    if wrong:
        # A skip here would read green under bare pytest, so it is this control's failure.
        try:
            passed, result = _passed_count_of_emitted_suite(tmp_path, "test_probe.py")
        except pytest.skip.Exception as skipped:
            pytest.fail(f"a HANG hid the emitted suite's real failure: {skipped}")
        assert passed == 1
        assert result.returncode == 1
        assert "1 failed" in result.stdout
    else:
        with pytest.raises(pytest.skip.Exception, match="no complete verdict"):
            _passed_count_of_emitted_suite(tmp_path, "test_probe.py")


def test_the_emitted_service_passes_its_own_suite_with_a_NON_ZERO_count(tmp_path: Path) -> None:
    """The assertion the missing citation promised.

    Runs the emitted package's own tests and requires a real passed count. An
    empty emit, or one whose tests stopped collecting under the new module name,
    exits 0 with "no tests ran" -- which is the shape this whole bead is about.
    """
    dest = tmp_path / "svc"
    emitted = _run([str(SCRIPT), "foundry-probe-service", str(dest)])
    assert emitted.returncode == 0, emitted.stderr

    passed, run = _passed_count_of_emitted_suite(dest)
    assert passed > 0, "the emitted service's suite ran zero tests -- a green that proves nothing"
    assert run.returncode == 0, f"emitted suite failed ({passed} passed):\n{run.stdout[-3000:]}"


#: The README's estate opt-in, as the two edits a service makes to its own `deploy/`
#: files. `deploy/deploy_env.json`'s own comment names both variables. They go after
#: the two the emitted declaration already holds.
ESTATE_OPT_IN = (
    ("deploy/iac/serverless.yml", "  callerVpc: none\n", "  callerVpc: estate\n"),
    (
        "deploy/deploy_env.json",
        '    "INTERNAL_ZONE_SUFFIX": {"ssm": "/foundry/{stage}/network/internal-zone-suffix"}\n'
        "  }\n",
        '    "INTERNAL_ZONE_SUFFIX": {"ssm": "/foundry/{stage}/network/internal-zone-suffix"},\n'
        '    "ESTATE_CALLER_SG_ID": {"ssm": "/foundry/{stage}/network/estate-caller-sg-id"},\n'
        '    "PRIVATE_SUBNET_IDS": {"ssm": "/foundry/{stage}/network/private-subnet-ids"}\n'
        "  }\n",
    ),
)
#: What `deploy_env.py check dev` asks `aws` for in a service as emitted: the estate's
#: listener and zone, which the emitted declaration holds.
EMITTED_READS = [
    "ssm get-parameter --name /foundry/dev/network/internal-listener-arn "
    "--with-decryption --output json",
    "ssm get-parameter --name /foundry/dev/network/internal-zone-suffix "
    "--with-decryption --output json",
]
#: What the opt-in adds to that, in declaration order.
ESTATE_READS = [
    "ssm get-parameter --name /foundry/dev/network/estate-caller-sg-id "
    "--with-decryption --output json",
    "ssm get-parameter --name /foundry/dev/network/private-subnet-ids "
    "--with-decryption --output json",
]


def test_a_service_opted_in_to_the_estate_vpc_passes_its_shape_tests_and_starts_no_aws(
    tmp_path: Path,
) -> None:
    """Regression: `deploy/` is the service's own, and no shipped test may hold
    it to what the template's copy says.

    Two arms of `test_deploy_env_script.py` ran the kit's `deploy_env.py` over the
    service's own `deploy/` files. A service that followed the README's estate opt-in
    declares two variables there, so one arm was red on a correct service, and to get
    there it started the `aws` CLI twice from inside pytest. This emits one service
    and runs its whole `tests/shape` directory as emitted and again after the opt-in,
    each time with a stand-in `aws` first on PATH that records its arguments.

    The last two steps are the controls for both `calls() == []`. A test written
    into the service starts `aws --version` and is run the same way: the record
    holds that one call, so the stand-in is the `aws` a shipped test would start.
    Then `deploy_env.py check` is run in the service: it asks for the two parameters
    the emitted declaration holds and then for the opt-in's two, so the declaration's
    edit is the opt-in's. A stand-in pytest could not see, or an edit that declared
    nothing, would leave the record short of them for any shipped test. `check` never
    reads serverless.yml, so the switch is read back from that file after the edits:
    with it still `none`, the two runs compared below would be two runs over one
    serverless config.
    """
    dest = tmp_path / "svc"
    emitted = _run([str(SCRIPT), "foundry-probe-service", str(dest)])
    assert emitted.returncode == 0, emitted.stderr

    bin_dir = tmp_path / "stand-in"
    bin_dir.mkdir()
    record = tmp_path / "aws-calls.log"
    stand_in = bin_dir / "aws"
    stand_in.write_text(f'#!/bin/sh\necho "$*" >> "{record}"\nexit 97\n')
    stand_in.chmod(0o755)

    def calls() -> list[str]:
        return record.read_text().splitlines() if record.is_file() else []

    as_emitted, run = _passed_count_of_emitted_suite(dest, "tests/shape", first_on_path=bin_dir)
    assert run.returncode == 0, f"the emitted shape tests failed:\n{run.stdout[-3000:]}"
    assert as_emitted > 0, "the emitted service's shape tests ran zero tests"
    assert calls() == [], "a shipped shape test started the aws CLI in a fresh emission"

    for rel, old, new in ESTATE_OPT_IN:
        text = (dest / rel).read_text()
        assert text.count(old) == 1, f"{rel} no longer holds {old!r} once; the opt-in edit moved"
        (dest / rel).write_text(text.replace(old, new))
    serverless = yaml.safe_load((dest / "deploy" / "iac" / "serverless.yml").read_text())
    assert serverless["custom"]["callerVpc"] == "estate", (
        "serverless.yml does not select the estate VPC after the opt-in's edits"
    )

    opted_in, run = _passed_count_of_emitted_suite(dest, "tests/shape", first_on_path=bin_dir)
    assert run.returncode == 0, (
        f"a shipped shape test is red after the estate opt-in:\n{run.stdout[-3000:]}"
    )
    assert opted_in == as_emitted, (
        f"{as_emitted} shape tests passed as emitted and {opted_in} after the opt-in"
    )
    assert calls() == [], "a shipped shape test started the aws CLI in an opted-in service"

    (dest / "tests" / "shape" / "test_aws_is_the_stand_in.py").write_text(
        "import subprocess\n\n\n"
        "def test_aws_is_the_stand_in():\n"
        '    assert subprocess.run(["aws", "--version"], check=False).returncode == 97\n'
    )
    seen, run = _passed_count_of_emitted_suite(
        dest, "tests/shape/test_aws_is_the_stand_in.py", first_on_path=bin_dir
    )
    assert (seen, run.returncode) == (1, 0), run.stdout[-3000:]
    assert calls() == ["--version"], "the stand-in is not the aws a shipped test starts"

    check = subprocess.run(
        [sys.executable, "service-kit/ci/deploy_env.py", "check", "dev"],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={
            **_emitted_service_environment(),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
    )
    assert check.returncode == 2, check.stdout + check.stderr
    assert calls() == ["--version", *EMITTED_READS, *ESTATE_READS], (
        "the edits above are not the estate opt-in"
    )


def test_a_comms_name_is_accepted_and_emits_a_working_service(tmp_path: Path) -> None:
    """The second prefix this template emits under.

    `comms-` is a real product surface (the omnichannel comms blackbox), not a
    Foundry-prefixed service, so it must clear both halves the foundry- name
    already proves: the validator accepts it, and the emitted copy's own suite
    actually collects and passes something.
    """
    dest = tmp_path / "svc"
    emitted = _run([str(SCRIPT), "comms-probe-service", str(dest)])
    assert emitted.returncode == 0, emitted.stderr
    assert (dest / "src" / "comms_probe_service").is_dir(), (
        "comms-probe-service must rename its module to comms_probe_service"
    )

    passed, run = _passed_count_of_emitted_suite(dest)
    assert passed > 0, (
        "the emitted comms- service's suite ran zero tests -- a green that proves nothing"
    )
    assert run.returncode == 0, (
        f"emitted comms- suite failed ({passed} passed):\n{run.stdout[-3000:]}"
    )


def test_a_near_miss_of_the_comms_prefix_is_still_refused(tmp_path: Path) -> None:
    """`comms-` widened the validator; it must not have widened it into a prefix match.

    `commsx-` shares the `comms` letters without the separating hyphen, and
    `comms_foo` uses the wrong separator entirely -- both must still be refused,
    or the regex is matching a substring rather than the declared prefix.
    """
    for near_miss in ("commsx-foo", "comms_foo"):
        refused = _run([str(SCRIPT), near_miss, str(tmp_path / f"refused-{near_miss}")])
        assert refused.returncode != 0, f"{near_miss!r} must still be refused"
        assert "comms-<kebab-case>" in refused.stderr


#: The exact five artifact classes an earlier change names: SqsPublisher and its
#: wiring, the sqs event_bus_mode plus sqs_queue_url config, SQS_QUEUE_URL/ARN in
#: serverless.yml and ci.yml, sqs:SendMessage IAM in serverless.yml and app/main.tf,
#: and the outbox relay's aws_sqs_queue in deploy/iac/resources. Structured checks --
#: `ast` for Python, `yaml.safe_load` for YAML, a resource/variable/output regex for
#: HCL (written before `python-hcl2` joined test.lock) -- never a bare text `grep` for "sqs",
#: which would also flag the illustrative, unwired `handle_sqs_record` consumer
#: (`transport/events/widget_events_consumer.py`) and every historical docstring.
def _sqs_publish_path_findings(dest: Path, *, module: str) -> list[str]:
    findings: list[str] = []

    sqs_module = dest / "src" / module / "adapters" / "real" / "sqs_event_bus.py"
    if sqs_module.is_file():
        findings.append(f"{sqs_module.relative_to(dest)} still exists")

    guard_test = dest / "tests" / "unit" / "test_sqs_event_bus_import_guard.py"
    if guard_test.is_file():
        findings.append(f"{guard_test.relative_to(dest)} still exists")

    real_init = dest / "src" / module / "adapters" / "real" / "__init__.py"
    if "SqsPublisher" in ast.dump(ast.parse(real_init.read_text())):
        findings.append(f"{real_init.relative_to(dest)} still names SqsPublisher")

    config_py = dest / "src" / module / "composition" / "config.py"
    config_tree = ast.parse(config_py.read_text())
    for node in ast.walk(config_tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "EventBusMode" for t in node.targets)
            and isinstance(node.value, ast.Subscript)
        ):
            literal_values = {
                element.value
                for element in ast.walk(node.value)
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            if "sqs" in literal_values:
                findings.append("config.py EventBusMode Literal still lists 'sqs'")
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "sqs_queue_url":
                findings.append("config.py ServiceConfig still declares sqs_queue_url")

    wiring_py = dest / "src" / module / "composition" / "wiring.py"
    if "SqsPublisher" in ast.dump(ast.parse(wiring_py.read_text())):
        findings.append("wiring.py still references SqsPublisher")

    serverless = yaml.safe_load((dest / "deploy" / "iac" / "serverless.yml").read_text())
    provider = serverless.get("provider", {}) if isinstance(serverless, dict) else {}
    environment = provider.get("environment", {}) if isinstance(provider, dict) else {}
    if isinstance(environment, dict) and "SQS_QUEUE_URL" in environment:
        findings.append("serverless.yml provider.environment still sets SQS_QUEUE_URL")
    if isinstance(provider, dict) and "iam" in provider:
        findings.append("serverless.yml still declares provider.iam (sqs:SendMessage)")

    def _walk_strings(node: object) -> set[str]:
        found: set[str] = set()
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(str(key))
                found |= _walk_strings(value)
        elif isinstance(node, list):
            for item in node:
                found |= _walk_strings(item)
        elif isinstance(node, str):
            found.add(node)
        return found

    ci_yaml = yaml.safe_load((dest / ".github" / "workflows" / "ci.yml").read_text())
    ci_strings = _walk_strings(ci_yaml)
    if "SQS_QUEUE_URL" in ci_strings or "SQS_QUEUE_ARN" in ci_strings:
        findings.append("ci.yml still sets SQS_QUEUE_URL/SQS_QUEUE_ARN")

    main_tf = (dest / "deploy" / "iac" / "app" / "main.tf").read_text()
    if re.search(r'resource\s+"aws_iam_role_policy"\s+"task_sqs"', main_tf):
        findings.append("deploy/iac/app/main.tf still declares aws_iam_role_policy.task_sqs")
    if "sqs:SendMessage" in main_tf:
        findings.append("deploy/iac/app/main.tf still grants sqs:SendMessage")

    app_variables_tf = (dest / "deploy" / "iac" / "app" / "variables.tf").read_text()
    if re.search(r'variable\s+"sqs_queue_arn"', app_variables_tf):
        findings.append("deploy/iac/app/variables.tf still declares variable.sqs_queue_arn")

    resources_tf = (dest / "deploy" / "iac" / "resources" / "main.tf").read_text()
    if re.search(r'resource\s+"aws_sqs_queue"', resources_tf):
        findings.append("deploy/iac/resources/main.tf still declares an aws_sqs_queue resource")

    outputs_tf = (dest / "deploy" / "iac" / "resources" / "outputs.tf").read_text()
    if re.search(r'output\s+"sqs_queue_(arn|url)"', outputs_tf):
        findings.append("deploy/iac/resources/outputs.tf still declares an sqs_queue output")

    return findings


#: Emission-time tools that run from the TEMPLATE directory and must not ship in an
#: emitted service of either flavour.
EMISSION_TIME_TOOLS = ("scripts/apply_comms_flavour.py",)


def _shipped_emission_tools(dest: Path) -> list[str]:
    return [rel for rel in EMISSION_TIME_TOOLS if (dest / rel).exists()]


def _emitted_kit_drift(dest: Path) -> subprocess.CompletedProcess[str]:
    gate = dest / "service-kit" / "gates" / "check_kit_drift.py"
    return _run([sys.executable, str(gate)], cwd=dest)


def test_a_comms_emission_has_no_sqs_point_to_point_publish_path(tmp_path: Path) -> None:
    """comms-02 rule 1: a producer holds only events:PutEvents on its own bus --
    no queue URL and no consumer endpoint in code, config or IaC.

    Structured, not textual: `_sqs_publish_path_findings` parses Python via `ast`
    and YAML via `yaml.safe_load`, so it cannot be tripped by the illustrative
    `handle_sqs_record` consumer or a historical docstring mentioning "sqs".
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "comms-probe-service", str(dest)]).returncode == 0

    findings = _sqs_publish_path_findings(dest, module="comms_probe_service")
    assert findings == [], f"comms emission retained SQS point-to-point artifact(s): {findings}"
    assert _shipped_emission_tools(dest) == [], (
        "an emission-time tool shipped inside the comms- service"
    )
    # Regression: the stripped ci.yml matches the manifest's comms digest.
    drift = _emitted_kit_drift(dest)
    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "(comms flavour)" in drift.stdout, drift.stdout

    passed, run = _passed_count_of_emitted_suite(dest)
    assert passed > 0, "the emitted comms- service's suite ran zero tests"
    assert run.returncode == 0, (
        f"emitted comms- suite failed ({passed} passed):\n{run.stdout[-3000:]}"
    )


def test_a_foundry_emission_still_has_the_sqs_publish_path(tmp_path: Path) -> None:
    """Positive control for the finder above: the flavour is comms-only.

    Without this, a finder that always returns `[]` -- broken, not clean -- would
    make the test above pass for the wrong reason.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    findings = _sqs_publish_path_findings(dest, module="foundry_probe_service")
    assert findings, "the finder must still see the SQS publish path on an unflavoured emission"
    # Not a control for the finder: the emission-time tool must be absent here too,
    # because TEMPLATE_ONLY applies to every flavour.
    assert _shipped_emission_tools(dest) == [], (
        "an emission-time tool shipped inside the foundry- service"
    )
    drift = _emitted_kit_drift(dest)
    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "(default flavour)" in drift.stdout, drift.stdout


def test_disabling_the_comms_flavour_strip_leaves_sqs_in_the_emission(tmp_path: Path) -> None:
    """Mutation proof: baseline (clean) / mutation (strip disabled, red) / restore (clean).

    Neutralising `apply_comms_flavour.py` into a no-op must turn a comms emission
    back into one the finder above flags, and restoring the script must clean it
    again -- proving the finder is actually exercising the strip script rather
    than agreeing with it by construction.
    """
    baseline_dest = tmp_path / "svc-baseline"
    assert _run([str(SCRIPT), "comms-probe-service", str(baseline_dest)]).returncode == 0
    assert _sqs_publish_path_findings(baseline_dest, module="comms_probe_service") == []

    flavour_script = TEMPLATE_DIR / "scripts" / "apply_comms_flavour.py"
    original = flavour_script.read_text()
    try:
        flavour_script.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
        mutated_dest = tmp_path / "svc-mutated"
        assert _run([str(SCRIPT), "comms-probe-service", str(mutated_dest)]).returncode == 0
        mutated_findings = _sqs_publish_path_findings(mutated_dest, module="comms_probe_service")
        assert mutated_findings, (
            "disabling the comms flavour strip must leave SQS artifact(s) in the emission"
        )
    finally:
        flavour_script.write_text(original)

    restored_dest = tmp_path / "svc-restored"
    assert _run([str(SCRIPT), "comms-probe-service", str(restored_dest)]).returncode == 0
    restored_findings = _sqs_publish_path_findings(restored_dest, module="comms_probe_service")
    assert restored_findings == [], f"restore arm still finds: {restored_findings}"


def test_the_emitted_service_persists_a_widget_across_real_http_app_instances(
    tmp_path: Path,
) -> None:
    """Exercise the generated artifact, not the template import already in pytest.

    Two independently constructed ASGI applications share only a SQLite file.
    The first creates through HTTP and closes its unit of work; the second must
    read the committed row through HTTP. This catches an emitter that boots but
    never completes its persistence transaction.

    THE SCHEMA COMES FROM THE EMITTED SERVICE'S OWN `alembic upgrade head`, and
    that half is new. The template's unit of work used to run
    `CREATE TABLE IF NOT EXISTS` on connect, so an emitted service arrived with a
    schema whatever else was true of it. Migrations own schema now, which means
    this probe fails unless `new_service.sh` carried `migrations/` and
    `alembic.ini` across AND the rename pass reached `migrations/env.py` -- where
    a missed rename is an ImportError for a package that no longer exists. Both
    are properties of the emitter, and neither had an arm before.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0
    db_path = tmp_path / "widgets.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    probe = r"""
from fastapi.testclient import TestClient
from foundry_probe_service.composition.app_factory import create_app
from foundry_probe_service.composition.config import ServiceConfig
from foundry_probe_service.composition.container import build_container

headers = {
    "x-org-id": "org_emit",
    "x-subject-id": "user_emit",
    "x-scopes": "widgets:read,widgets:write",
}
def config():
    return ServiceConfig(
        service_env="test", port=8080, principal_issuer="emit-test",
        database_url=DATABASE_URL, event_bus_mode="in_process", sqs_queue_url=None,
        aws_region=None,
    )

first = build_container(config())
with TestClient(create_app(first)) as client:
    created = client.post("/widgets", json={"name": "Persisted"}, headers=headers)
    assert created.status_code == 201, created.text
if first.unit_of_work is not None:
    first.unit_of_work.close()

second = build_container(config())
with TestClient(create_app(second)) as client:
    listed = client.get("/widgets", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [item["name"] for item in listed.json()["widgets"]] == ["Persisted"]
"""
    env = _emitted_service_environment()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(dest / "src"),
            str(dest / "service-kit" / "vendor"),
            str(dest / "service-kit" / "testing"),
            str(dest),
        ]
    )
    probe = f"DATABASE_URL = {database_url!r}\n" + probe

    # The emitted service migrates ITSELF, through its own copied alembic tree.
    # A `create_all` here would prove the models import and would say nothing
    # about whether the revision files, `alembic.ini` and the renamed `env.py`
    # survived the copy -- which is the half that can break.
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**env, "DATABASE_URL": database_url},
    )
    assert migrated.returncode == 0, (
        f"the emitted service could not migrate itself:\n{migrated.stdout}\n{migrated.stderr}"
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, f"generated HTTP/persistence probe failed:\n{result.stderr}"


def test_the_emitted_dockerfile_has_no_monorepo_paths(tmp_path: Path) -> None:
    """Regression: an emitted repo could be renamed perfectly and still not build.

    The template's Dockerfile COPYs `packages/...`, which only resolves with the
    monorepo as build context. A leftover here is the failure mode where every
    other check passes and `docker build` is the first thing to notice.

    The Dockerfile moved to `service-kit/docker/` with the template-owned kit
    (contract §5); this test follows it rather than looking anywhere a Dockerfile
    might be, because "a Dockerfile exists somewhere" would pass with a stray
    root copy left behind -- the drift `test_single_dockerfile.py` exists to rule out.

    Since 57e9b99 the Dockerfile is GENERIC and byte-identical in every service:
    it COPYs `src/${SERVICE_PACKAGE}` and refuses to build without that arg. So
    "the image carries THIS service's source" is no longer a line in the
    Dockerfile; it is the arg `deploy/compose.yml` declares and every build site
    reads, and those are asserted instead. This assertion read
    `"src/foundry_probe_service" in dockerfile` and went red for the
    retirement, while both CI builds were missing the arg and could not have
    built anything -- which is what the build-site arm below
    is for.

    Since 56592cf the vendor COPY is likewise generic (`COPY service-kit/vendor/ ./`
    replaces one `COPY` per package name), so "foundry_contracts reaches the
    image" is asserted against the emitted tree's real `service-kit/vendor/`
    directory below, not against Dockerfile text that no longer names it.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    dockerfile = (dest / "service-kit" / "docker" / "Dockerfile").read_text()
    offending = [ln for ln in dockerfile.splitlines() if "packages/" in ln]
    assert offending == [], f"Dockerfile still references the monorepo layout: {offending}"

    for expected in (
        "requirements/service.lock",
        "COPY service-kit/vendor/ ./",
        "COPY src/${SERVICE_PACKAGE} ${SERVICE_PACKAGE}",
        "service-kit/docker/entrypoint.sh",
        "service-kit/docker/healthcheck.sh",
        "service-kit/docker/wait-for.sh",
    ):
        assert expected in dockerfile, f"Dockerfile lost its COPY of {expected}"
    assert (dest / "src" / "foundry_probe_service").is_dir()

    # `foundry_contracts` no longer has its OWN COPY line (56592cf: one
    # `COPY service-kit/vendor/ ./` replaces a COPY-per-package-name list, so a
    # service can vendor an additional domain-specific package -- comms-core's
    # factbus/comms_contracts, for instance -- without forking this
    # kit-pinned file). What the whole-tree COPY above actually guarantees is
    # that whatever is ON DISK under `service-kit/vendor/` reaches the image,
    # so that is what this asserts: the real artifact, not a line of text that
    # no longer has any reason to appear.
    assert (dest / "service-kit" / "vendor" / "foundry_contracts").is_dir()

    # deploy/compose.yml is the ONE authority for the generic Dockerfile's per-service
    # args, and it states them for THIS service.
    compose = yaml.safe_load((dest / "deploy" / "compose.yml").read_text())
    args = compose["services"]["service"]["build"]["args"]
    assert args["SERVICE_PACKAGE"] == "foundry_probe_service", (
        f"deploy/compose.yml builds SERVICE_PACKAGE={args['SERVICE_PACKAGE']!r}"
    )
    # HAS_MIGRATIONS must describe the tree: `yes` exactly when an Alembic tree ships.
    owns_schema = (dest / "migrations").is_dir() and (dest / "alembic.ini").is_file()
    assert args["HAS_MIGRATIONS"] == ("yes" if owns_schema else "no"), (
        f"deploy/compose.yml says HAS_MIGRATIONS={args['HAS_MIGRATIONS']!r} "
        f"for a tree whose Alembic tree present={owns_schema}"
    )

    # Regression: every CI build reads that authority instead of restating it. A bare
    # `docker build` had to repeat the args, and repeated SERVICE_PACKAGE but not
    # HAS_MIGRATIONS, so the pushed image of a schema-owning service lacked migrations/.
    workflow = (dest / ".github" / "workflows" / "ci.yml").read_text()
    runs = [ln.strip() for ln in workflow.splitlines() if not ln.strip().startswith("#")]
    bare = [ln for ln in runs if "docker build" in ln or "--build-arg" in ln]
    assert bare == [], f"ci.yml restates build args instead of building via compose: {bare}"
    builds = [ln for ln in runs if "docker compose -f deploy/compose.yml build service" in ln]
    # Hand-written floor: the workflow has a build job and a deploy push today.
    assert len(builds) >= 2, f"found {len(builds)} compose build line(s) in ci.yml: {builds}"


def test_the_emitted_repo_vendors_its_contracts_dependency(tmp_path: Path) -> None:
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    vendored = dest / "service-kit" / "vendor" / "foundry_contracts"
    assert vendored.is_dir(), "foundry_contracts was not vendored"
    assert (vendored / "principal.py").is_file(), "vendored contracts look incomplete"
    caches = list(vendored.rglob("__pycache__"))
    assert caches == [], f"bytecode was vendored along with the source: {caches}"


def test_the_emitted_repo_carries_a_ci_workflow_and_an_iac_gate(tmp_path: Path) -> None:
    """Regression: until this test existed, a new service had no CI and no IaC
    gate until someone hand-wrote both -- which is what happened by hand in
    llm-gateway. Enumerate the emitted TREE, not a hardcoded list
    with a silent `continue`, so a future rename of either file fails this
    assertion instead of passing it by accident.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    emitted = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    for expected in (".github/workflows/ci.yml", "scripts/check_service_iac.sh"):
        assert expected in emitted, f"new_service.sh did not emit {expected}"
    # The gate travels and the tests of its print handling do not. That file edits
    # `deploy/`, which the service owns, by exact text, so in a service that changed
    # its config it went red on a correct one.
    assert "tests/shape/test_iac_gate_print_streams.py" not in emitted, (
        "new_service.sh emitted the template-only tests of the gate's print handling"
    )
    # Nor do the arms over the template's own deploy/ files. The tests of the kit's
    # deploy_env.py do: each writes its own declaration.
    assert "tests/shape/test_template_deploy_declaration.py" not in emitted, (
        "new_service.sh emitted the template-only arms over the template's own deploy/ files"
    )
    assert "tests/shape/test_deploy_env_script.py" in emitted, (
        "new_service.sh no longer emits the tests of the kit's deploy_env.py"
    )

    gate_path = dest / "scripts" / "check_service_iac.sh"
    assert os.access(gate_path, os.X_OK), f"{gate_path} was emitted without the execute bit"

    workflow = (dest / ".github" / "workflows" / "ci.yml").read_text()
    gate = gate_path.read_text()
    assert "name: foundry-probe-service" in workflow, "the workflow's own name was not renamed"
    assert 'SERVICE="foundry-probe-service"' in gate, "the gate's SERVICE var was not renamed"
    for leftover in ("foundry-service-template", "foundry_service_template"):
        assert leftover not in workflow, f"template name survived in the workflow: {leftover}"
        assert leftover not in gate, f"template name survived in the gate: {leftover}"

    # The deploy job's smoke and rollback call kit scripts; a workflow emitted without
    # them would fail at the one step nobody runs until a release is already live.
    for script in ("service-kit/ci/smoke.py", "service-kit/ci/lambda_release.sh"):
        assert script in emitted, f"new_service.sh did not emit {script}"
        assert script in workflow, f"the emitted workflow no longer calls {script}"
    assert "deploy/smoke.json" in emitted, "the smoke has no route declaration to read"
    assert os.access(dest / "service-kit/ci/lambda_release.sh", os.X_OK)
    assert "foundry-probe-service-${{ inputs.target_environment }}-api" in workflow, (
        "the rollback target names a function the emitted service does not deploy"
    )


TEMPLATE_ONLY_MENTION = re.compile(r"(?<![A-Za-z0-9_])TEMPLATE_ONLY(?![A-Za-z0-9_])")
#: The one assignment shape, as the value reader below reads it: the rest of the line after
#: the name, with nothing after the closing quote.
TEMPLATE_ONLY_ASSIGNED_REST = re.compile(r'="[^"\n]*"')


def _template_only_mentions_outside_the_two_shapes(source: str) -> list[str]:
    """Each line that names TEMPLATE_ONLY other than as a whole `TEMPLATE_ONLY="..."` line,
    `$TEMPLATE_ONLY` or `${TEMPLATE_ONLY}`. The assignment is accepted only as the value
    reader reads it, so a second assignment with a comment, a command or a space after its
    closing quote is a mention here rather than a write nobody counts (review of #214,
    round 3, minor 1)."""
    offenders = []
    for match in TEMPLATE_ONLY_MENTION.finditer(source):
        line_start = source.rfind("\n", 0, match.start()) + 1
        line_end = source.find("\n", match.start())
        before, after = source[line_start : match.start()], source[match.end() :]
        rest = source[match.end() : line_end if line_end >= 0 else len(source)]
        if (
            (before == "" and TEMPLATE_ONLY_ASSIGNED_REST.fullmatch(rest))
            or before.endswith("$")
            or (before.endswith("${") and after.startswith("}"))
        ):
            continue
        offenders.append(source[line_start : line_end if line_end >= 0 else len(source)].strip())
    return offenders


#: Lines added after the assignment, and whether the test must refuse them. Every write
#: form a review has used is refused; so is quoted data, which this test does not try to
#: tell from a write; the two read shapes stay quiet.
TEMPLATE_ONLY_EDITS = {
    "array element, arithmetic index": ('TEMPLATE_ONLY[$((0))]="changed"', True),
    "array element, spaced index": ('TEMPLATE_ONLY[0 + 0]="changed"', True),
    "append": ('TEMPLATE_ONLY+=" more"', True),
    "declare": ('declare TEMPLATE_ONLY="changed"', True),
    "nameref": ("declare -n alias=TEMPLATE_ONLY", True),
    "printf -v": ("printf -v TEMPLATE_ONLY '%s' changed", True),
    "read": ("read -r TEMPLATE_ONLY <<< changed", True),
    "default expansion": (': "${TEMPLATE_ONLY:=changed}"', True),
    "quoted data": ("printf '%s\\n' 'TEMPLATE_ONLY=display-only'", True),
    "a label": ("qa_label=TEMPLATE_ONLY", True),
    "second assignment, comment after": ('TEMPLATE_ONLY="changed" # another authority', True),
    "second assignment, command after": ('TEMPLATE_ONLY="changed"; :', True),
    "second assignment, spaces after": ('TEMPLATE_ONLY="changed"  ', True),
    "a $ read": ('echo "$TEMPLATE_ONLY"', False),
    "a ${} read": ('echo "${TEMPLATE_ONLY}"', False),
}


@pytest.mark.parametrize("edit", sorted(TEMPLATE_ONLY_EDITS))
def test_template_only_may_be_named_only_by_its_assignment_and_its_reads(edit: str) -> None:
    line, refused = TEMPLATE_ONLY_EDITS[edit]
    source = SCRIPT.read_text()
    assigned = re.search(r'^TEMPLATE_ONLY="[^"\n]*"\n', source, flags=re.M)
    assert assigned, "new_service.sh has no TEMPLATE_ONLY assignment to edit after"
    edited = source[: assigned.end()] + line + "\n" + source[assigned.end() :]
    found = _template_only_mentions_outside_the_two_shapes(edited)
    assert found == ([line.strip()] if refused else []), found
    # CONTROL: the script as committed names the variable only in the two shapes.
    assert _template_only_mentions_outside_the_two_shapes(source) == []


def test_every_template_only_entry_names_a_tracked_file() -> None:
    """`new_service.sh` matches `TEMPLATE_ONLY` against `git ls-files` by exact path, so
    an entry that names no tracked file withholds nothing and says nothing. Renamed with
    `git mv`, the tests of the gate's print handling were shipped again and every test
    here passed."""
    source = SCRIPT.read_text()
    # The value reader below reads ONE assignment, so a second authority must not exist.
    # Rather than recognise every way Bash can write a variable (a nameref, an array
    # element with an arithmetic index, `printf -v`, `read`, `declare`...), the script may
    # name TEMPLATE_ONLY in exactly two shapes: the one assignment at the start of a line,
    # and a `$` read. Any other mention, quoted or not, is refused: this test does not
    # guess whether it writes.
    offenders = _template_only_mentions_outside_the_two_shapes(source)
    assert offenders == [], (
        "new_service.sh names TEMPLATE_ONLY outside its one assignment and its `$` reads, "
        f"which this test does not read: {offenders}"
    )
    assigned = re.findall(r'^TEMPLATE_ONLY="([^"\n]*)"$', source, flags=re.M)
    assert len(assigned) == 1, f"new_service.sh assigns TEMPLATE_ONLY {len(assigned)} time(s)"
    entries = assigned[0].split()
    # The script is the first entry and the arms over the template's own deploy/ files
    # are the last: the line was read to its end. A parse that read nothing would pass
    # the comparison below with nothing in it.
    for known in ("scripts/new_service.sh", "tests/shape/test_template_deploy_declaration.py"):
        assert known in entries, f"TEMPLATE_ONLY was not read whole: {known} is not in {entries}"

    listed = _run(["git", "ls-files", "-z", "."], cwd=TEMPLATE_DIR)
    assert listed.returncode == 0, listed.stderr
    tracked = set(listed.stdout.split("\0"))
    assert "scripts/check_service_iac.sh" in tracked, "git ls-files did not list the gate"

    stale = [entry for entry in entries if entry not in tracked]
    assert stale == [], f"TEMPLATE_ONLY names files git does not track: {stale}"


def test_the_emitted_service_carries_a_current_openapi_contract(tmp_path: Path) -> None:
    """Regression: a new service commits its spec, and the spec is its own.

    The committed spec names the service in `info.title`, so the copy is only
    current if the rename pass reached it. `--check` is run in the EMITTED tree,
    against the emitted package, because that is the comparison its CI makes.
    """
    dest = tmp_path / "svc"
    emitted = _run([str(SCRIPT), "foundry-probe-service", str(dest)])
    assert emitted.returncode == 0, emitted.stderr
    assert "emitted the OpenAPI contract" in emitted.stdout

    workflow = (dest / ".github" / "workflows" / "ci.yml").read_text()
    assert "service-kit/ci/openapi_breaking.sh compare" in workflow
    assert os.access(dest / "service-kit/ci/openapi_breaking.sh", os.X_OK)

    env = _emitted_service_environment()
    env["PYTHONPATH"] = os.pathsep.join([str(dest / "src"), str(dest / "service-kit" / "vendor")])
    check = subprocess.run(
        [sys.executable, str(dest / "scripts" / "export_openapi.py"), "--check"],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert check.returncode == 0, f"{check.stderr}\n{check.stdout[:3000]}"
    assert str(dest / "contracts" / "openapi" / "v1.json") in check.stdout


def test_the_emitted_iac_gate_actually_passes(tmp_path: Path) -> None:
    """A citation is not a proof: assert the emitted gate RUNS green, not merely
    that the file exists. Requires terraform/tofu and npx on PATH, exactly like
    `bash scripts/check_template_iac.sh` already does for this template's own
    IaC in the monorepo's verification list.

    `new_service.sh` deliberately does not touch git (its own docstring says
    so), but one of the gate's arms -- ".terraform.lock.hcl was not
    rewritten" -- shells out to `git diff --quiet`, which is meaningless
    against a bare directory. The gate's REAL invocation is always inside an
    `actions/checkout@v4` clone, so this test commits the emit first rather
    than either skipping the arm or reading a git-less false red as a defect
    in the gate.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    for git_args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "emit"]):
        commit = subprocess.run(
            ["git", *git_args],
            cwd=dest,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            env=git_env,
        )
        assert commit.returncode == 0, f"git {git_args} failed: {commit.stderr}"

    result = subprocess.run(
        ["bash", "scripts/check_service_iac.sh"],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"emitted check_service_iac.sh did not pass:\n{result.stdout}\n{result.stderr}"
    )
    # HAND-WRITTEN LITERAL. It moved 7 -> 8 when the inert-command arm was added, and
    # 8 -> 13 at the resource/application IaC split: the four terraform arms (init,
    # validate, fmt -check, lock untouched) now run once per ROOT rather than once, so
    # `deploy/iac/resources/` adds four, and the split also added the arm asserting no durable
    # resource or remote-state read survives in the release root.
    # 13 -> 14 with an earlier change's "no non-HTTP function competes with the
    # adapter's runtime client" arm (R.1 named it for the awslambdaric shape it
    # required then; R.2 redesigned the arm's content in place -- the count did
    # not move again).
    # 14 -> 19 with an earlier change's five arms for a service that calls another
    # private service: one terraform test over the task's security groups and four
    # over the resolved serverless config -- the committed `custom.callerVpc` is one of
    # its two values, and each value, forced in a copy, resolves to what it means.
    # 19 -> 20 with that slice's second review: `estate` committed without its two
    # variables declared in deploy/deploy_env.json is refused here, before the build.
    # It must stay a literal: deriving it from the gate's own output would make this
    # assertion true for any arm count, including an emitted gate that silently lost
    # an arm. The emitted service declares ONE function and no image command, so the
    # inert-command arm reports "0 with an image command, 0 inert" and holds its 6
    # controls, and the adapter-dispatch arm reports "0 non-HTTP, 0 adapter-competing,
    # 0 missing pass-through env" and holds its 9 controls.
    assert "PASS: all 20 arm(s) executed" in result.stdout, result.stdout


def test_the_emitted_repo_carries_a_gitignore_covering_what_running_it_produces(
    tmp_path: Path,
) -> None:
    """the emission is real but was INCIDENTAL until this test.

    `new_service.sh` copies `git ls-files`, so the template's `.gitignore` rides
    along because it happens to be tracked -- nothing asserted it, and nothing
    would have gone red if it were untracked, deleted, or added to TEMPLATE_ONLY.
    That is exactly how the three wave-1 services were emitted WITHOUT one and had
    to have it added by hand on 2026-09-05, each starting dirty the first time
    anyone ran its tests. In an estate whose contract proof refuses a dirty tree, a
    service that dirties itself by being tested cannot prove itself.

    The classes below are the ones a run of the emitted repo actually produces, so
    the test is about the OUTPUT of using the service, not about tidiness:
    `__pycache__/` from pytest, `.venv/` from the quickstart the script itself
    prints, `graphify-out/` from `graphify-repo-init.sh`, `*.db` from the SQLite
    backend. Asserting the rule text rather than a file listing is deliberate --
    an emitted repo has never been run, so the artifacts do not exist yet and a
    `rglob` check would pass vacuously against an empty tree.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    ignore_file = dest / ".gitignore"
    assert ignore_file.is_file(), (
        "new_service.sh emitted no .gitignore; a fresh service goes dirty on its first "
        "pytest run, and the contract proof refuses a dirty tree"
    )

    rules = {
        line.strip()
        for line in ignore_file.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for required in ("__pycache__/", ".venv/", "graphify-out/", "*.db", ".pytest_cache/"):
        assert required in rules, (
            f".gitignore does not cover {required!r}; running the emitted service "
            f"produces it. Present rules: {sorted(rules)}"
        )


# ---------------------------------------------------------------------------
# The v2 module contract: an emitted service must inherit the whole shape
# ---------------------------------------------------------------------------

#: HAND-WRITTEN, and the reason it is a literal rather than a glob over
#: `scripts/check_*.py`: a count derived from the emitted tree is satisfied by
#: whatever that tree happens to hold, including zero gates. This list IS §9.
CONTRACT_GATES = (
    "check_layering.py",
    "check_mvc_separation.py",
    "check_size_limits.py",
    "check_mock_budget.py",
    "check_docker_shared.py",
    "check_lifecycle.py",
    # S18. Added by an earlier change. The emitted service carries the gate
    # AND `scripts/pydantic_dict_ratchet.json`; without the second it refuses
    # (exit 2) rather than passing, which is the point of a ratchet with no
    # recorded value.
    "check_pydantic_boundaries.py",
)

#: §1's physical submodules. Same reasoning: the literal is the contract.
CONTRACT_SUBMODULES = (
    "domain",
    "ports",
    "application",
    "adapters",
    "transport",
    "composition",
    "runtime",
)

#: §5's shared containerisation folder.
CONTRACT_DOCKER_ARTEFACTS = (
    "service-kit/docker/Dockerfile",
    "service-kit/docker/entrypoint.sh",
    "service-kit/docker/healthcheck.sh",
    "service-kit/docker/wait-for.sh",
    "service-kit/docker/README.md",
    "service-kit/docker/compose/localstack.yml",
    "service-kit/docker/compose/postgres.yml",
    "service-kit/docker/compose/service.yml",
)


def test_the_emitted_service_carries_the_seven_physical_submodules(tmp_path: Path) -> None:
    """§1 is PHYSICAL: a boundary is a directory a reader can see.

    An emitted service that lost a submodule would still import, still pass its
    own tests, and would have quietly reverted to "layers by convention".
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    package = dest / "src" / "foundry_probe_service"
    missing = [
        name
        for name in CONTRACT_SUBMODULES
        if not (package / name / "__init__.py").is_file()
    ]
    assert missing == [], f"the emitted service has no {missing} submodule(s) with a contract"


def test_the_emitted_service_carries_the_shared_docker_folder(tmp_path: Path) -> None:
    """§5: the folder is IDENTICAL across services, so it travels whole."""
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    missing = [name for name in CONTRACT_DOCKER_ARTEFACTS if not (dest / name).is_file()]
    assert missing == [], f"the emitted service is missing docker artefact(s): {missing}"
    for script in ("entrypoint.sh", "healthcheck.sh", "wait-for.sh"):
        path = dest / "service-kit" / "docker" / script
        assert os.access(path, os.X_OK), (
            f"service-kit/docker/{script} was emitted without the execute bit"
        )


def test_the_emitted_service_satisfies_ALL_SIX_contract_gates(tmp_path: Path) -> None:
    """The assertion this whole item exists for: the gates RUN, and they PASS.

    Not "the files were copied" -- a citation is not a proof. Each gate is run
    as a subprocess against the emitted tree and its RAW exit code is read.

    Exit 3 is a NAMED SKIP and is accepted as such, never as a pass: the
    assertion below records which gates skipped, so a run in which every gate
    skipped cannot be read as a run in which every gate passed. Exit 2 is a
    REFUSAL -- the gate could not make a trustworthy measurement -- and fails
    this test exactly as a finding does.
    """
    dest = tmp_path / "svc"
    emitted = _run([str(SCRIPT), "foundry-probe-service", str(dest)])
    assert emitted.returncode == 0, emitted.stderr

    present = [g for g in CONTRACT_GATES if (dest / "service-kit" / "gates" / g).is_file()]
    assert present == list(CONTRACT_GATES), (
        f"the emitted service is missing gate(s): "
        f"{sorted(set(CONTRACT_GATES) - set(present))}"
    )

    env = _emitted_service_environment()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(dest / "src"),
            str(dest / "service-kit" / "vendor"),
            str(dest / "service-kit" / "testing"),
            str(dest),
        ]
    )
    outcomes: dict[str, int] = {}
    for gate in CONTRACT_GATES:
        result = subprocess.run(
            [sys.executable, str(dest / "service-kit" / "gates" / gate)],
            cwd=dest,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
            env=env,
        )
        outcomes[gate] = result.returncode
        assert result.returncode in (0, 3), (
            f"{gate} on the emitted service exited {result.returncode} "
            f"(1=finding, 2=refusal):\n{result.stdout}\n{result.stderr}"
        )

    skipped = sorted(g for g, code in outcomes.items() if code == 3)
    passed = sorted(g for g, code in outcomes.items() if code == 0)
    # A run where everything skipped is NOT a run where everything passed. The
    # hand-written floor makes that distinction fail loudly instead of reading
    # as six green checks.
    assert len(passed) >= 5, (
        f"only {len(passed)} of {len(CONTRACT_GATES)} gates actually ran green; "
        f"skipped (exit 3, NOT a pass): {skipped}"
    )


def test_the_emitted_gates_can_still_go_RED(tmp_path: Path) -> None:
    """A gate that has never been shown to fire cannot certify a clean tree.

    The emitted gates pass above. This plants a real §1 violation in the emitted
    service -- an `application` module importing `adapters` -- and requires
    `check_layering.py` to find it. Without this arm, "all six gates pass" is
    equally consistent with six gates that cannot see anything.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    offender = dest / "src" / "foundry_probe_service" / "application" / "_planted.py"
    offender.write_text(
        "from foundry_probe_service.adapters.fake import InMemoryWidgetRepository\n\n"
        "STORE = InMemoryWidgetRepository\n"
    )

    env = _emitted_service_environment()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(dest / "src"),
            str(dest / "service-kit" / "vendor"),
            str(dest / "service-kit" / "testing"),
            str(dest),
        ]
    )
    result = subprocess.run(
        [sys.executable, str(dest / "service-kit" / "gates" / "check_layering.py")],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=env,
    )

    # 1 = finding. Not 0, and not 2: a refusal here would mean the gate broke
    # rather than that it saw the violation, and the two must not be conflated.
    assert result.returncode == 1, (
        f"check_layering.py returned {result.returncode} on a planted "
        f"application->adapters import:\n{result.stdout}\n{result.stderr}"
    )
    assert "application imports adapters" in result.stderr, result.stderr


def test_the_emitted_service_passes_its_own_test_first_gate(tmp_path: Path) -> None:
    """The gate is emitted AND green on what was emitted.

    Emitting a gate that fails on the freshly generated service would be worse than not
    emitting it: an author's first act on a new repository would be to silence a red gate
    they did not write, and the fastest way to silence this one is to add an exemption --
    which is exactly how an escape hatch becomes the normal path.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    result = subprocess.run(
        [sys.executable, "service-kit/gates/check_test_first.py"],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**os.environ, "PYTHONPATH": "src:vendor"},
    )

    assert result.returncode == 0, (
        f"emitted check_test_first.py did not pass:\n{result.stdout}\n{result.stderr}"
    )
    # HAND-WRITTEN, like the arm count above. Deriving "0 findings" from the gate's own
    # output would hold for a gate that had been blinded to every module.
    assert "0 finding(s)" in result.stdout, result.stdout
    assert "0 exempt" in result.stdout, (
        "the emitted service starts with exemptions, so the escape is the default path"
    )


def test_the_emitted_service_can_prove_its_own_seeded_arms_and_says_what_it_skipped(
    tmp_path: Path,
) -> None:
    """The prover travels, runs in the emitted tree, and accounts for every arm.

    The presence half is already covered by `SEEDED_TIER` above, and presence is
    the weaker claim: `prove_seeded_tests.py` names source files by path and
    anchors on exact lines of them, so a copy that arrived intact into a service
    whose package was renamed would still report every arm UNMEASURED. Running it
    is what separates "the file is there" from "the file works here".

    It also asserts the ACCOUNTING, not only the exit code. A one-profile run
    proves the arms that apply under that profile and declines the rest, and a
    table that prints a total without saying which arms it declined invites a
    reader to take the total for the whole population. So this requires the union
    line and the named list of arms this run did not take -- the two sentences
    that make a single-profile green honest.

    The `fake` profile is deliberate: it needs no database, so this arm measures
    the emitted tree rather than the machine it runs on. The `real` arms are the
    ones the assertions below require the output to NAME as not run.
    """
    dest = tmp_path / "svc"
    assert _run([str(SCRIPT), "foundry-probe-service", str(dest)]).returncode == 0

    env = _emitted_service_environment()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(dest / "src"),
            str(dest / "service-kit" / "vendor"),
            str(dest / "service-kit" / "testing"),
            str(dest),
        ]
    )
    result = subprocess.run(
        [sys.executable, str(dest / "scripts" / "prove_seeded_tests.py"), "--profile", "fake"],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
        env=env,
    )

    assert result.returncode == 0, (
        f"the emitted prover did not prove its arms:\n{result.stdout[-4000:]}\n{result.stderr}"
    )
    # A prover that mutated nothing exits 0 in the same shape as one that proved
    # everything, so the words that only a real arm can print are the assertion.
    assert "=> PROVEN" in result.stdout, result.stdout
    assert "UNMEASURED" not in result.stdout, result.stdout
    assert "NOT PROVEN" not in result.stdout, result.stdout
    # THE ACCOUNTING. Hand-written rather than parsed back out of the run's own
    # total: deriving the expectation from the output would hold for a prover
    # whose table had been emptied.
    assert "union 8/8 across both profiles" in result.stdout, result.stdout
    assert "5 proven + 3 declared skip(s)" in result.stdout, result.stdout
    assert "NOT RUN HERE (3); `--profile real` takes them:" in result.stdout, result.stdout
    assert result.stdout.count("=> DECLARED SKIP") == 3, result.stdout
