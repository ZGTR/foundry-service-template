"""Shared discovery for the §9 gates.

Two things live here because every gate needs them and because a second copy is
how two gates come to disagree about what "the source tree" means.

`source_root()` finds `src/<package>` by GLOB, never by a hardcoded package
name: this file is copied verbatim into every emitted service, where the package
is called something else, and a name written here would make each gate's
population silently empty in the one place it matters most.

`fixture_root()` is the exclusion every gate needs. A gate's offending fixture
is, by construction, input the gate's own production scan would find -- that is
what makes it a working control -- so every gate must exclude the fixture tree
from its production population, and must ASSERT the exclusion in both
directions. `an earlier change` is that defect measured: a gate went red on a tree
nobody had changed, one commit after its fixture was committed, because
committing the fixture made the production glob match it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "FIXTURE_DIR_NAME",
    "FIXTURE_ROOT",
    "ImportStatement",
    "is_a_gate_fixture",
    "EXPECTED_MARKED_FIXTURE_FILES",
    "GATES_DIR",
    "KIT_ROOT",
    "KNOWN_SOURCE_LAYERS",
    "MARKER",
    "PACKAGE_ROOT",
    "SCRIPTS_DIR",
    "declared_extra_layers",
    "imports_in",
    "marker_count",
    "package_of",
    "python_sources",
    "service_data_file",
    "MIN_PACKAGES",
    "source_root",
    "source_roots",
    "unmarked_flagged_files",
]

#: §1/§4's closed set of top-level layers, in ONE place both gates read.
#: `check_layering.py`'s `ALLOWED` direction matrix and `check_folder_structure.py`'s
#: closed-set clause each need the same seven names, and two copies is how they come
#: to disagree about what "known" means -- exactly the class of defect this file's own
#: module docstring warns about for `source_root()`. `runtime` is here but NOT in
#: `check_folder_structure.py`'s `REQUIRED_SOURCE_LAYERS`: it is a KNOWN, optional
#: layer (§1 gives it a direction), not a required one -- agent-runtime's
#: `foundry_sandbox` and this kit's own fixtures carry it, and most services do not.
KNOWN_SOURCE_LAYERS = frozenset(
    {
        "adapters",
        "application",
        "composition",
        "domain",
        "ports",
        "runtime",
        "transport",
    }
)

#: Where the gates live: `service-kit/gates/`. The kit is template-owned and
#: byte-identical across services, so nothing here may name a service.
GATES_DIR = Path(__file__).resolve().parent
KIT_ROOT = GATES_DIR.parent
#: The service repository root. Two levels up from the gates now that they live
#: inside the kit, and this is NOT the same as "my parent" any more -- a gate
#: that kept `Path(__file__).parent.parent` after the kit move would resolve
#: `service-kit/` as the repository and find no `src/`, which reads as a service
#: with no source rather than as a gate that lost its bearings.
PACKAGE_ROOT = KIT_ROOT.parent
#: Service-owned data the gates read: ratchets, budgets and exemption lists.
#: They live OUTSIDE the kit on purpose -- a ratchet is a measurement of one
#: service, and a kit that carried it would either overwrite a service's
#: measurement at sync time or force every service to share one number.
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"
FIXTURE_ROOT = GATES_DIR / "gate_fixtures"

#: The directory name a gate fixture tree carries, WHEREVER it lives.
FIXTURE_DIR_NAME = "gate_fixtures"


def is_a_gate_fixture(path: Path) -> bool:
    """True when `path` sits under ANY directory named `gate_fixtures/`, not only the kit's.

    WHY BY NAME RATHER THAN BY ROOT. `FIXTURE_ROOT` is this kit's own fixture tree, so a
    clause written as `FIXTURE_ROOT not in p.parents` excludes the fixtures of whichever
    copy is running and nobody else's. Measured two ways on the same defect:

    * FROM THE PARITY HARNESS. The kit copy and a service's mature copy carry the identical
      clause and each imports `FIXTURE_ROOT` from its OWN `gate_common`, so in a tree
      holding both -- which the parity harness deliberately builds -- each counts the
      other's fixtures as production. `check_docker_shared` went exit 0 to exit 1, and
      deleting `service-kit/gates/gate_fixtures/` from that tree flipped both copies back.
    * FROM ADOPTION. Running the kit against `llm-gateway`, whose own mature gate
      copies live under `scripts/` with `scripts/gate_fixtures/{conforming,offending}/
      docker/Dockerfile`, the Dockerfile-count clause reported four Dockerfiles where one
      was real, one was the expected kit file, and two were that service's fixtures.

    Two independent routes to one cause, which is the strongest reason to fix it in the
    shared helper rather than in either caller.

    SCOPE, STATED HONESTLY. After adoption a service's mature copies are REPLACED by the
    kit's, which already excluded its own fixtures, so this is not a post-adoption bug. It
    matters during the transition window while both copies exist, and for any service that
    keeps a copy of its own. The clause is nonetheless correct independently of either
    situation: a contract that counts production artefacts should never count a fixture,
    whoever owns it.

    Only DIRECTORY components are consulted -- `path.parts[:-1]` -- so a file that happened
    to be named `gate_fixtures` is still production. Excluding it would be the vacuous-scan
    direction, where a gate quietly stops looking at something real.
    """
    return FIXTURE_DIR_NAME in path.parts[:-1]


def service_data_file(name: str) -> Path:
    """A service-owned data file a gate reads, by name, under `scripts/`."""
    return SCRIPTS_DIR / name


def is_file_or_unreadable(path: Path) -> bool:
    """`Path.is_file()`, answering False for a path this process may not stat.

    Python 3.12 and 3.13 RAISE `PermissionError` from `is_file()` when a parent
    directory is mode 0o000; 3.14 returns False. The kit's CI pins 3.12, so a gate
    that probes an unreadable package with a bare `is_file()` crashes with a
    traceback -- which exits 1, the same code a real finding uses -- on exactly the
    interpreter it is judged under.
    """
    try:
        return path.is_file()
    except PermissionError:
        return False


def source_roots(package_root: Path | None = None) -> list[Path]:
    """EVERY `src/<package>` in this repository, found by glob.

    WHY THIS EXISTS, MEASURED. `source_root()` below returns None whenever `src/`
    holds more than one package, and a gate handed None takes its NAMED SKIP
    branch. Eight of the fourteen gates this kit ships call it, and one service
    in this estate -- agent-runtime -- has THREE packages: the service,
    a framework-neutral kernel library and a second deployable. So on the day
    that service adopts the kit, eight of fourteen gates would skip entirely,
    printing a reason that reads like a missing source tree rather than like a
    gate that cannot count past one.

    That is the expensive shape rather than a loud one: exit 3 is this estate's
    honest "could not measure" and is deliberately not a pass, but a run that
    prints eight accounted-for skips and no findings satisfies any adoption
    checklist reading "gates ran, no findings". Coverage going dark inside an
    accounted-for skip.

    A package with no surface is excluded by the ABSENCE of the relevant
    directory, never by name, so a library package is skipped for the right
    reason and one that grows a surface tomorrow is covered by default.
    """
    root = (package_root or PACKAGE_ROOT) / "src"
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir() if p.is_dir() and is_file_or_unreadable(p / "__init__.py")
    )


#: A glob that matched nothing is a broken scan, not a clean repository. The
#: EXACT count is deliberately NOT asserted here: the number of packages a
#: service ships is a measurement of that service, and a kit carrying one would
#: either overwrite it at sync time or force every service to share it -- the
#: same reason ratchets and budgets live under `scripts/`. A service that wants
#: the stronger exact-count guard declares it in its own data file; this floor
#: is the universal half.
MIN_PACKAGES = 1


def source_root(package_root: Path | None = None) -> Path | None:
    """The ONE package a single-package gate would examine. None when ambiguous.

    Kept for gates that genuinely examine a single package, and deliberately
    still returning None for a multi-package repo: a gate that silently examined
    the first of three would report a clean estate for two it never opened.
    Prefer `source_roots()` in anything that scans a surface.
    """
    root = (package_root or PACKAGE_ROOT) / "src"
    if not root.is_dir():
        return None
    candidates = sorted(p for p in root.iterdir() if p.is_dir() and (p / "__init__.py").is_file())
    return candidates[0] if len(candidates) == 1 else None


#: The ONE service-owned registry of per-package policy. Read by four gates.
#: It lives here rather than in any one of them because a second copy of a
#: parser is how two gates come to disagree about the same file -- the reason
#: `source_root()` lives here too.
PACKAGE_ROLES_FILE = "package_roles.yaml"

#: The roles a package may declare. UNDECLARED IS `service`, the STRICT default:
#: a service is held to every layer and asked for exactly one config root, so
#: forgetting to declare buys more checking, never less.
VALID_ROLES = frozenset({"service", "library"})
DEFAULT_ROLE = "service"


def load_package_roles(path: Path | None = None) -> tuple[dict[str, dict], list[str]]:
    """Return ({package: declaration}, problems). A missing file is EMPTY.

    Parsed without PyYAML, which the gates cannot depend on: they run on a bare
    interpreter in every service. The subset is one level deeper than the other
    service data files -- a top-level package key, scalar fields at two spaces,
    and a `carve_out:` map at four -- and it is deliberately the SAME shape, so a
    service learns one file rather than several.

    A missing file is empty rather than an error: a service with nothing to
    declare should not have to carry one, and every default is the strict one.
    A MALFORMED file is different and is a problem, because it means somebody
    wrote a declaration the gates are not reading.
    """
    target = path or (SCRIPTS_DIR / PACKAGE_ROLES_FILE)
    entries: dict[str, dict] = {}
    problems: list[str] = []
    if not target.is_file():
        return entries, problems

    current: str | None = None
    in_carve_out = False
    for raw in target.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            if not stripped.endswith(":"):
                problems.append(f"{target.name}: {stripped!r} is not a package key")
                current = None
                continue
            current = stripped[:-1].strip("\"'")
            entries[current] = {"carve_out": {}}
            in_carve_out = False
            continue
        if current is None:
            problems.append(f"{target.name}: {stripped!r} appears before any package key")
            continue
        if indent == 2:
            in_carve_out = False
            if stripped == "carve_out:":
                in_carve_out = True
                continue
            if ":" not in stripped:
                problems.append(f"{target.name}: {current!r} has a field with no value")
                continue
            key, _, value = stripped.partition(":")
            entries[current][key.strip()] = value.strip().strip("\"'")
            continue
        if in_carve_out and indent >= 4:
            key, _, value = stripped.partition(":")
            entries[current]["carve_out"][key.strip().strip("\"'")] = value.strip().strip("\"'")
    return entries, problems


def role_of(package_name: str, entries: dict[str, dict]) -> str:
    """The declared role, or the STRICT default for an undeclared package."""
    return entries.get(package_name, {}).get("role", DEFAULT_ROLE)


def declared_extra_layers(package_name: str, entries: dict[str, dict]) -> frozenset[str]:
    """The service-reviewed top-level layers `KNOWN_SOURCE_LAYERS` does not name.

    Comma-separated, the SAME scalar-field shape every other `package_roles.yaml`
    field already uses (`role`, `reason`, `bead`) -- no parser change, because the
    generic indent-2 `key: value` branch in `load_package_roles` already reads it.
    An undeclared package returns the empty set, which is the strict default: a
    layer this file has never heard of is a finding until a human names it here.
    """
    raw = entries.get(package_name, {}).get("extra_layers", "")
    return frozenset(token.strip() for token in raw.split(",") if token.strip())


def validate_package_roles(entries: dict[str, dict], packages: list[Path]) -> list[str]:
    """Problems with the registry itself. FAILS CLOSED IN BOTH DIRECTIONS.

    An entry naming a package that is not under `src/`, or a carve-out naming a
    path that does not exist, is a hard error: a registry that cannot rot loudly
    rots quietly. So is an unrecognised role, a missing reason or bead, and a
    second `lifecycle_owner` -- two answers to "which package owns §6" is not an
    answer.
    """
    problems: list[str] = []
    by_name = {p.name: p for p in packages}
    owners = []
    for name, entry in sorted(entries.items()):
        if name not in by_name:
            problems.append(
                f"{PACKAGE_ROLES_FILE}: {name!r} is not a package under src/ "
                f"({sorted(by_name)}); the entry describes a tree that no longer exists"
            )
            continue
        role = entry.get("role", DEFAULT_ROLE)
        if role not in VALID_ROLES:
            problems.append(
                f"{PACKAGE_ROLES_FILE}: {name!r} declares role {role!r}, not one of "
                f"{sorted(VALID_ROLES)}"
            )
        for field in ("reason", "bead"):
            if not entry.get(field):
                problems.append(f"{PACKAGE_ROLES_FILE}: {name!r} declares no {field}")
        declared = declared_extra_layers(name, entries)
        if "extra_layers" in entry and not declared:
            problems.append(
                f"{PACKAGE_ROLES_FILE}: {name!r} declares extra_layers with no layer named; "
                "an empty declaration is not an exemption, it is a hole"
            )
        for layer in declared:
            if layer in KNOWN_SOURCE_LAYERS:
                problems.append(
                    f"{PACKAGE_ROLES_FILE}: {name!r} declares extra_layers: {layer!r}, which "
                    f"is already a known layer ({sorted(KNOWN_SOURCE_LAYERS)}); the "
                    "declaration describes nothing this gate did not already know"
                )
            elif not (by_name[name] / layer).is_dir():
                problems.append(
                    f"{PACKAGE_ROLES_FILE}: {name!r} declares extra_layers: {layer!r}, which "
                    f"does not exist under src/{name}/; the declaration describes a directory "
                    "that has moved or gone"
                )
        if str(entry.get("lifecycle_owner", "")).lower() == "true":
            owners.append(name)
        for relative, module in sorted(entry.get("carve_out", {}).items()):
            if not (by_name[name] / relative).is_file():
                problems.append(
                    f"{PACKAGE_ROLES_FILE}: {name!r} carves out {relative!r}, which does not "
                    f"exist; the exemption describes a file that has moved or gone"
                )
            if not module:
                problems.append(
                    f"{PACKAGE_ROLES_FILE}: {name!r} carves out {relative!r} with no module "
                    f"named; an unbounded carve-out is not an exemption, it is a hole"
                )
    if len(owners) > 1:
        problems.append(
            f"{PACKAGE_ROLES_FILE}: {sorted(owners)} each declare lifecycle_owner; §6 has "
            "exactly one owner, and a file naming two names none"
        )
    return problems


def python_sources(root: Path, *, exclude_fixtures: bool = True) -> list[Path]:
    """Every .py file under `root`; caches always excluded, fixtures by default.

    `exclude_fixtures=False` is how a gate runs its PRODUCTION analyser over a
    fixture arm. Without it the control would enumerate zero files and pass by
    emptiness -- the arm would be green for the one reason that proves nothing,
    which is the "control that tests nothing" shape at one remove.
    """
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
        and not (exclude_fixtures and FIXTURE_ROOT in path.parents)
    )


class ImportStatement(NamedTuple):
    """One module an import statement names, as an ABSOLUTE dotted name.

    `module` is None for a relative import that climbs above the import root, which
    Python refuses at import time ("attempted relative import beyond top-level
    package"); a caller decides whether that is a finding. `names` pairs each imported
    name with the local name it binds: `from m import a as b` is `(("a", "b"),)`, and
    `import a.b as c` is `(("a.b", "c"),)`, and `import a.b` is `(("a.b", "a"),)`, because
    that statement binds `a`.
    """

    module: str | None
    lineno: int
    is_from: bool
    names: tuple[tuple[str, str], ...]


def package_of(path: Path, import_root: Path) -> str:
    """The dotted package a module at `path` belongs to: Python's `__package__`.

    `import_root` is the directory on `sys.path` (`src/` for a service package). A
    module and its directory's `__init__.py` share one package, which is the anchor
    every relative import in either file is resolved against.
    """
    return ".".join(path.parent.relative_to(import_root).parts)


def imports_in(tree: ast.AST, package: str) -> list[ImportStatement]:
    """Every import in `tree`, relative ones resolved against `package`.

    THE ONE RESOLVER. `check_layering`, `check_orm_standard`,
    `check_pydantic_boundaries` and `check_test_first` each walked the tree themselves
    and kept an `ImportFrom` only when `node.level == 0`, so `from ..transport import
    http` was invisible to the direction rule: planted in a service's `application/`,
    the absolute spelling was a finding and the relative one printed PASS. Resolving
    here, once, is what makes "the gates agree about what a file imports" true.
    """
    found: list[ImportStatement] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                # `import a.b.c` binds `a`; the module is still the full dotted name.
                ImportStatement(
                    alias.name,
                    node.lineno,
                    False,
                    ((alias.name, alias.asname or alias.name.split(".")[0]),),
                )
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            if node.level:
                # Level 1 is `package` itself; each further dot drops one component.
                anchor = package.split(".") if package else []
                kept = len(anchor) - node.level + 1
                tail = [node.module] if node.module else []
                module = ".".join([*anchor[:kept], *tail]) if kept > 0 else None
            names = tuple((alias.name, alias.asname or alias.name) for alias in node.names)
            found.append(ImportStatement(module, node.lineno, True, names))
    return found


#: The token every deliberate violation in `gate_fixtures/offending/` carries.
#: A gate asserts TWO things about its offending arm: that it produced the
#: hand-written number of findings, and that every file it flagged carries this
#: token. Stripping the token therefore turns the gate RED -- which is the
#: property "neutralise the marker and watch it go red" is actually testing.
#: Without the second assertion the token would be a comment nobody reads, and
#: the red-arm proof would be a proof about deleting code rather than about the
#: gate consuming its fixture.
MARKER = "GATE_MUST_FLAG_THIS"

#: How many files in `gate_fixtures/offending/` carry a PLANTED violation.
#: HAND-WRITTEN, and shared because it is a property of the fixture TREE rather
#: than of any one gate. Every gate asserts it, so stripping the token from any
#: planted file turns all six red at once rather than silently disarming one.
#: Growing it is a deliberate fixture change, not a normal event, so an exact
#: comparison is right here where it would be wrong over a service roster.
#: 9 -> 10 when `check_class_density.py` planted
#: `offending/pkg/domain/model/doodad_variants.py`. Growing this number is the
#: deliberate fixture change the paragraph above allows; it is not a ratchet and
#: it moves only alongside a new planted violation.
#: 10 -> 11 when `check_mvc_separation.py` planted
#: `offending/pkg/transport/http/controllers/widgets_controller.py`, the fixture
#: for its model-link clause -- the clause that had no fixture arm and could be
#: regressed to a bare substring while the gate stayed green.
#: 11 -> 12 when `check_layering.py` planted `offending/pkg/ops/probe.py`
#:: an undeclared top-level layer that imports straight
#: into `transport` and `adapters`, the fixture for its new rule 5 -- the clause
#: that had no fixture arm and let an eighth directory escape both this gate and
#: `check_folder_structure.py`'s closed set silently.
#: 12 -> 13 when `check_layering.py` planted
#: `offending/pkg/application/pricing_policy.py` under the owner's ruling
#: ruling. `application` may now import `pydantic`; that file is the negative half,
#: asserting `fastapi`, `sqlalchemy` and `pydantic_ai` are still refused there by name.
#: 13 -> 15 when `check_untimed_waits.py` planted two files:
#: `offending/tests/test_waits_forever.py`, twenty-six waits with no deadline or with
#: one nobody checks, and `offending/tests/support/takes_a_lock_made_elsewhere.py`,
#: which takes a lock the first file makes.
EXPECTED_MARKED_FIXTURE_FILES = 15


def marker_count(fixture_name: str = "offending") -> int:
    """How many fixture files carry the marker. Zero means the control is gone.

    EVERY file, not just `*.py`. Two of the planted violations are shell and
    Dockerfile, and a `*.py` glob would have excluded exactly the fixtures
    `check_docker_shared.py` depends on -- the count would then have been a
    control for four gates and decoration for a fifth, with nothing saying so.
    """
    root = FIXTURE_ROOT / fixture_name
    total = 0
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            if MARKER in path.read_text(encoding="utf-8", errors="ignore"):
                total += 1
        except OSError:
            continue
    return total


def unmarked_flagged_files(fixture_pkg: Path, findings: list[str]) -> list[str]:
    """Flagged fixture files that carry no marker.

    A finding's first colon-delimited field is its path, relative to the fixture
    package. A flagged file with no marker means the gate found something the
    fixture did not deliberately plant -- the fixture has drifted, and the
    hand-written count above it is measuring the wrong thing.
    """
    unmarked = []
    for item in findings:
        relative = item.split(":", 1)[0].strip()
        path = fixture_pkg / relative
        if not path.is_file() or MARKER not in path.read_text():
            unmarked.append(relative)
    return sorted(set(unmarked))
