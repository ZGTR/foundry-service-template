"""A service's declared bytes survive sync and stay checked.

Every arm drives the REAL scripts through subprocess against
scratch git repositories, as `test_sync_bootstrap.py` does: the defects this guards
against live in the order of sync's steps and in what each gate reads, which a direct
call to an inner function cannot see.

The template a scratch service syncs FROM is itself a scratch repository built from
this checkout's working tree (tracked and untracked, ignored files aside), with its
manifest emitted there. Syncing from this repository at HEAD would test the last
COMMIT's kit, not the kit under review.

Each clause has an arm where it is the ONLY finding, so a red names one cause.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
VENDOR = "service-kit/vendor"
VENDOR_JSON = f"{VENDOR}/VENDOR.json"
CI = ".github/workflows/ci.yml"


def git(*argv: str, cwd: Path) -> str:
    done = subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout


def python(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *argv], cwd=cwd, capture_output=True, text=True, check=False
    )


def commit_all(root: Path, message: str) -> None:
    git("add", "-A", cwd=root)
    git("-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", message,
        cwd=root)


def tree_hash(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """(scratch template root, its revision), built from this working tree."""
    root = tmp_path_factory.mktemp("template")
    listed = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=REPO)
    for relative in filter(None, listed.split("\0")):
        source = REPO / relative
        if not source.is_file():
            continue  # deleted in the working tree
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    git("init", "--quiet", ".", cwd=root)
    commit_all(root, "template tree")
    emitted = python(str(root / "service-kit" / "sync.py"), "--emit-manifest", cwd=root)
    assert emitted.returncode == 0, emitted.stderr
    commit_all(root, "manifest")
    return root, git("rev-parse", "HEAD", cwd=root).strip()


def sync(service: Path, template: tuple[Path, str]) -> subprocess.CompletedProcess:
    root, revision = template
    return python(str(root / "service-kit" / "sync.py"), "--into", str(service),
                  "--from", str(root), "--revision", revision)


def gate(service: Path, name: str) -> subprocess.CompletedProcess:
    return python(str(service / "service-kit" / "gates" / name), cwd=service)


def digest(service: Path, package: str) -> str:
    """The content_sha256 `check_vendor_digests.digest` computes, called, not restated."""
    gates = service / "service-kit" / "gates"
    code = (f"import sys; sys.path.insert(0, {str(gates)!r}); from pathlib import Path; "
            f"from check_vendor_digests import digest; "
            f"print(digest(Path({str(service / VENDOR / package)!r}))[0])")
    done = python("-c", code)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture
def service(tmp_path: Path, template: tuple[Path, str]) -> Path:
    """A committed service with the template's kit, bootstrapped by the real sync."""
    root = tmp_path / "svc"
    root.mkdir()
    git("init", "--quiet", ".", cwd=root)
    commit_all(root, "init")
    template_root, revision = template
    done = python(str(template_root / "service-kit" / "sync.py"), "--into", str(root),
                  "--bootstrap", "--from", str(template_root), "--revision", revision)
    assert done.returncode == 0, done.stderr
    commit_all(root, "bootstrap the kit")
    return root


def declare(service: Path, body: str) -> None:
    (service / "pyproject.toml").write_text(f"[tool.foundry.kit]\n{body}", encoding="utf-8")


def vendor_package(service: Path, name: str) -> dict:
    """Vendor a small package with a correct VENDOR.json row; return the row."""
    package = service / VENDOR / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 'kept'\n", encoding="utf-8")
    (package / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    document = json.loads((service / VENDOR_JSON).read_text(encoding="utf-8"))
    row = {"content_sha256": digest(service, name), "source_package": f"elsewhere/{name}"}
    document["packages"][name] = row
    (service / VENDOR_JSON).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return row


def findings(result: subprocess.CompletedProcess) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith("  ")]


# --- AC1: sync preserves declared packages --------------------------------------------


def test_ac1_sync_keeps_a_declared_package_and_its_row(service, template) -> None:
    declare(service, 'service_vendored = ["pkg_x"]\n')
    row = vendor_package(service, "pkg_x")
    commit_all(service, "vendor pkg_x")
    before = tree_hash(service / VENDOR / "pkg_x")

    done = sync(service, template)
    assert done.returncode == 0, done.stderr
    assert tree_hash(service / VENDOR / "pkg_x") == before, "the declared bytes moved"
    merged = json.loads((service / VENDOR_JSON).read_text(encoding="utf-8"))
    assert merged["packages"]["pkg_x"] == row
    # The rest of VENDOR.json is the template's, byte for byte, once the row is stripped.
    template_bytes = (template[0] / VENDOR_JSON).read_text(encoding="utf-8")
    del merged["packages"]["pkg_x"]
    assert json.dumps(merged, indent=2, sort_keys=True) + "\n" == template_bytes
    assert "kept     service-kit/vendor/pkg_x/" in done.stdout


def test_ac1_control_without_the_declaration_sync_removes_both(service, template) -> None:
    vendor_package(service, "pkg_x")
    commit_all(service, "vendor pkg_x, undeclared")

    done = sync(service, template)
    assert done.returncode == 0, done.stderr
    assert not (service / VENDOR / "pkg_x").exists()
    assert (service / VENDOR_JSON).read_bytes() == (template[0] / VENDOR_JSON).read_bytes()


# --- AC2: a collision is refused before anything is deleted ---------------------------


def test_ac2_a_declared_name_the_template_ships_is_refused_untouched(service, template) -> None:
    shipped = sorted(p.name for p in (template[0] / VENDOR).iterdir() if p.is_dir())
    assert shipped, "the template ships no vendored package to collide with"
    declare(service, f'service_vendored = ["{shipped[0]}"]\n')
    commit_all(service, "declare a template package")
    before = tree_hash(service / "service-kit")

    done = sync(service, template)
    assert done.returncode == 2, done.stdout + done.stderr
    # The DIRECTORY check's own wording. The row merge refuses the same name a step later
    # with "already has row(s)", so asserting only on "shadow" passed with this check
    # deleted: two guards for one fixture, and the test could not tell which one spoke.
    assert "also ships under" in done.stderr and shipped[0] in done.stderr, done.stderr
    assert tree_hash(service / "service-kit") == before, "a refused sync changed the kit"


def test_ac2_a_declared_row_the_template_already_has_is_refused() -> None:
    """The row merge's own refusal, reached when a template ships a row and no directory
    (the directory check above cannot see that one)."""
    probe = (
        "import sys\n"
        "sys.path.insert(0, 'service-kit')\n"
        "from kit_ownership import OwnershipRefused, merge_rows\n"
        "template = '{\"packages\": {\"tpl_pkg\": {\"sha256\": \"0\"}}}'\n"
        "try:\n"
        "    merge_rows(template, {'tpl_pkg': {'sha256': '1'}})\n"
        "except OwnershipRefused as exc:\n"
        "    print('REFUSED', exc)\n"
        "print(merge_rows(template, {'svc_pkg': {'sha256': '1'}}))\n"
    )
    done = python("-c", probe, cwd=REPO)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "REFUSED" in done.stdout and "already has row(s) tpl_pkg" in done.stdout, done.stdout
    merged = json.loads(done.stdout.split("\n", 1)[1])
    assert sorted(merged["packages"]) == ["svc_pkg", "tpl_pkg"]


# --- AC3: an owned placement is never written -----------------------------------------


def test_ac3_an_owned_placement_is_left_byte_identical(service, template) -> None:
    declare(service, f'owned_placements = ["{CI}"]\n')
    workflow = service / CI
    workflow.write_text(workflow.read_text(encoding="utf-8") + "# this service's own\n")
    commit_all(service, "own the workflow")
    before = workflow.read_bytes()

    done = sync(service, template)
    assert done.returncode == 0, done.stderr
    assert workflow.read_bytes() == before
    assert f"kept     {CI}" in done.stdout


def test_ac3_control_without_the_declaration_the_template_file_replaces_it(
    service, template
) -> None:
    workflow = service / CI
    workflow.write_text(workflow.read_text(encoding="utf-8") + "# a local edit\n")
    commit_all(service, "edit the workflow")

    done = sync(service, template)
    assert done.returncode == 0, done.stderr
    assert workflow.read_bytes() == (template[0] / CI).read_bytes()


# --- AC4: drift reads the template's manifest -----------------------------------------


@pytest.fixture
def declared_service(service, template) -> Path:
    """A service carrying pkg_x, declared, after a real sync."""
    declare(service, 'service_vendored = ["pkg_x"]\n')
    vendor_package(service, "pkg_x")
    commit_all(service, "vendor pkg_x")
    assert sync(service, template).returncode == 0
    return service


def test_ac4_a_declared_package_on_the_template_manifest_is_green(declared_service, template):
    manifest = declared_service / "service-kit" / "MANIFEST.json"
    # Sync records the commit it read the kit at, which cannot be the revision the
    # template's own manifest names (that manifest is part of the commit); every other
    # field is the template's.
    expected = json.loads((template[0] / "service-kit" / "MANIFEST.json").read_text())
    expected["template_revision"] = template[1]
    assert json.loads(manifest.read_text()) == expected
    drift = gate(declared_service, "check_kit_drift.py")
    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "1 service-vendored package(s)" in drift.stdout
    digests = gate(declared_service, "check_vendor_digests.py")
    assert digests.returncode == 0, digests.stdout + digests.stderr


def _edit_row(service: Path, name: str) -> None:
    document = json.loads((service / VENDOR_JSON).read_text(encoding="utf-8"))
    document["packages"][name]["content_sha256"] = "0" * 64
    (service / VENDOR_JSON).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def test_ac4_editing_a_declared_row_reds_only_the_digest_gate(declared_service) -> None:
    _edit_row(declared_service, "pkg_x")
    digests = gate(declared_service, "check_vendor_digests.py")
    assert digests.returncode == 1, digests.stdout + digests.stderr
    assert "pkg_x" in digests.stdout + digests.stderr
    drift = gate(declared_service, "check_kit_drift.py")
    assert drift.returncode == 0, "drift strips declared rows; the digest gate owns them"


def test_ac4_editing_a_template_row_reds_drift(declared_service, template) -> None:
    shipped = sorted(p.name for p in (template[0] / VENDOR).iterdir() if p.is_dir())
    _edit_row(declared_service, shipped[0])
    drift = gate(declared_service, "check_kit_drift.py")
    assert drift.returncode == 1, drift.stdout + drift.stderr
    assert len(findings(drift)) == 1, drift.stdout
    assert VENDOR_JSON in drift.stdout and "declared rows stripped" in drift.stdout


NON_ASCII = "Zoë — platform"


def test_ac4_a_template_row_outside_ascii_leaves_a_declaring_service_green(
    service, template, tmp_path
) -> None:
    """The strip must give back the template's bytes whatever characters a row holds.

    The template's writer keeps a character outside ASCII as itself; the strip escaped
    it, so the stripped hash was never the manifest's. The same sync
    without a declaration is the control: it never strips, and was green throughout.
    """
    root = tmp_path / "template-outside-ascii"
    shutil.copytree(template[0], root)
    document = json.loads((root / VENDOR_JSON).read_text(encoding="utf-8"))
    shipped = sorted(document["packages"])[0]
    document["packages"][shipped]["reviewed_by"] = NON_ASCII
    # Exactly as scripts/vendor_package.py writes the file.
    (root / VENDOR_JSON).write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    commit_all(root, "a template row outside ASCII")
    emitted = python(str(root / "service-kit" / "sync.py"), "--emit-manifest", cwd=root)
    assert emitted.returncode == 0, emitted.stderr
    commit_all(root, "manifest")
    moved = (root, git("rev-parse", "HEAD", cwd=root).strip())

    assert sync(service, moved).returncode == 0
    undeclared = gate(service, "check_kit_drift.py")
    assert undeclared.returncode == 0, undeclared.stdout + undeclared.stderr
    commit_all(service, "the moved template, nothing declared")

    declare(service, 'service_vendored = ["pkg_x"]\n')
    vendor_package(service, "pkg_x")
    commit_all(service, "vendor pkg_x")
    assert sync(service, moved).returncode == 0
    drift = gate(service, "check_kit_drift.py")
    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "1 service-vendored package(s)" in drift.stdout
    # The merged file keeps the character as the template wrote it.
    assert NON_ASCII in (service / VENDOR_JSON).read_text(encoding="utf-8")


def test_ac4_an_undeclared_package_dir_reds_drift(declared_service) -> None:
    stray = declared_service / VENDOR / "pkg_y"
    stray.mkdir()
    (stray / "__init__.py").write_text("", encoding="utf-8")
    drift = gate(declared_service, "check_kit_drift.py")
    assert drift.returncode == 1, drift.stdout + drift.stderr
    assert len(findings(drift)) == 1, drift.stdout
    assert f"{VENDOR}/pkg_y/__init__.py is inside the kit" in drift.stdout


# --- AC5: an owned placement must still run every kit gate ----------------------------


@pytest.fixture
def owning_service(service) -> Path:
    declare(service, f'owned_placements = ["{CI}"]\n')
    workflow = service / CI
    workflow.write_text(workflow.read_text(encoding="utf-8") + "# this service's release\n")
    return service


def test_ac5_an_owned_placement_running_every_gate_is_green(owning_service) -> None:
    drift = gate(owning_service, "check_kit_drift.py")
    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "1 owned placement(s)" in drift.stdout


def test_ac5_dropping_one_gate_from_an_owned_placement_reds_and_names_it(owning_service):
    workflow = owning_service / CI
    lines = workflow.read_text(encoding="utf-8").splitlines(keepends=True)
    target = "service-kit/gates/check_layering.py"
    kept = [line for line in lines if not (line.strip().startswith("run:") and target in line)]
    assert len(kept) == len(lines) - 1, "the workflow does not run check_layering.py once"
    workflow.write_text("".join(kept), encoding="utf-8")
    drift = gate(owning_service, "check_kit_drift.py")
    assert drift.returncode == 1, drift.stdout + drift.stderr
    assert len(findings(drift)) == 1, drift.stdout
    assert "never runs service-kit/gates/check_layering.py" in drift.stdout


# --- AC6: a malformed declaration is a named refusal, from both readers ---------------


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ('service_vendor = ["pkg_x"]\n', "unknown key(s) service_vendor"),
        ('service_vendored = ["pkg-x"]\n', "not importable package names"),
        ('owned_placements = ["Dockerfile"]\n', "only a kit placement can be owned"),
    ],
    ids=["unknown key", "non-identifier name", "placement outside PLACEMENTS"],
)
def test_ac6_each_malformed_declaration_exits_2_naming_why(service, template, body, reason):
    declare(service, body)
    commit_all(service, "malformed declaration")
    before = tree_hash(service / "service-kit")
    synced = sync(service, template)
    assert synced.returncode == 2, synced.stdout + synced.stderr
    assert reason in synced.stderr
    assert tree_hash(service / "service-kit") == before
    drift = gate(service, "check_kit_drift.py")
    assert drift.returncode == 2, drift.stdout + drift.stderr
    assert reason in drift.stderr


# --- a placement that names the service it lands in ------------------------------------
#
# sync renames a placement for the service and check_kit_drift undoes the rename before
# it hashes. Text that named the service before the rename is undone with it, so drift
# can never be green. comms-core could not adopt a kit revision whose ci.yml said, in a
# comment, whose suite had hung.

SERVICE_MODULE = "widget_shop"


@pytest.fixture
def named_service(service: Path) -> Path:
    """`service` with one package under `src/`, so sync and drift both rename."""
    package = service / "src" / SERVICE_MODULE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    commit_all(service, "a package of its own")
    return service


def template_naming(tmp_path: Path, template: tuple[Path, str], comment: str) -> tuple[Path, str]:
    """A copy of the scratch template whose ci.yml carries one more comment line."""
    root = tmp_path / "template-naming"
    shutil.copytree(template[0], root)
    workflow = root / CI
    workflow.write_text(workflow.read_text(encoding="utf-8") + f"# {comment}\n", encoding="utf-8")
    commit_all(root, "a comment")
    emitted = python(str(root / "service-kit" / "sync.py"), "--emit-manifest", cwd=root)
    assert emitted.returncode == 0, emitted.stderr
    commit_all(root, "manifest")
    return root, git("rev-parse", "HEAD", cwd=root).strip()


def test_a_named_service_syncs_and_its_renamed_placement_matches_the_manifest(
    named_service: Path, template: tuple[Path, str]
) -> None:
    """The control: with no such text the rename round-trips and drift is green."""
    done = sync(named_service, template)
    assert done.returncode == 0, done.stdout + done.stderr
    placed = (named_service / CI).read_text(encoding="utf-8")
    assert "widget-shop" in placed, "the placement was not renamed for the service"
    commit_all(named_service, "sync")

    drift = gate(named_service, "check_kit_drift.py")

    assert drift.returncode == 0, drift.stdout + drift.stderr


@pytest.mark.parametrize("name", ["widget-shop", "widget_shop"])
def test_a_placement_that_already_names_the_service_is_refused_untouched(
    named_service: Path, template: tuple[Path, str], tmp_path: Path, name: str
) -> None:
    naming = template_naming(tmp_path, template, f"{name} hung here once")
    last_line = len((naming[0] / CI).read_text(encoding="utf-8").splitlines())
    before = tree_hash(named_service)

    done = sync(named_service, naming)

    assert done.returncode == 2, done.stdout + done.stderr
    assert f"already names this service in {CI}" in done.stderr
    assert f"({name!r}, line {last_line}; 1 mention(s))" in done.stderr
    assert tree_hash(named_service) == before, "a refused sync changed the service"


def test_the_same_comment_is_no_obstacle_to_a_service_of_another_name(
    named_service: Path, template: tuple[Path, str], tmp_path: Path
) -> None:
    """The refusal is about THIS service's name, not about naming a service."""
    naming = template_naming(tmp_path, template, "gadget-yard hung here once")

    done = sync(named_service, naming)
    assert done.returncode == 0, done.stdout + done.stderr
    commit_all(named_service, "sync")

    drift = gate(named_service, "check_kit_drift.py")
    assert drift.returncode == 0, drift.stdout + drift.stderr


def test_without_the_refusal_such_a_sync_leaves_drift_red_for_good(
    named_service: Path, template: tuple[Path, str], tmp_path: Path
) -> None:
    """Why the refusal exists, shown on the real gate: place the file by hand, as a
    sync with no refusal would, and drift reports an edit that nobody made."""
    naming = template_naming(tmp_path, template, "widget-shop hung here once")
    assert sync(named_service, template).returncode == 0
    text = (naming[0] / CI).read_text(encoding="utf-8")
    renamed = text.replace("foundry_service_template", SERVICE_MODULE)
    renamed = renamed.replace("foundry-service-template", "widget-shop")
    (named_service / CI).write_text(renamed, encoding="utf-8")
    manifest = (naming[0] / "service-kit" / "MANIFEST.json").read_text(encoding="utf-8")
    (named_service / "service-kit" / "MANIFEST.json").write_text(manifest, encoding="utf-8")
    commit_all(named_service, "placed by hand")

    drift = gate(named_service, "check_kit_drift.py")

    assert drift.returncode == 1, drift.stdout + drift.stderr
    assert f"{CI} (placement): sha256" in drift.stdout


# --- the one reader --------------------------------------------------------------------


def test_the_single_reader_gate_is_green_on_this_checkout() -> None:
    done = python(str(REPO / "scripts" / "check_kit_ownership_single_reader.py"))
    assert done.returncode == 0, done.stdout + done.stderr
