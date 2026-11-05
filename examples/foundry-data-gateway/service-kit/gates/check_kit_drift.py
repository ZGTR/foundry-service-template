"""§15: the kit in this repository is the kit the template published.

`service-kit/` is template-owned. Every file in it is byte-identical in every
service, which is the only thing that makes "run the gate, get the same answer"
true across the estate. This gate is what turns that sentence into something a
commit can fail.

It reads `service-kit/MANIFEST.json` -- `template_revision`, a `files` map of
path to sha256, and `placements`, the files the kit owns but that must live at a
fixed path outside it (`.github/workflows/ci.yml`, which GitHub reads only from
`.github/workflows/`) -- and compares it against the tree.

THREE OUTCOMES, and the third is the one that is usually got wrong:

* 0  every listed file is present with its recorded hash, and nothing unlisted
     sits inside the kit.
* 1  a FINDING: a hash moved, or a file inside the kit is not in the manifest.
     Someone edited the kit here, or added to it here; either way this service
     has forked from the template.
* 2  the gate COULD NOT MEASURE: the manifest is missing or unparseable, it
     lists a file that does not exist, or its file map is empty. A missing input
     is not a clean tree. Reporting it as a pass is the vacuous-scan defect, and
     reporting it as a finding would send an author looking for an edit nobody
     made.

WHAT THIS GATE CANNOT DO, stated because the hole is structural rather than a
bug to fix later: an author who edits a kit file AND re-runs
`sync.py --emit-manifest` gets a green tree, because both sides of the
comparison then come from the same edited tree. Nothing inside a service can
close that -- the missing party is the template. The estate gate
`check_service_kit_parity.py` (Plan B, B10) compares each service's manifest
file map against the template submodule's own manifest at the declared
`template_revision`, hash for hash. Until that exists, this gate catches the
accident (a hand edit) and not the deliberate re-hash.

SERVICE-OWNED BYTES. A service may declare, in its pyproject.toml's
`[tool.foundry.kit]` (read by `service-kit/kit_ownership.py`, the one reader), bytes
it owns inside or beside the kit. The manifest is still the TEMPLATE's, unedited:

* `service_vendored` packages under `service-kit/vendor/<name>/` are excluded from the
  "not in the manifest" check, for declared names only, and their rows are stripped
  from VENDOR.json before its hash is compared -- so every template row stays pinned.
  `check_vendor_digests.py` still recomputes each declared package's digest. A
  declared name the template also ships, or one with no directory or no row, is a
  finding.
* an `owned_placements` entry skips the placement digest, and must instead run every
  `service-kit/gates/check_*.py` (found by glob over the kit) in a `run:` step that is
  not commented out. A missing gate is a finding that names it.

Exit 0 clean, 1 on a finding, 2 if the gate could not measure.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))

from gate_common import KIT_ROOT, PACKAGE_ROOT, source_root  # noqa: E402
from kit_ownership import (  # noqa: E402
    DEFAULT_FLAVOUR,
    KIT_DOTTED,
    KIT_TABLE,
    VENDOR_DIR,
    VENDOR_JSON,
    KitOwnership,
    OwnershipRefused,
    canonical_json,
    declared_rows,
    read_ownership,
    strip_declared_rows,
)

MANIFEST_PATH = KIT_ROOT / "MANIFEST.json"

#: The manifest cannot record its own hash, and `__pycache__` is build output
#: that no service should be asked to keep identical.
EXCLUDED_FROM_KIT = ("MANIFEST.json",)

#: HAND-WRITTEN floors. Both are literals rather than counts read from the tree:
#: an expectation derived from the artifact under test holds for every artifact
#: including an empty one, which is the defect
#: `check_tautological_expectation.py` refuses in the monorepo. They are
#: deliberately loose -- they catch a manifest that lost its contents or a kit
#: that failed to copy, not a kit that legitimately grew or shrank by a file.
MIN_MANIFEST_FILES = 40
MIN_PLACEMENTS = 1

#: A kit gate named in a workflow command. The same shape
#: `tests/shape/test_ci_runs_every_kit_gate.py` reads in the template's own ci.yml.
GATE_INVOCATION = re.compile(r"service-kit/gates/(check_\w+\.py)")
RUN_KEY = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:\s*(?P<value>.*)$")
BLOCK_SCALAR = re.compile(r"^[|>][-+]?\s*(#.*)?$")


def refuse(message: str) -> int:
    print(f"check-kit-drift: REFUSE -- {message}", file=sys.stderr)
    return 2


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


TEMPLATE_MODULE = "foundry_service_template"
TEMPLATE_NAME = "foundry-service-template"


def placement_digest(path: Path, package_root: Path) -> str:
    """A placement's hash, with this service's own name normalised back.

    WHY THIS EXISTS. A placement is a kit-owned file that must live at a fixed
    path OUTSIDE the kit -- today only `.github/workflows/ci.yml`, which GitHub
    reads from `.github/workflows/` and nowhere else. That file NAMES the
    service, by design and by necessity: its `name:` key, its image tag and its
    ECR reference are all the service's own, and `scripts/new_service.sh`
    renames them on emission.

    So pinning a placement to the TEMPLATE's bytes pinned the one thing the
    scaffolder is required to change, and every emitted service failed this gate
    on arrival for a rename it was supposed to have. Measured 2026-09-18: a
    pristine scaffold reported 7 findings before anything was edited, identically
    for two different service names -- which is what proved the fault was the
    scaffolder's and this gate's rather than any service's.

    Normalising the name back keeps the drift detection intact: a real edit to
    the workflow still moves the hash, because only the service's own two names
    are substituted and nothing else is touched. What it no longer reports is the
    rename itself.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    local = source_root(package_root)
    if local is not None and local.name != TEMPLATE_MODULE:
        text = text.replace(local.name, TEMPLATE_MODULE)
        text = text.replace(local.name.replace("_", "-"), TEMPLATE_NAME)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ownership_of(package_root: Path) -> tuple[KitOwnership | None, str | None]:
    """The service's declaration, or the reason it cannot be read (unmeasurable).

    WHY A FLAVOUR IS PART OF THE COMPARISON. A comms emission
    has `scripts/apply_comms_flavour.py`'s marked blocks removed from its ci.yml, so
    the template's own placement digest can never match it. The manifest records a
    second digest per flavour, and a service picks the one it declares. A `comms_*`
    package must declare one: judged as default it would pass while holding the SQS
    path the comms flavour removes. `kit_ownership.py` owns
    those rules; this gate only reports its refusal.
    """
    local = source_root(package_root)
    try:
        return read_ownership(package_root, module=local.name if local else None), None
    except OwnershipRefused as exc:
        return None, str(exc)


def expected_placements(manifest: dict, flavour: str) -> dict[str, str] | None:
    """The placement digests this flavour must match; None when the manifest has none."""
    if flavour == DEFAULT_FLAVOUR:
        return manifest.get("placements", {})
    return manifest.get("placements_by_flavour", {}).get(flavour)


def kit_files(kit_root: Path) -> list[Path]:
    """Every file physically inside the kit, caches and the manifest aside."""
    return sorted(
        path
        for path in kit_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.relative_to(kit_root).as_posix() not in EXCLUDED_FROM_KIT
    )


def template_packages(recorded: dict[str, str]) -> set[str]:
    """Package directories the template ships under `service-kit/vendor/`."""
    prefix = f"{VENDOR_DIR}/"
    return {
        relative[len(prefix):].split("/")[0]
        for relative in recorded
        if relative.startswith(prefix) and "/" in relative[len(prefix):]
    }


def run_commands(text: str) -> list[str]:
    """Every line of every `run:` value in a workflow, comments excluded.

    Read as structure rather than by token: a single-line `run: <cmd>`, and each
    indented line of a `run: |` or `run: >` block. A line that is only a comment is
    dropped, so a gate invocation that was commented out does not count as run.
    """
    commands: list[str] = []
    block_indent: int | None = None
    for line in text.splitlines():
        if block_indent is not None:
            indent = len(line) - len(line.lstrip())
            if not line.strip() or indent > block_indent:
                if line.strip() and not line.strip().startswith("#"):
                    commands.append(line.strip())
                continue
            block_indent = None
        match = RUN_KEY.match(line)
        if match is None or line.strip().startswith("#"):
            continue
        value = match.group("value").strip()
        if BLOCK_SCALAR.match(value):
            block_indent = len(match.group("indent"))
        elif value and not value.startswith("#"):
            commands.append(value)
    return commands


def missing_gates(placement: Path, kit_root: Path) -> tuple[list[str], list[str]]:
    """(kit gates the owned placement never runs, every kit gate found by glob)."""
    gates = sorted(path.name for path in (kit_root / "gates").glob("check_*.py"))
    invoked = {
        match.group(1)
        for command in run_commands(placement.read_text(encoding="utf-8", errors="replace"))
        for match in GATE_INVOCATION.finditer(command)
    }
    return [gate for gate in gates if gate not in invoked], gates


def compare(
    manifest: dict, package_root: Path, owned: KitOwnership | None = None
) -> tuple[list[str], list[str]]:
    """(findings, unmeasurable). Findings are drift; unmeasurable is exit 2.

    Returns both rather than raising on the first problem, because a run that
    stops at the first mismatch cannot say how far the fork goes -- and the
    first thing an author wants to know is whether one file drifted or forty.
    """
    owned = owned or KitOwnership(DEFAULT_FLAVOUR, frozenset(), frozenset())
    findings: list[str] = []
    unmeasurable: list[str] = []
    kit_root = package_root / "service-kit"

    recorded: dict[str, str] = manifest.get("files", {})
    shipped = template_packages(recorded)
    declared = owned.service_vendored
    for name in sorted(declared & shipped):
        findings.append(
            f"service_vendored names {name}, which the template ships under {VENDOR_DIR}/; "
            f"a declared package may not shadow a template package"
        )
    own_packages = declared - shipped

    for relative, expected in sorted(recorded.items()):
        path = package_root / relative
        if not path.is_file():
            unmeasurable.append(
                f"{relative} is in the manifest and not on disk; the kit is incomplete, "
                f"which is a failed sync rather than a local edit -- re-run "
                f"`python service-kit/sync.py`"
            )
            continue
        stripped = relative == VENDOR_JSON and bool(declared)
        if stripped:
            try:
                text = strip_declared_rows(path.read_text(encoding="utf-8"), declared)
            except OwnershipRefused as exc:
                unmeasurable.append(str(exc))
                continue
            actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        else:
            actual = sha256_of(path)
        if actual != expected:
            findings.append(
                f"{relative}: sha256 {actual[:12]} != manifest {expected[:12]}"
                f"{' (with the declared rows stripped)' if stripped else ''}. The kit is "
                f"template-owned; fix it in the template (platform/packages/"
                f"foundry-service-template in the monorepo) and sync, rather "
                f"than editing it here"
            )

    for name in sorted(own_packages):
        if not (package_root / VENDOR_DIR / name).is_dir():
            findings.append(
                f"service_vendored names {name} and {VENDOR_DIR}/{name}/ does not exist; "
                f"vendor it, or remove it from the declaration"
            )
    vendor_json = package_root / VENDOR_JSON
    if own_packages and vendor_json.is_file():
        try:
            declared_rows(vendor_json.read_text(encoding="utf-8"), frozenset(own_packages))
        except OwnershipRefused as exc:
            findings.append(str(exc))

    owned_dirs = tuple(f"{VENDOR_DIR}/{name}/" for name in sorted(own_packages))
    for path in kit_files(kit_root):
        relative = path.relative_to(package_root).as_posix()
        if relative in recorded or (owned_dirs and relative.startswith(owned_dirs)):
            continue
        findings.append(
            f"{relative} is inside the kit and not in the manifest. A file added here is a "
            f"fork; add it in the template, declare its package in {KIT_TABLE} "
            f"service_vendored, or put it outside service-kit/ where this service owns it"
        )

    placements = expected_placements(manifest, owned.flavour)
    if placements is None:
        unmeasurable.append(
            f"this service declares the {owned.flavour!r} flavour and the manifest records no "
            f"placement digest for it; the kit predates flavoured placements -- re-run "
            f"`python service-kit/sync.py --revision <template revision>`"
        )
        placements = {}
    for relative, expected in sorted(placements.items()):
        path = package_root / relative
        if not path.is_file():
            unmeasurable.append(f"placement {relative} is in the manifest and not on disk")
            continue
        if relative in owned.owned_placements:
            missing, gates = missing_gates(path, kit_root)
            if not gates:
                unmeasurable.append(
                    f"{relative} is owned and the glob service-kit/gates/check_*.py found no "
                    f"gate to require of it; the kit is incomplete"
                )
            for gate in missing:
                findings.append(
                    f"{relative} (owned placement) never runs service-kit/gates/{gate} in a "
                    f"`run:` step; an owned workflow must still run every kit gate"
                )
            continue
        # NOT sha256_of: a placement is renamed by the scaffolder BY DESIGN, so
        # comparing its raw bytes pins the one file new_service.sh must change.
        actual = placement_digest(path, package_root)
        if actual != expected:
            findings.append(
                f"{relative} (placement): sha256 {actual[:12]} != manifest {expected[:12]}"
            )

    return findings, unmeasurable


def measure(manifest: dict, package_root: Path) -> tuple[list[str], list[str], KitOwnership | None]:
    """Read the declaration, then compare; a declaration that cannot be read is exit 2."""
    owned, reason = ownership_of(package_root)
    if owned is None:
        return [], [f"{KIT_TABLE}: {reason}"], None
    findings, unmeasurable = compare(manifest, package_root, owned)
    return findings, unmeasurable, owned


def _control_cases() -> list[tuple[str, int, str]]:
    """(name, expected_exit, why) for the inline control below.

    INLINE rather than a tracked fixture tree, and the reason is this gate's own
    subject. Its input is a manifest PLUS the tree that manifest describes, and a
    tracked fixture holding a second `service-kit/` would be found by the
    production scan above -- the leak `check_gate_fixture_isolation.py` exists to
    catch, arriving in the one gate whose population is "every file in the kit".
    So the control builds whole temporary services instead, which is also the
    medium this gate's verdict reads: the filesystem.
    """
    return [
        ("untouched", 0, "a kit that matches its manifest is clean"),
        ("one-byte-edit", 1, "a single changed byte in a kit file is a fork"),
        ("unlisted-file", 1, "a file added inside the kit is a fork"),
        ("manifest-lists-a-ghost", 2, "a manifest naming a missing file cannot be measured"),
        ("placement-edited", 1, "a placement is kit-owned wherever it has to live"),
        ("comms-flavoured", 0, "a comms service matches its manifest's comms placement digest"),
        ("comms-kept-text-edited", 1, "an edit to the text the comms flavour keeps is a fork"),
        ("comms-digest-missing", 2, "a flavour the manifest has no digest for is unmeasurable"),
        ("unknown-flavour", 2, "a flavour the kit does not know cannot be measured"),
        ("comms-undeclared", 2, "a comms_* package that declares no flavour cannot be measured"),
        ("comms-dotted-key", 0, "a dotted-key declaration is the same declaration"),
        ("comms-opted-out", 0, "a comms_* package may declare the default flavour"),
        ("declared-vendored", 0, "a declared package and its row beside the template's are clean"),
        ("undeclared-vendored", 1, "the same package, undeclared, is a fork"),
        ("declared-template-row-edited", 1, "stripping the declared rows still pins every "
                                            "template row"),
        ("declared-shadows-template", 1, "a declared name the template ships is a finding"),
        ("declared-but-absent", 1, "a declared package with no directory and no row is a "
                                   "finding"),
        ("owned-placement-runs-every-gate", 0, "an owned placement may differ if it runs "
                                               "every kit gate"),
        ("owned-placement-block-run", 0, "a gate run inside a `run: |` block counts"),
        ("owned-placement-drops-a-gate", 1, "an owned placement that skips a kit gate is a "
                                            "finding"),
        ("owned-placement-commented-gate", 1, "a commented-out invocation is not a run"),
        ("unknown-key", 2, "an unknown key in the declaration cannot be measured"),
        ("bad-package-name", 2, "a declared name Python cannot import cannot be measured"),
        ("placement-not-ownable", 2, "only a kit placement can be owned"),
    ]


#: The finding a red case must carry. An exit code alone cannot tell which clause spoke:
#: a declared name the template ships also moves VENDOR.json's stripped hash, so with the
#: shadow clause deleted that case still exited 1 and the control stayed quiet.
_CONTROL_FINDINGS = {
    "undeclared-vendored": "inside the kit and not in the manifest",
    "declared-shadows-template": "may not shadow a template package",
    "declared-but-absent": "does not exist",
    "owned-placement-drops-a-gate": "never runs service-kit/gates/",
    "owned-placement-commented-gate": "never runs service-kit/gates/",
}

#: The owned-placement control workflows: one per shape the matcher must read.
_OWNED_WORKFLOWS = {
    "owned-placement-runs-every-gate": (
        "name: mine\njobs:\n  gates:\n    steps:\n"
        "      - run: python service-kit/gates/check_example.py\n"
    ),
    "owned-placement-block-run": (
        "name: mine\njobs:\n  gates:\n    steps:\n      - run: |\n"
        "          echo first\n          python service-kit/gates/check_example.py\n"
    ),
    "owned-placement-drops-a-gate": (
        "name: mine\njobs:\n  gates:\n    steps:\n      - run: echo no gates here\n"
    ),
    "owned-placement-commented-gate": (
        "name: mine\njobs:\n  gates:\n    steps:\n"
        "      # - run: python service-kit/gates/check_example.py\n"
        "      - run: |\n          # python service-kit/gates/check_example.py\n"
        "          echo skipped\n"
    ),
}

#: The declarations each ownership control case writes, before any flavour case.
_DECLARATIONS = {
    "declared-vendored": 'service_vendored = ["svc_pkg"]\n',
    "declared-template-row-edited": 'service_vendored = ["svc_pkg"]\n',
    "declared-shadows-template": 'service_vendored = ["tpl_pkg"]\n',
    "declared-but-absent": 'service_vendored = ["svc_pkg"]\n',
    "unknown-key": 'service_vendor = ["svc_pkg"]\n',
    "bad-package-name": 'service_vendored = ["svc-pkg"]\n',
    "placement-not-ownable": 'owned_placements = ["Dockerfile"]\n',
    **{case: 'owned_placements = [".github/workflows/ci.yml"]\n' for case in _OWNED_WORKFLOWS},
}


def _build_control(case: str, root: Path) -> None:
    """Write a whole miniature service for one control case."""
    kit = root / "service-kit" / "gates"
    kit.mkdir(parents=True)
    (kit / "check_example.py").write_text("print('gate')\n")
    (root / "service-kit" / "docker").mkdir()
    (root / "service-kit" / "docker" / "Dockerfile").write_text("FROM scratch\n")
    vendor = root / VENDOR_DIR
    (vendor / "tpl_pkg").mkdir(parents=True)
    (vendor / "tpl_pkg" / "__init__.py").write_text("")
    template_rows = {"tpl_pkg": {"content_sha256": "a" * 64}}
    (root / VENDOR_JSON).write_text(canonical_json({"note": "n", "packages": template_rows}))
    workflow = root / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text("name: ci\n")

    files = {
        "service-kit/gates/check_example.py": sha256_of(kit / "check_example.py"),
        "service-kit/docker/Dockerfile": sha256_of(root / "service-kit/docker/Dockerfile"),
        f"{VENDOR_DIR}/tpl_pkg/__init__.py": sha256_of(vendor / "tpl_pkg" / "__init__.py"),
        VENDOR_JSON: sha256_of(root / VENDOR_JSON),
    }
    placements = {".github/workflows/ci.yml": sha256_of(workflow / "ci.yml")}
    by_flavour: dict[str, dict[str, str]] = {}

    if case in _DECLARATIONS:
        (root / "pyproject.toml").write_text(f"{KIT_TABLE}\n{_DECLARATIONS[case]}")
    if case in ("declared-vendored", "undeclared-vendored", "declared-template-row-edited"):
        (vendor / "svc_pkg").mkdir()
        (vendor / "svc_pkg" / "__init__.py").write_text("VALUE = 1\n")
        rows = {**template_rows, "svc_pkg": {"content_sha256": "b" * 64}}
        if case == "declared-template-row-edited":
            rows["tpl_pkg"] = {"content_sha256": "c" * 64}
        (root / VENDOR_JSON).write_text(canonical_json({"note": "n", "packages": rows}))
    if case in _OWNED_WORKFLOWS:
        (workflow / "ci.yml").write_text(_OWNED_WORKFLOWS[case])

    if case in ("comms-undeclared", "comms-opted-out"):
        package = root / "src" / "comms_probe"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        if case == "comms-opted-out":
            opt_out = f'{KIT_TABLE}\nflavour = "{DEFAULT_FLAVOUR}"\n'
            (root / "pyproject.toml").write_text(opt_out)
    elif case.startswith("comms-") or case == "unknown-flavour":
        declared = "comms" if case != "unknown-flavour" else "sms"
        declaration = (
            f'{KIT_DOTTED}.flavour = "{declared}"\n'
            if case == "comms-dotted-key"
            else f'{KIT_TABLE}\nflavour = "{declared}"\n'
        )
        (root / "pyproject.toml").write_text(declaration)
        # The service holds the stripped text; the default digest is of the
        # unstripped template text, which this service can never match.
        (workflow / "ci.yml").write_text("name: ci\n# kept\n")
        if case != "comms-digest-missing":
            by_flavour["comms"] = {".github/workflows/ci.yml": sha256_of(workflow / "ci.yml")}

    if case == "one-byte-edit":
        (kit / "check_example.py").write_text("print('gatE')\n")
    elif case == "unlisted-file":
        (kit / "check_smuggled.py").write_text("print('mine')\n")
    elif case == "manifest-lists-a-ghost":
        files["service-kit/gates/check_absent.py"] = "0" * 64
    elif case == "placement-edited":
        (workflow / "ci.yml").write_text("name: ci-local\n")
    elif case == "comms-kept-text-edited":
        (workflow / "ci.yml").write_text("name: ci\n# kepT\n")

    manifest = {"template_revision": "0" * 40, "files": files, "placements": placements}
    if by_flavour:
        manifest["placements_by_flavour"] = by_flavour
    (root / "service-kit" / "MANIFEST.json").write_text(json.dumps(manifest))


def _self_test() -> int | None:
    """Run every control case. None on success, an exit code on failure.

    A gate that has never been shown to FIRE, and never been shown to stay
    QUIET, has proved half of nothing in each direction.
    """
    for case, expected, why in _control_cases():
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _build_control(case, root)
            manifest = json.loads((root / "service-kit" / "MANIFEST.json").read_text())
            findings, unmeasurable, _ = measure(manifest, root)
            actual = 2 if unmeasurable else (1 if findings else 0)
            if actual != expected:
                return refuse(
                    f"inline control '{case}' returned {actual}, expected {expected} ({why}). "
                    f"findings={findings} unmeasurable={unmeasurable}"
                )
            needle = _CONTROL_FINDINGS.get(case)
            if needle is not None and not any(needle in finding for finding in findings):
                return refuse(
                    f"inline control '{case}' went red without its own finding ({needle!r}), "
                    f"so another clause answered for it. findings={findings}"
                )
    return None


def main() -> int:
    failed = _self_test()
    if failed is not None:
        return failed

    if not MANIFEST_PATH.is_file():
        return refuse(
            f"{MANIFEST_PATH} does not exist. Every service carries one; generate it in the "
            f"template with `python service-kit/sync.py --emit-manifest`"
        )
    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
    except json.JSONDecodeError as exc:
        return refuse(f"{MANIFEST_PATH} is not valid JSON: {exc}")

    revision = manifest.get("template_revision")
    if not isinstance(revision, str) or len(revision) < 7:
        return refuse("the manifest has no usable template_revision")

    recorded = manifest.get("files", {})
    if len(recorded) < MIN_MANIFEST_FILES:
        return refuse(
            f"the manifest lists {len(recorded)} file(s), fewer than the floor of "
            f"{MIN_MANIFEST_FILES}. A manifest that lost its contents would otherwise report a "
            f"clean kit by emptiness"
        )
    if len(manifest.get("placements", {})) < MIN_PLACEMENTS:
        return refuse(
            f"the manifest lists {len(manifest.get('placements', {}))} placement(s), fewer than "
            f"the floor of {MIN_PLACEMENTS}"
        )

    findings, unmeasurable, owned = measure(manifest, PACKAGE_ROOT)
    arms = len(recorded) + len(manifest.get("placements", {})) + len(kit_files(KIT_ROOT))
    if unmeasurable:
        for item in unmeasurable:
            print(f"  {item}", file=sys.stderr)
        return refuse(
            f"{len(unmeasurable)} manifest entr(ies) could not be measured; "
            f"{arms} arm(s) attempted"
        )
    if findings:
        print(f"check-kit-drift: FAIL {len(findings)} finding(s) against template {revision[:9]}:")
        for item in findings:
            print(f"  {item}")
        return 1

    cases = _control_cases()
    print(
        f"check-kit-drift: PASS kit matches template {revision[:9]} ({owned.flavour} flavour), "
        f"{len(owned.service_vendored)} service-vendored package(s) and "
        f"{len(owned.owned_placements)} owned placement(s) declared; "
        f"{len(recorded)} manifest file(s) + {len(manifest.get('placements', {}))} placement(s) "
        f"+ {len(kit_files(KIT_ROOT))} file(s) on disk = {arms} arms; {arms} executed, 0 skipped, "
        f"0 unaccounted. Inline control: {len(cases)} cases, "
        f"{sum(1 for _, code, _ in cases if code == 0)} quiet and "
        f"{sum(1 for _, code, _ in cases if code != 0)} flagged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
