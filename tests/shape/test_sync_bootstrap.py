"""sync.py can adopt the kit into a service that has none, and refuses every near miss.

Before this, `sync()` called `current_manifest()` first and exited 2
with "does not look like a service with a kit" whenever `service-kit/MANIFEST.json`
was absent -- which is true of every service that has not adopted the kit yet. So the
one command the rollout recipe named could not perform the one step the rollout needed
it for, and B1 bootstrapped with a hand-rolled `git archive` instead.

These tests drive the REAL script through subprocess against scratch git repositories,
rather than importing and calling `sync()`. That is deliberate: the defect was in
`main()`'s argument surface and in the ORDER of the guards, and a test that calls
`sync()` directly cannot see either -- the same reason a unit test cannot tell you
whether its caller still calls it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SYNC = REPO / "service-kit" / "sync.py"


def git(*argv: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True, check=True)


def run_sync(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SYNC), *argv], capture_output=True, text=True, check=False
    )


@pytest.fixture
def service(tmp_path: Path) -> Path:
    """An empty git checkout standing in for a service that has never adopted the kit."""
    root = tmp_path / "svc"
    root.mkdir()
    git("init", "--quiet", ".", cwd=root)
    git("commit", "--allow-empty", "--quiet", "-m", "init", cwd=root)
    assert not (root / "service-kit").exists()
    return root


@pytest.fixture
def revision() -> str:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return head.stdout.strip()


def test_bootstrap_adopts_the_kit_into_a_service_that_has_none(
    service: Path, revision: str
) -> None:
    """The step the rollout actually needs, and the one that used to be refused."""
    result = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert result.returncode == 0, result.stderr

    kit = service / "service-kit"
    assert kit.is_dir(), "the kit was not placed"
    assert (kit / "MANIFEST.json").is_file()
    assert (kit / "ci" / "verify.sh").is_file()
    # The placement is the half that is easy to forget: GitHub reads workflows from
    # one directory and nowhere else, so a kit without it is adopted but not wired.
    assert (service / ".github" / "workflows" / "ci.yml").is_file()

    # A floor, so a bootstrap that placed almost nothing cannot pass. The kit had 138
    # files when this was written; 50 is well clear of a broken extraction and well
    # under any plausible shrinkage.
    placed = [p for p in kit.rglob("*") if p.is_file()]
    assert len(placed) > 50, f"only {len(placed)} file(s) placed -- extraction looks partial"


def test_a_bootstrapped_kit_then_syncs_idempotently(service: Path, revision: str) -> None:
    """The second run is the one a service actually lives with."""
    first = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert first.returncode == 0, first.stderr
    # sync refuses a dirty kit, and a freshly bootstrapped one is untracked -- which is
    # what the bootstrap's own closing message tells the operator to resolve. Committing
    # here is not test scaffolding; it is the documented next step.
    git("add", "-A", cwd=service)
    git("commit", "--quiet", "-m", "bootstrap the kit", cwd=service)

    second = run_sync("--into", str(service), "--from", str(REPO))
    assert second.returncode == 0, second.stderr
    assert "0 added, 0 changed, 0 removed" in second.stdout, second.stdout


def test_bootstrap_refuses_when_a_kit_is_already_present(service: Path, revision: str) -> None:
    """Otherwise a bootstrap silently becomes an unchecked re-adoption."""
    first = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert first.returncode == 0, first.stderr

    again = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert again.returncode == 2
    assert "already exists" in again.stderr
    # The refusal has to name the remedy the reader can perform, not merely the problem.
    assert "without --bootstrap" in again.stderr


def test_bootstrap_refuses_without_a_revision(service: Path) -> None:
    """There is no manifest yet, so nothing can supply the default."""
    result = run_sync("--into", str(service), "--bootstrap", "--from", str(REPO))
    assert result.returncode == 2
    assert "--revision" in result.stderr


def test_into_refuses_a_directory_that_is_not_a_git_checkout(
    tmp_path: Path, revision: str
) -> None:
    """sync shells out to git to refuse a dirty kit; say so here, not deep inside."""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    result = run_sync(
        "--into", str(plain), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert result.returncode == 2
    assert "not a git checkout" in result.stderr


def test_emit_manifest_refuses_the_sync_only_flags(service: Path) -> None:
    """--emit-manifest records THIS tree, so a target argument is a contradiction."""
    for extra in (["--into", str(service)], ["--bootstrap"]):
        result = run_sync("--emit-manifest", *extra)
        assert result.returncode == 2, f"{extra} was accepted"
        assert "records THIS tree" in result.stderr


def test_the_default_target_is_unchanged_by_the_new_flags(revision: str) -> None:
    """NO-REGRESSION CONTROL.

    Every test above passes `--into`. If the new argument had become required, or had
    changed the default target, they would all still pass and the in-service usage that
    every adopted kit relies on would be broken. This asserts the default resolves to
    the tree the script sits in, with no `--into` given at all.
    """
    from importlib import util

    spec = util.spec_from_file_location("sync_under_test", SYNC)
    assert spec and spec.loader
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.resolve_target_root(None) == module.PACKAGE_ROOT == REPO


def test_a_synced_placement_names_the_service_and_still_passes_the_drift_gate(
    service: Path, revision: str
) -> None:
    """Regression: a sync used to revert ci.yml to the TEMPLATE's name.

    `sync.py` copied the placement byte for byte, so a service's workflow named
    `foundry-service-template` after every sync -- its image tag, its ECR repository
    and, before an earlier change, the package it built. `check_kit_drift` could not
    notice: it normalises the SERVICE's name back before hashing, and the file carried
    none. Both halves are asserted, because they are one contract: the placement names
    this service, and the drift gate still hashes it to the manifest.
    """
    package = service / "src" / "foundry_probe_sync"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    git("add", "-A", cwd=service)
    git("commit", "--quiet", "-m", "a service package", cwd=service)

    result = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert result.returncode == 0, result.stderr

    workflow = (service / ".github" / "workflows" / "ci.yml").read_text()
    assert "foundry-service-template" not in workflow, "the placement kept the template's name"
    assert "foundry_service_template" not in workflow, "the placement kept the template's module"
    # Hand-written floor: the workflow's `name:` plus the two image references today.
    assert workflow.count("foundry-probe-sync") >= 3, workflow.count("foundry-probe-sync")

    drift = subprocess.run(
        ["python3", str(service / "service-kit" / "gates" / "check_kit_drift.py")],
        cwd=service,
        capture_output=True,
        text=True,
        check=False,
    )
    assert drift.returncode == 0, drift.stdout + drift.stderr


def _drift(service: Path) -> subprocess.CompletedProcess:
    # sys.executable, not python3: a comms service's flavour is read with tomllib (3.11+).
    return subprocess.run(
        [sys.executable, str(service / "service-kit" / "gates" / "check_kit_drift.py")],
        cwd=service,
        capture_output=True,
        text=True,
        check=False,
    )


def _service_package(service: Path, name: str, *, comms: bool) -> None:
    package = service / "src" / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    if comms:
        (service / "pyproject.toml").write_text('[tool.foundry.kit]\nflavour = "comms"\n')
    git("add", "-A", cwd=service)
    git("commit", "--quiet", "-m", "a service package", cwd=service)


def test_a_comms_service_syncs_a_stripped_placement_the_drift_gate_accepts(
    service: Path, revision: str
) -> None:
    """Regression: every comms-* emission failed check_kit_drift on arrival.

    `apply_comms_flavour.py` strips marked blocks from ci.yml, and the manifest held
    only the unstripped digest. Now the manifest also records the comms digest,
    computed by that script's own strip, and sync writes the stripped placement into
    a service that declares the comms flavour. Four arms: the synced placement is
    stripped; the gate accepts it; an edit to the kept text is still a finding; and
    the pre-fix state -- a comms service holding the unstripped placement -- is red.
    """
    _service_package(service, "comms_probe_sync", comms=True)
    result = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert result.returncode == 0, result.stderr

    placement = service / ".github" / "workflows" / "ci.yml"
    workflow = placement.read_text()
    assert "COMMS-FLAVOUR-STRIP" not in workflow, "sync left the comms markers in the placement"
    assert "SQS_QUEUE_URL" not in workflow, "sync put the SQS path back into a comms service"
    assert "comms-probe-sync" in workflow, "the placement no longer names the service"

    clean = _drift(service)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "(comms flavour)" in clean.stdout, clean.stdout

    placement.write_text(workflow + "# an edit inside the kept text\n")
    edited = _drift(service)
    assert edited.returncode == 1, edited.stdout + edited.stderr
    assert ".github/workflows/ci.yml (placement)" in edited.stdout, edited.stdout

    unstripped = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    placement.write_text(unstripped.replace("foundry-service-template", "comms-probe-sync"))
    prefix = _drift(service)
    assert prefix.returncode == 1, prefix.stdout + prefix.stderr


def test_a_default_service_keeps_the_unstripped_placement(service: Path, revision: str) -> None:
    """The flavour is opt-in: an undeclared service syncs and judges exactly as before."""
    _service_package(service, "foundry_probe_default", comms=False)
    result = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert result.returncode == 0, result.stderr
    assert "SQS_QUEUE_URL" in (service / ".github" / "workflows" / "ci.yml").read_text()
    drift = _drift(service)
    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "(default flavour)" in drift.stdout, drift.stdout


def test_a_comms_package_that_declares_no_flavour_is_refused_before_the_kit_is_written(
    service: Path, revision: str
) -> None:
    """Regression: an undeclared comms_* service was synced as default and
    regained the SQS path; a declaration is now required, and the refusal comes first."""
    _service_package(service, "comms_probe_undeclared", comms=False)
    result = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "declares no kit flavour" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert not (service / "service-kit").exists()


def test_a_dotted_key_declares_the_comms_flavour(service: Path, revision: str) -> None:
    """The declaration is parsed, not matched as text: a dotted key is the same table."""
    _service_package(service, "comms_probe_dotted", comms=False)
    (service / "pyproject.toml").write_text('tool.foundry.kit.flavour = "comms"\n')
    git("add", "-A", cwd=service)
    git("commit", "--quiet", "-m", "declare", cwd=service)
    result = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert result.returncode == 0, result.stderr
    assert "SQS_QUEUE_URL" not in (service / ".github" / "workflows" / "ci.yml").read_text()


def _pre_strip_template(tmp_path: Path, revision: str) -> tuple[Path, str]:
    """A scratch clone of the template whose newest commit has an
    apply_comms_flavour.py without `strip_markers`, standing in for a template
    revision that predates it. Built here so the test needs no particular history."""
    clone = tmp_path / "template-pre-strip"
    git("clone", "--quiet", "--no-checkout", str(REPO), str(clone), cwd=tmp_path)
    git("checkout", "--quiet", revision, cwd=clone)
    script = clone / "scripts" / "apply_comms_flavour.py"
    text = script.read_text()
    assert "def strip_markers(" in text, "the strip moved; update this fixture"
    script.write_text(text.replace("def strip_markers(", "def _retired_strip_markers("))
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-am", "pre-strip",
        cwd=clone)
    head = git("rev-parse", "--short", "HEAD", cwd=clone).stdout.strip()
    return clone, head


def test_a_sync_to_a_revision_without_the_strip_refuses_before_touching_the_kit(
    service: Path, revision: str, tmp_path: Path
) -> None:
    """Regression: the strip was resolved after the kit was deleted, so a
    comms service synced to an older revision died with an AttributeError and 51 dirty
    kit paths. It is now resolved first, and the refusal names the revision."""
    _service_package(service, "comms_probe_old", comms=True)
    adopted = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert adopted.returncode == 0, adopted.stderr
    git("add", "-A", cwd=service)
    git("commit", "--quiet", "-m", "adopt", cwd=service)

    source, pre_strip = _pre_strip_template(tmp_path, revision)
    result = run_sync("--into", str(service), "--from", str(source), "--revision", pre_strip)
    assert result.returncode == 2, result.stdout + result.stderr
    assert pre_strip in result.stderr and "strip_markers" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    status = git("status", "--porcelain", cwd=service).stdout
    assert status == "", status


def _kit_bytes(service: Path) -> dict[str, bytes]:
    kit = service / "service-kit"
    return {
        p.relative_to(kit).as_posix(): p.read_bytes()
        for p in sorted(kit.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_default_syncs_stay_at_the_commit_the_kit_was_read_at(
    service: Path, revision: str
) -> None:
    """The copied manifest used to keep the TEMPLATE's own record, which names an older
    commit than the one holding it, so each default sync went back to that older kit
    (QA round 2: "0 added, 4 changed" on the second default sync).
    An abbreviated revision is recorded as the full commit id it resolved to."""
    first = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision[:12]
    )
    assert first.returncode == 0, first.stderr
    git("add", "-A", cwd=service)
    git("commit", "--quiet", "-m", "bootstrap the kit", cwd=service)
    adopted = _kit_bytes(service)
    assert f'"template_revision": "{revision}"' in adopted["MANIFEST.json"].decode()

    for attempt in (1, 2):
        again = run_sync("--into", str(service), "--from", str(REPO))
        assert again.returncode == 0, again.stderr
        assert "0 added, 0 changed, 0 removed" in again.stdout, (attempt, again.stdout)
        assert _kit_bytes(service) == adopted, f"default sync {attempt} moved the kit"


def _scratch_repo(root: Path) -> Path:
    root.mkdir()
    git("init", "--quiet", ".", cwd=root)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--allow-empty", "--quiet", "-m", "init", cwd=root)
    return root


def test_an_abbreviation_naming_two_mapped_commits_is_refused(
    service: Path, tmp_path: Path
) -> None:
    """A prefix that matches two lines of the absorb's commit map is not translated to
    either of them."""
    source = _scratch_repo(tmp_path / "estate")
    commit_map = source / "scripts" / "absorbed" / "foundry-service-template.commit-map"
    commit_map.parent.mkdir(parents=True)
    head = git("rev-parse", "HEAD", cwd=source).stdout.strip()
    commit_map.write_text(f"{'abcdef1' + '1' * 33} {head}\n{'abcdef1' + '2' * 33} {head}\n")
    git("add", "-A", cwd=source)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--quiet", "-m", "map", cwd=source)
    refused = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(source), "--revision", "abcdef1"
    )
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "ambiguous" in refused.stderr, refused.stderr
    assert not (service / "service-kit").exists()


def test_an_abbreviation_naming_two_local_commits_is_refused(
    service: Path, tmp_path: Path
) -> None:
    """A four-character prefix shared by two commits is refused, never resolved to
    whichever one git or the map happens to offer. The commits are made until two
    collide, so the control does not depend on any fixed hash."""
    source = _scratch_repo(tmp_path / "estate")
    tree = git("rev-parse", "HEAD^{tree}", cwd=source).stdout.strip()
    seen: dict[str, str] = {}
    prefix = None
    for n in range(5000):
        made = subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "commit-tree", tree, "-m", str(n)],
            cwd=source, capture_output=True, text=True, check=True,
        ).stdout.strip()
        if made[:4] in seen:
            prefix = made[:4]
            break
        seen[made[:4]] = made
    assert prefix, "no four-character collision in 5000 commits; the control cannot fire"
    refused = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(source), "--revision", prefix
    )
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "ambiguous" in refused.stderr, refused.stderr


def test_a_hex_named_branch_resolves_locally_before_the_commit_map(tmp_path: Path) -> None:
    """A branch whose NAME is hex and matches a commit-map prefix is a revision the
    repository knows, so it is used as it resolves; the map translates only unknown ids
    (QA round 3: the map prefix used to win and extract older bytes)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("sync_under_test", SYNC)
    assert spec and spec.loader
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)

    source = _scratch_repo(tmp_path / "estate")
    old = git("rev-parse", "HEAD", cwd=source).stdout.strip()
    commit_map = source / sync.TEMPLATE_COMMIT_MAP
    commit_map.parent.mkdir(parents=True)
    commit_map.write_text(f"{'abcdef1' + '1' * 33} {old}\n")
    git("add", "-A", cwd=source)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--quiet", "-m", "new", cwd=source)
    new = git("rev-parse", "HEAD", cwd=source).stdout.strip()
    git("branch", "abcdef1", cwd=source)

    assert sync.resolve_revision(source, "abcdef1") == new != old
    # Positive control: with the branch gone, the same id IS unknown here and the map
    # translates it -- so the arm above is about precedence, not a broken map.
    git("branch", "-D", "abcdef1", cwd=source)
    assert sync.resolve_revision(source, "abcdef1") == old


def test_a_service_in_a_subdirectory_of_a_checkout_syncs_and_refuses_a_dirty_kit(
    tmp_path: Path, revision: str
) -> None:
    """A service that is a directory of the monorepo has no `.git` of its own. It is
    still a checkout git can judge, so it adopts the kit, re-syncs idempotently, and a
    dirty kit is still refused (an earlier change: `services/comms-email` could not sync)."""
    estate = _scratch_repo(tmp_path / "estate")
    service = estate / "services" / "svc"
    service.mkdir(parents=True)
    assert not (service / ".git").exists()
    first = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert first.returncode == 0, first.stderr
    git("add", "-A", cwd=estate)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--quiet", "-m", "adopt", cwd=estate)
    again = run_sync("--into", str(service), "--from", str(REPO))
    assert again.returncode == 0, again.stderr
    assert "0 added, 0 changed, 0 removed" in again.stdout, again.stdout

    (service / "service-kit" / "ci" / "verify.sh").write_text("# edited by hand\n")
    refused = run_sync("--into", str(service), "--from", str(REPO))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "uncommitted changes" in refused.stderr, refused.stderr


def test_an_ignored_service_directory_is_refused_and_keeps_its_edits(
    tmp_path: Path, revision: str
) -> None:
    """`git status` says nothing about an ignored path, so a kit under one looked clean
    and a hand edit was deleted by the next sync (QA). A kit whose
    manifest git does not track is refused before anything is replaced."""
    estate = _scratch_repo(tmp_path / "estate")
    (estate / ".gitignore").write_text("services/\n")
    git("add", ".gitignore", cwd=estate)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--quiet", "-m", "ignore services", cwd=estate)
    service = estate / "services" / "svc"
    service.mkdir(parents=True)
    first = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert first.returncode == 0, first.stderr
    edited = service / "service-kit" / "ci" / "verify.sh"
    edited.write_text("# local work\n")

    refused = run_sync("--into", str(service), "--from", str(REPO))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "not tracked by git" in refused.stderr, refused.stderr
    assert edited.read_text() == "# local work\n"


def _ignored_service_with_a_kit(tmp_path: Path, revision: str) -> Path:
    """An estate that ignores `services/`, with a kit bootstrapped into services/svc and
    nothing of it tracked yet; each caller tracks what its case needs."""
    estate = _scratch_repo(tmp_path / "estate")
    (estate / ".gitignore").write_text("services/\n")
    git("add", ".gitignore", cwd=estate)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--quiet", "-m", "ignore services", cwd=estate)
    service = estate / "services" / "svc"
    service.mkdir(parents=True)
    first = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert first.returncode == 0, first.stderr
    return service


def test_a_kit_with_only_its_manifest_tracked_is_refused_and_keeps_its_edits(
    tmp_path: Path, revision: str
) -> None:
    """QA round 2: tracking the manifest alone satisfied a manifest-only check while every
    other kit file stayed ignored, so an edit to one of them was still deleted."""
    service = _ignored_service_with_a_kit(tmp_path, revision)
    estate = service.parents[1]
    git("add", "-f", "services/svc/service-kit/MANIFEST.json", cwd=estate)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--quiet", "-m", "manifest only", cwd=estate)
    edited = service / "service-kit" / "ci" / "verify.sh"
    edited.write_text("# local work\n")
    refused = run_sync("--into", str(service), "--from", str(REPO))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "not tracked by git" in refused.stderr, refused.stderr
    assert edited.read_text() == "# local work\n"


def test_one_ignored_file_in_an_otherwise_tracked_kit_is_refused_and_kept(
    tmp_path: Path, revision: str
) -> None:
    """QA round 2: every kit file tracked but one, which is ignored and edited."""
    service = _ignored_service_with_a_kit(tmp_path, revision)
    estate = service.parents[1]
    git("add", "-f", "services/svc", cwd=estate)
    git("rm", "--cached", "--quiet", "services/svc/service-kit/ci/verify.sh", cwd=estate)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--quiet", "-m", "all but one", cwd=estate)
    edited = service / "service-kit" / "ci" / "verify.sh"
    edited.write_text("# local work\n")
    assert git("status", "--porcelain", "--", "services/svc/service-kit", cwd=estate).stdout == ""
    refused = run_sync("--into", str(service), "--from", str(REPO))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "service-kit/ci/verify.sh" in refused.stderr, refused.stderr
    assert edited.read_text() == "# local work\n"


def _adopted(service: Path, revision: str) -> Path:
    """`service` with the kit bootstrapped and committed, ready for a default sync."""
    first = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert first.returncode == 0, first.stderr
    git("add", "-A", cwd=service)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--quiet", "-m", "adopt", cwd=service)
    return service


def _commit(service: Path, message: str) -> None:
    git("add", "-A", cwd=service)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "--quiet", "-m", message, cwd=service)


@pytest.mark.parametrize("kind", ["dangling", "directory"])
def test_an_ignored_symlink_in_the_kit_is_refused_and_kept(
    service: Path, revision: str, kind: str
) -> None:
    """Regression: a dangling or directory link is not a file, so the tracked-files
    inventory never saw it and `rmtree` deleted it at exit 0."""
    _adopted(service, revision)
    (service / ".gitignore").write_text("service-kit/local-link\n")
    _commit(service, "ignore the link")
    if kind == "directory":
        (service / "target").mkdir()
        (service / "target" / "work.txt").write_text("local work\n")
    link = service / "service-kit" / "local-link"
    link.symlink_to("../target", target_is_directory=True)
    assert git("status", "--porcelain", "--", "service-kit", cwd=service).stdout == ""

    refused = run_sync("--into", str(service), "--from", str(REPO))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "are symlinks" in refused.stderr, refused.stderr
    assert "service-kit/local-link" in refused.stderr, refused.stderr
    assert link.is_symlink()


def test_a_tracked_placement_symlink_is_refused_and_its_ignored_target_kept(
    service: Path, revision: str
) -> None:
    """Regression: a tracked link at a placement is clean, so every guard passed
    and `place` wrote through it into an ignored file outside the kit."""
    _adopted(service, revision)
    placement = service / ".github" / "workflows" / "ci.yml"
    placement.unlink()
    outside = service / "ignored-workflow.yml"
    outside.write_text("# valuable local workflow\n")
    (service / ".gitignore").write_text("ignored-workflow.yml\n")
    placement.symlink_to("../../ignored-workflow.yml")
    _commit(service, "link the workflow")
    assert git("status", "--porcelain", cwd=service).stdout == ""

    refused = run_sync("--into", str(service), "--from", str(REPO))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "are symlinks" in refused.stderr, refused.stderr
    assert ".github/workflows/ci.yml" in refused.stderr, refused.stderr
    assert outside.read_text() == "# valuable local workflow\n"
    assert placement.is_symlink()


def test_a_symlinked_placement_directory_is_refused(service: Path, revision: str) -> None:
    """The same write-through, one level up: `.github` itself is a link."""
    _adopted(service, revision)
    elsewhere = service / "elsewhere"
    (service / ".github").rename(elsewhere)
    (service / ".github").symlink_to("elsewhere", target_is_directory=True)
    _commit(service, "link .github")
    before = (elsewhere / "workflows" / "ci.yml").read_bytes()

    refused = run_sync("--into", str(service), "--from", str(REPO))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "are symlinks" in refused.stderr, refused.stderr
    assert ".github" in refused.stderr, refused.stderr
    assert (elsewhere / "workflows" / "ci.yml").read_bytes() == before


def test_bootstrap_refuses_an_ignored_placement_and_keeps_it(
    service: Path, revision: str
) -> None:
    """Regression: `--bootstrap` skipped the tracked-files inventory, so an ignored
    workflow already on disk was overwritten on first adoption."""
    (service / ".gitignore").write_text(".github/\n")
    _commit(service, "ignore .github")
    existing = service / ".github" / "workflows" / "ci.yml"
    existing.parent.mkdir(parents=True)
    existing.write_text("# valuable local workflow\n")

    refused = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert ".github/workflows/ci.yml" in refused.stderr, refused.stderr
    assert existing.read_text() == "# valuable local workflow\n"
    assert not (service / "service-kit").exists()


@pytest.mark.parametrize("form", ["regular", "file-link", "parent-link"])
def test_bootstrap_adopts_beside_an_ignored_owned_placement(
    service: Path, revision: str, form: str
) -> None:
    """A placement the service OWNS is never written, so none of the guards above may
    refuse it, whatever shape it has on disk (QA round 1). The unowned
    case is refused by `test_bootstrap_refuses_an_ignored_placement_and_keeps_it`."""
    (service / "pyproject.toml").write_text(
        '[tool.foundry.kit]\nowned_placements = [".github/workflows/ci.yml"]\n'
    )
    (service / ".gitignore").write_text(".github/\nlocal.yml\n")
    workflow = service / ".github" / "workflows" / "ci.yml"
    if form == "parent-link":
        (service / "external" / "workflows").mkdir(parents=True)
        (service / "external" / "workflows" / "ci.yml").write_text("# owned workflow\n")
        (service / ".github").symlink_to("external", target_is_directory=True)
    else:
        workflow.parent.mkdir(parents=True)
        if form == "regular":
            workflow.write_text("# owned workflow\n")
        else:
            (service / "local.yml").write_text("# owned workflow\n")
            workflow.symlink_to("../../local.yml")
    _commit(service, "own the workflow")

    adopted = run_sync(
        "--into", str(service), "--bootstrap", "--from", str(REPO), "--revision", revision
    )
    assert adopted.returncode == 0, adopted.stdout + adopted.stderr
    assert "owned_placements" in adopted.stdout, adopted.stdout
    assert workflow.read_text() == "# owned workflow\n"
    assert (service / "service-kit" / "MANIFEST.json").is_file()
