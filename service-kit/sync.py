"""Replace this service's `service-kit/` with the template's, at a revision.

Two modes, one script, because they are two halves of one contract and a second
file is how two halves come to disagree about what the kit is:

    python service-kit/sync.py --emit-manifest
        Run IN THE TEMPLATE. Hashes the kit as it stands, records the template's
        own HEAD as `template_revision`, and writes `service-kit/MANIFEST.json`.

    python service-kit/sync.py [--from <path-or-url>] [--revision <rev>]
        Run IN A SERVICE. Fetches the template at `--revision` (default: the
        revision the current manifest names, so a re-run is idempotent and a
        BUMP is an explicit argument), replaces `service-kit/` wholesale,
        rewrites the placements, and prints added / changed / removed.

Stdlib only, on purpose: this runs in a fresh service checkout before anything
is installed, and a sync that needs its own dependencies is a sync nobody runs.

WHOLESALE REPLACEMENT, not a merge. A merge would need to decide which side wins
per file, and the answer is always the template -- that is what "template-owned"
means. So the kit is deleted and re-extracted, which is also the only way a file
DELETED in the template leaves the service; a copy-over-the-top sync leaves
orphans behind forever, and an orphan inside the kit is what
`check_kit_drift.py` reports as a fork.

EXCEPT WHAT THE SERVICE DECLARES. A service names the bytes it
owns in its pyproject.toml's `[tool.foundry.kit]`, read by `kit_ownership.py`:
`service_vendored` packages under `service-kit/vendor/` are lifted out before the kit
is deleted and put back after, with their VENDOR.json rows merged into the template's
file; `owned_placements` are never written. A declared package the template also
ships is refused before anything is deleted, because restoring it would silently
shadow the template's copy.

IT REFUSES A DIRTY KIT. Uncommitted work inside `service-kit/` is either
someone's local experiment, which this would silently destroy, or a hand edit
that ought to go to the template first. Either way the honest move is to stop
and say which files. `--force` is deliberately not offered: the remedy is
`git checkout -- service-kit` or a commit, both of which the author can see.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# The declaration's one reader sits beside this script and is stdlib-only too.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kit_ownership import (  # noqa: E402
    DEFAULT_FLAVOUR,
    PLACEMENTS,
    VENDOR_DIR,
    VENDOR_JSON,
    KitOwnership,
    OwnershipRefused,
    declared_rows,
    merge_rows,
    read_ownership,
)

KIT_DIRNAME = "service-kit"
MANIFEST_NAME = "MANIFEST.json"
#: The kit file that carries the hashes cannot be one of the hashes.
EXCLUDED = (MANIFEST_NAME,)
#: The two spellings of the template's own name a placement carries. A placement
#: NAMES its service -- the workflow's `name:`, its image tag, its ECR repository --
#: so the template's copy says these and a service's copy says its own. The same pair
#: is what `gates/check_kit_drift.py::placement_digest` normalises back before
#: hashing; the two must stay the inverse of each other.
TEMPLATE_MODULE = "foundry_service_template"
TEMPLATE_NAME = "foundry-service-template"

#: A service declares its flavour in its own pyproject.toml, written there by
#: `scripts/apply_comms_flavour.py` at emission and read by `kit_ownership.py`. A
#: comms flavour strips marked blocks from the placement, so its digest is recorded
#: separately, in the manifest's `placements_by_flavour`, and computed by that
#: script's own strip.
FLAVOUR_SCRIPT = "scripts/apply_comms_flavour.py"

KIT_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = KIT_ROOT.parent
DEFAULT_TEMPLATE_URL = "https://github.com/ZGTR/foundry-service-template.git"
#: A source may hold the template at its root (this repository, or a scratch copy)
#: or under this prefix inside a larger repository. `git archive <rev>` of a repository
#: with no `service-kit/` at that place fails with "wrong revision or wrong source".
TEMPLATE_PREFIX = "."
#: Optional map from an older `template_revision` to the commit that now carries the
#: same bytes, one `<old> <new>` pair a line. Absent in this repository.
TEMPLATE_COMMIT_MAP = "scripts/absorbed/foundry-service-template.commit-map"


def die(message: str) -> None:
    print(f"sync: {message}", file=sys.stderr)
    raise SystemExit(2)


def run(argv: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        die(f"`{' '.join(argv)}` exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kit_file_map(package_root: Path) -> dict[str, str]:
    kit = package_root / KIT_DIRNAME
    out: dict[str, str] = {}
    for path in sorted(kit.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.relative_to(kit).as_posix() in EXCLUDED:
            continue
        out[path.relative_to(package_root).as_posix()] = sha256_of(path)
    return out


def placement_map(package_root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for relative in PLACEMENTS:
        path = package_root / relative
        if path.is_file():
            out[relative] = sha256_of(path)
    return out


def ownership(package_root: Path) -> KitOwnership:
    """The service's `[tool.foundry.kit]` declaration, from the kit's one reader.

    A malformed declaration is a refusal (exit 2) naming the reason, never "nothing
    declared": syncing on a misread declaration would delete the bytes it meant to keep.
    """
    try:
        return read_ownership(package_root, module=service_module(package_root))
    except OwnershipRefused as exc:
        die(str(exc))
    raise AssertionError("unreachable")


def flavour_strip(template_root: Path, revision: str = "this tree"):
    """The template's own strip function, loaded from the template tree.

    Refuses (exit 2) when the template at `revision` has no strip, so a sync to a
    revision older than the flavour stops before the kit is touched rather than failing halfway with an AttributeError.
    """
    script = template_root / FLAVOUR_SCRIPT
    if not script.is_file():
        die(f"the template at {revision} has no {FLAVOUR_SCRIPT}; it predates the comms flavour")
    spec = importlib.util.spec_from_file_location("_apply_comms_flavour", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    strip = getattr(module, "strip_markers", None)
    if strip is None:
        die(
            f"the template at {revision} has {FLAVOUR_SCRIPT} without strip_markers; it "
            f"predates flavoured placements, so a comms service cannot sync to it"
        )
    return strip


def flavoured_placement_map(package_root: Path) -> dict[str, dict[str, str]]:
    """Each non-default flavour's placement digests, stripped by the one strip."""
    strip = flavour_strip(package_root)
    comms: dict[str, str] = {}
    for relative in PLACEMENTS:
        text = (package_root / relative).read_text(encoding="utf-8")
        comms[relative] = hashlib.sha256(strip(text, source=relative).encode("utf-8")).hexdigest()
    return {"comms": comms}


def emit_manifest() -> int:
    """Write the manifest from the tree this script sits in (template mode)."""
    files = kit_file_map(PACKAGE_ROOT)
    if not files:
        die(
            f"no files found under {PACKAGE_ROOT / KIT_DIRNAME}; "
            f"refusing to write an empty manifest"
        )
    placements = placement_map(PACKAGE_ROOT)
    if len(placements) != len(PLACEMENTS):
        missing = [p for p in PLACEMENTS if p not in placements]
        die(f"placement(s) missing from this tree: {', '.join(missing)}")

    revision = run(["git", "rev-parse", "HEAD"], cwd=PACKAGE_ROOT).strip()
    dirty = run(["git", "status", "--porcelain", "--", KIT_DIRNAME, *PLACEMENTS], cwd=PACKAGE_ROOT)
    manifest = {
        "template_revision": revision,
        "files": files,
        "placements": placements,
        "placements_by_flavour": flavoured_placement_map(PACKAGE_ROOT),
    }
    (PACKAGE_ROOT / KIT_DIRNAME / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(f"sync: wrote {len(files)} file hash(es) and {len(placements)} placement(s)")
    print(f"sync: template_revision {revision}")
    if dirty.strip():
        # NOT fatal, and said out loud rather than swallowed: hashing an
        # uncommitted tree records a revision that does not describe it, so the
        # manifest is right about the bytes and wrong about their provenance.
        print(
            "sync: WARNING -- the kit is dirty, so template_revision names a commit that does "
            "not contain these bytes. Commit, then re-emit.",
            file=sys.stderr,
        )
    return 0


def current_manifest(package_root: Path) -> dict:
    path = package_root / KIT_DIRNAME / MANIFEST_NAME
    if not path.is_file():
        die(f"{path} does not exist; this does not look like a service with a kit")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        die(f"{path} is not valid JSON: {exc}")
    raise AssertionError("unreachable")


def refuse_dirty_kit(package_root: Path) -> None:
    porcelain = run(
        ["git", "status", "--porcelain", "--", KIT_DIRNAME, *PLACEMENTS], cwd=package_root
    )
    changed = [line for line in porcelain.splitlines() if line.strip()]
    if changed:
        print("sync: the kit has uncommitted changes; refusing to replace it:", file=sys.stderr)
        for line in changed:
            print(f"  {line}", file=sys.stderr)
        die("commit them, or `git checkout -- service-kit`, then run sync again")


def refuse_untracked_kit(package_root: Path, owned: KitOwnership) -> None:
    """A file git does not track cannot be judged dirty: `git status` is silent about an
    ignored path, so a hand edit there would be deleted without the refusal below ever
    seeing it. Every file sync is about to delete or overwrite -- the kit, caches aside,
    and each placement present -- must be tracked first (QA rounds 1 and
    2: a gitignored service directory lost its edits, first with nothing tracked, then
    with only the manifest tracked). A placement the service OWNS is never written, so it
    is not inventoried: an ignored owned workflow is the service's business, and a
    bootstrap must adopt the kit beside it (QA round 1)."""
    written = [relative for relative in PLACEMENTS if relative not in owned.owned_placements]
    kit = package_root / KIT_DIRNAME
    on_disk = sorted(
        path.relative_to(package_root).as_posix()
        for path in (kit.rglob("*") if kit.is_dir() else ())
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    on_disk += [relative for relative in written if (package_root / relative).is_file()]
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", KIT_DIRNAME, *written],
        cwd=package_root, capture_output=True, text=True, check=False,
    )
    if listed.returncode != 0:
        die(f"git ls-files failed in {package_root}: {listed.stderr.strip()}")
    tracked = set(filter(None, listed.stdout.split("\0")))
    untracked = [path for path in on_disk if path not in tracked]
    if untracked:
        print("sync: these kit files are not tracked by git, so a change to them could not "
              "be refused; refusing to replace them:", file=sys.stderr)
        for path in untracked[:20]:
            print(f"  {path}", file=sys.stderr)
        if len(untracked) > 20:
            print(f"  ... and {len(untracked) - 20} more", file=sys.stderr)
        die("commit them (or stop ignoring them), then run sync again")


def refuse_symlinks(package_root: Path, owned: KitOwnership) -> None:
    """Sync replaces regular files, and the template ships no symlink in the kit or a
    placement. A symlink there is therefore never the template's, and sync cannot
    replace one safely: `rmtree` drops a link without asking whose it was, and `place`
    writes THROUGH a link to wherever it points, ignored files included. The tracked
    and dirty checks cannot see either case, because a dangling or directory link is
    not a file and a tracked link to an ignored file is clean. So any
    symlink in the kit, at a placement sync writes, or on the path to one, is refused
    before anything is touched."""
    found: list[str] = []
    kit = package_root / KIT_DIRNAME
    if kit.is_symlink():
        found.append(KIT_DIRNAME)
    elif kit.is_dir():
        for directory, dirnames, filenames in os.walk(kit):
            for name in sorted(dirnames + filenames):
                path = Path(directory) / name
                if path.is_symlink():
                    found.append(path.relative_to(package_root).as_posix())
    for relative in PLACEMENTS:
        if relative in owned.owned_placements:
            continue
        path = package_root
        for part in Path(relative).parts:
            path = path / part
            if path.is_symlink():
                found.append(path.relative_to(package_root).as_posix())
                break
    found = sorted(set(found))
    if found:
        print("sync: these paths are symlinks, which sync would drop or write through; "
              "refusing to replace them:", file=sys.stderr)
        for path in found:
            print(f"  {path}", file=sys.stderr)
        die("replace each with the regular file it stands for, then run sync again")


def _git_ok(argv: list[str], cwd: Path) -> bool:
    return subprocess.run(["git", *argv], cwd=cwd, capture_output=True, check=False).returncode == 0


def _is_hex(revision: str) -> bool:
    return 4 <= len(revision) <= 40 and all(c in "0123456789abcdef" for c in revision.lower())


def resolve_revision(repo: Path, revision: str) -> str:
    """The full commit id `revision` names in `repo`, else its rewritten id from the
    absorb's commit map.

    Only an id the repository does not know is translated, so a monorepo revision, a
    branch name or a commit of the archived template repository is used as given. An
    abbreviation that names more than one commit, here or in the map, is REFUSED rather
    than guessed. An id neither knows is returned unchanged and `git archive` names it
    in the refusal.
    """
    if _is_hex(revision):
        listed = subprocess.run(
            ["git", "rev-parse", f"--disambiguate={revision.lower()}"],
            cwd=repo, capture_output=True, text=True, check=False,
        ).stdout.split()
        commits = [
            sha for sha in listed
            if subprocess.run(["git", "cat-file", "-t", sha], cwd=repo, capture_output=True,
                              text=True, check=False).stdout.strip() == "commit"
        ]
        if len(commits) > 1:
            die(f"{revision} is ambiguous in {repo}: it abbreviates {len(commits)} commits; "
                "give more of the id")
    # Every input the repository can resolve is used as it resolves -- a hex-looking
    # BRANCH name included, which a commit-map prefix would otherwise shadow.
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if done.returncode == 0 and done.stdout.strip():
        return done.stdout.strip()
    shown = subprocess.run(
        ["git", "show", f"HEAD:{TEMPLATE_COMMIT_MAP}"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if shown.returncode != 0 or len(revision) < 7 or not _is_hex(revision):
        return revision
    hits = [
        new for old, new in (line.split() for line in shown.stdout.splitlines() if line.strip())
        if old.startswith(revision.lower())
    ]
    if len(hits) > 1:
        die(f"{revision} is ambiguous in {TEMPLATE_COMMIT_MAP}: it abbreviates {len(hits)} "
            "imported commits; give more of the id")
    return hits[0] if hits else revision


def template_tree(repo: Path, revision: str) -> tuple[str, str]:
    """The tree-ish holding the template at `revision`, and the commit it was read at:
    the commit itself when the template is the repository, `<commit>:<TEMPLATE_PREFIX>`
    when it is a directory."""
    resolved = resolve_revision(repo, revision)
    if _git_ok(["cat-file", "-e", f"{resolved}:{KIT_DIRNAME}"], repo):
        return resolved, resolved
    if _git_ok(["cat-file", "-e", f"{resolved}:{TEMPLATE_PREFIX}/{KIT_DIRNAME}"], repo):
        return f"{resolved}:{TEMPLATE_PREFIX}", resolved
    return resolved, resolved


def _archive(repo: Path, revision: str, archive: Path, where: str) -> str:
    """Write the template at `revision` to `archive`; return the commit it was read at."""
    tree, commit = template_tree(repo, revision)
    # From the repository's TOP, not from `repo`: run inside a subdirectory, `git archive`
    # keeps only the paths under that subdirectory, and the template's own directory read
    # as a prefix of its own subtree selects nothing -- an empty tar at exit 0.
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True, text=True,
        check=False,
    ).stdout.strip() or str(repo)
    with archive.open("wb") as handle:
        result = subprocess.run(
            ["git", "archive", "--format=tar", tree],
            cwd=top,
            stdout=handle,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
        )
    if result.returncode != 0:
        die(f"git archive {revision} in {where} failed: {result.stderr.decode().strip()}")
    return commit


def extract_template(source: str, revision: str, into: Path) -> tuple[Path, str]:
    """Materialise the template at `revision` under `into`. Returns its root and the
    full commit id it was read at."""
    archive = into / "template.tar"
    local = Path(source).expanduser()
    if local.is_dir():
        commit = _archive(local, revision, archive, str(local))
    else:
        clone = into / "clone"
        run(["git", "clone", "--quiet", "--no-checkout", source, str(clone)])
        commit = _archive(clone, revision, archive, "the clone")

    root = into / "template"
    root.mkdir()
    with tarfile.open(archive) as tar:
        # `filter="data"` refuses absolute paths, `..` and device files. A sync
        # extracts an archive over a working tree, which is exactly where a
        # path-traversal entry would be worth planting.
        tar.extractall(root, filter="data")
    if not (root / KIT_DIRNAME).is_dir():
        die(f"the template at {revision} has no {KIT_DIRNAME}/ -- wrong revision or wrong source")
    return root, commit


def resolve_target_root(into: str | None) -> Path:
    """Which service this sync writes into.

    Defaults to PACKAGE_ROOT -- the tree this script sits in -- so a service
    running its own vendored copy behaves exactly as before. `--into` exists for
    the case PACKAGE_ROOT cannot express: running the TEMPLATE's sync.py against
    a service that does not have a kit yet, which is the first adoption and the
    one step the rollout actually needs.
    """
    if into is None:
        return PACKAGE_ROOT
    root = Path(into).expanduser().resolve()
    if not root.is_dir():
        die(f"--into {root} is not a directory")
    # refuse_dirty_kit shells out to git with cwd=root, and a non-repo there
    # would fail deep inside with a confusing message about the kit rather than
    # about the path. Say it here, where the reader can act on it. Git is ASKED
    # rather than `<root>/.git` tested: a service that is a directory of the
    # monorepo has no `.git` of its own and is still a checkout git can judge
    #.
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        die(f"--into {root} is not a git checkout; sync needs one to refuse a dirty kit")
    return root


def service_module(package_root: Path) -> str | None:
    """The one package under `src/`, or None when there is not exactly one.

    The same rule as `gates/gate_common.py::source_root`, restated here because this
    script is stdlib-only and runs before anything is importable. Keeping the rule
    identical is what makes a synced placement hash to the manifest: the drift gate
    normalises back exactly the name this function supplies.
    """
    src = package_root / "src"
    if not src.is_dir():
        return None
    packages = sorted(p.name for p in src.iterdir() if p.is_dir() and (p / "__init__.py").is_file())
    return packages[0] if len(packages) == 1 else None


def place(incoming: Path, destination: Path, module: str | None, strip=None) -> None:
    """Write a placement, renamed for THIS service.

    This used to be a plain `shutil.copy2`, so every sync reverted
    a service's workflow to the TEMPLATE's name: memory-service's ci.yml named
    `foundry-service-template` six times after a re-sync, built the template's
    package and would have pushed to the template's ECR repository. `check_kit_drift`
    stayed green throughout, because it normalises the service's own name and the
    file carried none. `new_service.sh` performs this rename on emission; a sync is
    the other writer of the same file and has to perform it too.
    """
    text = incoming.read_text(encoding="utf-8")
    if strip is not None:
        # A comms service's placement lacks the marked blocks; without this a sync
        # would put them back, and the flavoured digest would then be the one
        # that disagrees.
        text = strip(text, source=destination.name)
    if module is not None and module != TEMPLATE_MODULE:
        text = text.replace(TEMPLATE_MODULE, module)
        text = text.replace(TEMPLATE_NAME, module.replace("_", "-"))
    destination.write_text(text, encoding="utf-8")
    shutil.copymode(incoming, destination)


def refuse_collisions(owned: KitOwnership, template: Path, revision: str) -> None:
    """A declared package the template also ships would shadow the template's copy."""
    shipped = template / VENDOR_DIR
    clash = sorted(name for name in owned.service_vendored if (shipped / name).exists())
    if clash:
        die(
            f"service_vendored names {', '.join(clash)}, which the template at {revision[:9]} "
            f"also ships under {VENDOR_DIR}/; restoring the service's copy would shadow it. "
            f"Rename the service's package or drop it from service_vendored. Nothing was changed"
        )


def refuse_a_placement_that_names_the_service(
    owned: KitOwnership, template: Path, module: str | None, revision: str
) -> None:
    """A placement may not already hold the name it is about to be renamed to.

    `place` renames the template to this service, and check_kit_drift undoes that
    rename before it hashes: every occurrence of the service's name goes back to
    the template's. Text that named the service BEFORE the rename goes back with
    it, the hash can never equal the manifest's, and drift reports an edit nobody
    made. Measured in comms-core: one comment in ci.yml said which service's suite
    had hung, and that service was the only one that could not adopt the revision.
    """
    if module is None or module == TEMPLATE_MODULE:
        return
    names = (module, module.replace("_", "-"))
    for relative in PLACEMENTS:
        incoming = template / relative
        if relative in owned.owned_placements or not incoming.is_file():
            continue
        lines = incoming.read_text(encoding="utf-8").splitlines()
        hits = [(n, name) for n, line in enumerate(lines, 1) for name in names if name in line]
        if hits:
            number, name = hits[0]
            die(
                f"the template at {revision[:9]} already names this service in {relative} "
                f"({name!r}, line {number}; {len(hits)} mention(s)). That file is renamed for "
                f"the service it lands in and check_kit_drift undoes the rename before it "
                f"hashes, so this text would read as an edit and drift could never be green. "
                f"Reword the template's file so it names no service. Nothing was changed"
            )


def lift_declared(owned: KitOwnership, package_root: Path, into: Path) -> dict[str, object]:
    """Copy each declared package out of the kit; return its VENDOR.json rows.

    A declared package that is not there, or has no row, is refused: a declaration that
    names nothing would otherwise rot silently, and the next reader would trust it.
    """
    if not owned.service_vendored:
        return {}
    missing = sorted(
        name for name in owned.service_vendored if not (package_root / VENDOR_DIR / name).is_dir()
    )
    if missing:
        die(
            f"service_vendored names {', '.join(missing)}, which is not a directory under "
            f"{VENDOR_DIR}/; vendor it first or remove it from the declaration"
        )
    vendor_json = package_root / VENDOR_JSON
    if not vendor_json.is_file():
        die(f"service_vendored is declared and {VENDOR_JSON} does not exist")
    try:
        rows = declared_rows(vendor_json.read_text(encoding="utf-8"), owned.service_vendored)
    except OwnershipRefused as exc:
        die(str(exc))
    for name in sorted(rows):
        shutil.copytree(
            package_root / VENDOR_DIR / name,
            into / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    return rows


def merge_template_rows(template: Path, rows: dict[str, object]) -> str | None:
    """The template's VENDOR.json plus the declared rows; None when nothing is declared,
    so an undeclared service keeps the template's bytes exactly."""
    if not rows:
        return None
    source = template / VENDOR_JSON
    if not source.is_file():
        die(f"the template has no {VENDOR_JSON} to merge the declared rows into")
    try:
        return merge_rows(source.read_text(encoding="utf-8"), rows)
    except OwnershipRefused as exc:
        die(str(exc))
    raise AssertionError("unreachable")


def restore_declared(
    names: list[str], lifted: Path, package_root: Path, merged: str | None
) -> None:
    for name in names:
        shutil.copytree(lifted / name, package_root / VENDOR_DIR / name)
        print(f"sync: kept     {VENDOR_DIR}/{name}/ (service_vendored)")
    if merged is not None:
        (package_root / VENDOR_JSON).write_text(merged, encoding="utf-8")


def sync(source: str, revision: str | None, into: str | None, bootstrap: bool) -> int:
    package_root = resolve_target_root(into)
    kit = package_root / KIT_DIRNAME

    if bootstrap:
        # A bootstrap is the FIRST adoption, so there is no manifest to read a
        # revision from and none to reconcile against. Both of the guards below
        # are refusals rather than conveniences: silently accepting either would
        # turn a bootstrap into an unchecked re-adoption.
        if kit.is_dir():
            die(
                f"{kit} already exists, so this is not a bootstrap. Run without "
                "--bootstrap to sync it, which reconciles against the existing manifest "
                "and refuses a dirty kit."
            )
        if not revision:
            die("--bootstrap needs --revision: there is no manifest yet to name one")
        target = revision
    else:
        manifest = current_manifest(package_root)
        target = revision or manifest.get("template_revision")
        if not target:
            die("no --revision given and the manifest names none")
    owned = ownership(package_root)
    # First, because the checks after it read through a link: git cannot list a path
    # beyond one, and a link that is tracked is clean.
    refuse_symlinks(package_root, owned)
    # A bootstrap writes the placements too, so an ignored placement already on disk is
    # as much at risk on first adoption as on a re-sync.
    refuse_untracked_kit(package_root, owned)
    refuse_dirty_kit(package_root)
    flavour = owned.flavour

    before = kit_file_map(package_root)
    with tempfile.TemporaryDirectory() as raw:
        template, commit = extract_template(source, target, Path(raw))
        # Everything that can refuse is resolved BEFORE the kit is deleted, so a refusal
        # leaves the service exactly as it was.
        strip = flavour_strip(template, target[:9]) if flavour != DEFAULT_FLAVOUR else None
        refuse_collisions(owned, template, target)
        refuse_a_placement_that_names_the_service(
            owned, template, service_module(package_root), target
        )
        lifted = Path(raw) / "lifted"
        rows = lift_declared(owned, package_root, lifted)
        merged = merge_template_rows(template, rows)
        if kit.is_dir():
            shutil.rmtree(kit)
        shutil.copytree(template / KIT_DIRNAME, kit)
        restore_declared(sorted(rows), lifted, package_root, merged)
        for relative in PLACEMENTS:
            if relative in owned.owned_placements:
                print(f"sync: kept     {relative} (owned_placements; this service owns it)")
                continue
            incoming = template / relative
            if not incoming.is_file():
                die(f"the template at {target} has no {relative}")
            destination = package_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            place(incoming, destination, service_module(package_root), strip)
    # The copied manifest names the revision the TEMPLATE recorded when it emitted it,
    # which cannot be the commit that holds that manifest. Record the commit these bytes
    # were actually read at instead, so the next default sync reads the same bytes; with
    # the template's own record, a second default sync went back to an older kit
    # (QA round 2).
    manifest_path = kit / MANIFEST_NAME
    recorded = json.loads(manifest_path.read_text())
    recorded["template_revision"] = commit
    manifest_path.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")

    after = kit_file_map(package_root)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in set(after) & set(before) if after[p] != before[p])

    for label, paths in (("added", added), ("changed", changed), ("removed", removed)):
        for path in paths:
            print(f"sync: {label:8s} {path}")
    print(
        f"sync: {len(added)} added, {len(changed)} changed, {len(removed)} removed; "
        f"kit now at template {commit[:9]}"
    )
    if bootstrap:
        # Say what the next step is. A bootstrapped kit carries the TEMPLATE's
        # manifest, whose template_revision is correct but whose file hashes were
        # taken in the template's tree -- check_kit_drift compares against them,
        # so the service is green only because the bytes are identical, which is
        # exactly what a fresh adoption guarantees and a later edit will not.
        print(
            f"sync: bootstrapped {package_root}. Commit this, then run the service's own "
            f"{KIT_DIRNAME}/sync.py for every later sync -- it is idempotent and reconciles "
            "against the manifest."
        )
    if not (added or changed or removed):
        # A no-op is a legitimate and common outcome -- it is what a re-run looks
        # like. Saying so beats printing nothing, which reads as a failed run.
        print("sync: the kit was already identical to that revision")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--emit-manifest",
        action="store_true",
        help="template mode: hash this tree's kit and write MANIFEST.json",
    )
    parser.add_argument(
        "--from",
        dest="source",
        default=DEFAULT_TEMPLATE_URL,
        help="a local template checkout or a git URL (default: the monorepo)",
    )
    parser.add_argument(
        "--revision",
        help="the template revision to sync to (default: the one the manifest names)",
    )
    parser.add_argument(
        "--into",
        help=(
            "the service checkout to sync INTO (default: the tree this script sits in). "
            "Use it to run the template's sync.py against another service."
        ),
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "first adoption: the target has no kit yet, so there is no manifest to "
            "reconcile against. Requires --revision, and refuses if a kit is present."
        ),
    )
    args = parser.parse_args(argv)
    if args.emit_manifest:
        if args.revision:
            die("--revision is meaningless with --emit-manifest; it records HEAD")
        if args.into or args.bootstrap:
            die("--into and --bootstrap are sync options; --emit-manifest records THIS tree")
        return emit_manifest()
    return sync(args.source, args.revision, args.into, args.bootstrap)


if __name__ == "__main__":
    raise SystemExit(main())
