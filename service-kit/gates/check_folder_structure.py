"""§5: one folder shape, in every service, gated.

The estate had nine services and roughly nine folder layouts. That is not a
tidiness complaint: it is why a person who knows one service cannot find
anything in the next, why a shared gate has to guess where to look, and why
`sync.py` could not have a fixed idea of what it owns. This gate fixes the
shape so a service that drifts fails a commit rather than a reader's
expectations.

WHAT IT ASSERTS, and each clause is here because an absence of it was measured
somewhere in this estate:

1. Every REQUIRED top-level entry exists. A service missing `deploy/` has its
   deployment description somewhere unnameable.
2. No UNDECLARED top-level directory exists. `ALLOWED_TOP_LEVEL` is a closed
   set, and an unrecognised directory is a FINDING rather than a silent pass --
   the "enumerate the artifact, not a list" rule inverted: here the artifact IS
   the directory listing, and the declared set says what may be in it.
2b. The SAME closed set applies one level down, inside `src/<package>/`. Before
   this clause existed, `src/<package>/ops/` --
   or any other eighth directory -- was invisible here exactly as it was
   invisible to `check_layering.py`'s direction matrix: this gate asserted the
   §4 layers it REQUIRES are present and never asked whether anything ELSE was
   also there. `KNOWN_SOURCE_LAYERS` is the closed set (domain, ports,
   application, adapters, transport, runtime, composition); a directory outside
   it is a finding unless the service declares it in `package_roles.yaml` as
   `extra_layers: <name>` with a reason and a bead -- the identical escape hatch
   `check_layering.py` reads, so a declared layer is quiet in both gates or
   neither.
3. `src/` holds exactly ONE package, and it carries the §4 layer directories.
4. No Dockerfile and no compose file outside the two places that may hold one:
   the kit (generic, template-owned) and `deploy/` (a registered override).
   This is the clause with teeth -- a root `Dockerfile` is how a service quietly
   stops using the kit's image while every drift gate still passes, because
   `check_kit_drift.py` compares the kit against its manifest and a file OUTSIDE
   the kit is invisible to it.
5. The kit's Dockerfile copies `migrations/` through an `ARG HAS_MIGRATIONS`
   stage selector rather than an unconditional `COPY migrations`. An
   unconditional copy is not a style choice: it FAILS THE BUILD for every
   service with no Alembic tree, which is most of them, so the generic
   Dockerfile would be generic in name only.

Exit 0 clean, 1 on a finding, 2 if the gate could not measure.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import (  # noqa: E402
    FIXTURE_ROOT,
    KNOWN_SOURCE_LAYERS,
    MIN_PACKAGES,
    PACKAGE_ROLES_FILE,
    PACKAGE_ROOT,
    declared_extra_layers,
    is_file_or_unreadable,
    load_package_roles,
    role_of,
    source_roots,
    validate_package_roles,
)

#: Top-level entries every service MUST have. Hand-written from the contract's
#: "Required" list, never derived from this template's own listing -- deriving
#: it would make the assertion true of whatever the template happens to contain,
#: which is the tautological-expectation defect the monorepo gate refuses.
REQUIRED_TOP_LEVEL = (
    ".github/workflows/ci.yml",
    "README.md",
    "deploy/compose.yml",
    "pyproject.toml",
    "requirements",
    "service-kit",
    "src",
    "tests",
)

#: Directories that MAY sit at the top level. A closed set: anything else is a
#: finding. `contracts/` is here because a service that publishes a schema needs
#: somewhere to put it; `migrations/` because a service with a database needs an
#: Alembic tree, and its ABSENCE is not a finding -- most services have no
#: database, and requiring the directory would force every one of them to carry
#: an empty Alembic scaffold. `.claude/` carries the shared agent-instruction
#: bridge to AGENTS.md; it is declared structure, not an ignored cache.
#: `.circleci/` because CircleCI reads its pipeline from `.circleci/config.yml`
#: and nowhere else; a service whose GitHub Actions are billing-blocked
#: runs its CI there. Its ABSENCE is not a finding --
#: `.github/workflows/ci.yml` stays required.
ALLOWED_TOP_LEVEL = frozenset(
    {
        ".circleci",
        ".claude",
        ".git",
        ".github",
        "contracts",
        "deploy",
        "docs",
        "migrations",
        "requirements",
        "scripts",
        "service-kit",
        "src",
        "tests",
    }
)

#: Cache and tool droppings. Not "allowed" -- simply not part of the repository,
#: so they are skipped before the closed set is consulted rather than listed in
#: it, where a reader would take them for structure.
IGNORED_TOP_LEVEL = frozenset(
    {
        ".DS_Store",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)

#: §4's layers inside `src/<package>/`.
REQUIRED_SOURCE_LAYERS = (
    "adapters",
    "application",
    "composition",
    "domain",
    "ports",
    "transport",
)

#: The only two directories that may contain a Dockerfile or a compose file.
CONTAINER_FILE_HOMES = ("service-kit/docker", "deploy")

#: HAND-WRITTEN floors. A scan that reached nothing must refuse rather than
#: report a clean tree: "found no undeclared directory" and "listed no
#: directory at all" are the same output, and only a floor tells them apart.
#: The two layers only a DEPLOYABLE has. A composition ROOT and an HTTP SURFACE
#: are meaningless for a package that is never deployed, so a declared library is
#: exempt from these and from nothing else.
DEPLOYABLE_ONLY_LAYERS = frozenset({"composition", "transport"})

MIN_TOP_LEVEL_ENTRIES = 6

#: A FLOOR MUST COUNT SOMETHING THAT PROVES THE SCAN RAN, NEVER SOMETHING THAT PROVES THE
#: TREE CONFORMS. This constant replaces `MIN_SOURCE_LAYERS = 6`, which counted the layers
#: this gate FOUND -- its own subject -- and so went silent exactly where it had most to
#: say. Measured on `model-service`, whose four missing layers are the reason the
#: gate exists there:
#:
#:     check-folder-structure: REFUSE -- found 2 of 6 source layers;
#:                             the layer scan did not reach the package        (exit 2)
#:
#: The scan reached the package perfectly. It found four layers missing, which is a
#: FINDING, and the floor converted it into a refusal whose text is a false diagnosis --
#: it sends the reader to look for a broken glob. The worse the tree, the less the gate
#: could report about it, and at zero layers it would have been silent altogether.
#:
#: `layer_probes` counts (package, layer) pairs TESTED, which is evidence the loop ran and
#: is independent of what it found: a package with every layer missing still contributes a
#: full set of probes. The value is 4 rather than 6 because a package declared
#: `role: library` withdraws the two deployable-only layers, so a legitimate library-only
#: tree probes exactly four -- a floor of 6 would false-red it, and a false red that names
#: a real package is the worse direction.
MIN_LAYER_PROBES = 4

#: Set by `_unreadable_package_arm` when that arm could not fire here, and printed in the
#: PASS line. An unrun arm is not a green arm, and a summary that cannot say which is which
#: is the same defect as a count that says nothing about which route an item took.
UNREADABLE_ARM_SKIPPED = ""


def refuse(message: str) -> int:
    print(f"check-folder-structure: REFUSE -- {message}", file=sys.stderr)
    return 2


def _is_container_file(relative: str) -> bool:
    name = Path(relative).name
    return name == "Dockerfile" or name.startswith("Dockerfile.") or (
        name.startswith(("compose", "docker-compose")) and name.endswith((".yml", ".yaml"))
    )


def analyse(root: Path) -> tuple[list[str], list[str], dict[str, int]]:
    """(findings, unmeasurable, counts) for one service tree."""
    findings: list[str] = []
    unmeasurable: list[str] = []

    entries = [p for p in sorted(root.iterdir()) if p.name not in IGNORED_TOP_LEVEL]
    counts = {
        "top_level": len(entries),
        "source_layers": 0,
        "container_files": 0,
        "package_top_level": 0,
        # Scan EVIDENCE, read by the floors. `source_layers` above is kept for the PASS
        # line and is NOT floored on any more: see MIN_LAYER_PROBES.
        "layer_probes": 0,
        "packages_discovered": 0,
        "packages_probed": 0,
    }

    # 1. required entries
    for relative in REQUIRED_TOP_LEVEL:
        if not (root / relative).exists():
            findings.append(
                f"{relative} is missing. Every Foundry service carries it (contract §5); "
                f"a service without it has no declared place for what belongs there"
            )

    # 2. the closed set
    for path in entries:
        if path.is_dir() and path.name not in ALLOWED_TOP_LEVEL:
            findings.append(
                f"{path.name}/ is a top-level directory that the service shape does not "
                f"declare. Put it under an existing one, or add it to ALLOWED_TOP_LEVEL in "
                f"the template with a reason -- a new root directory in one service is how "
                f"nine layouts happen"
            )

    # 3. src/ and its layers, for EVERY package rather than the first of one
    packages = source_roots(root)
    counts["packages_discovered"] = len(packages)
    if len(packages) < MIN_PACKAGES:
        unmeasurable.append(
            f"src/ holds {len(packages)} importable package(s), so the layer check has "
            "nothing to run against"
        )
    else:
        entries, problems = load_package_roles(root / "scripts" / PACKAGE_ROLES_FILE)
        problems += validate_package_roles(entries, packages)
        if problems:
            unmeasurable.extend(problems)
        for package in packages:
            # CHECK THE ANSWERS ARE TRUSTWORTHY BEFORE COUNTING THEM. Every layer verdict
            # below comes from `(package / layer).is_dir()`, and `is_dir()` SWALLOWS OSError
            # and returns False -- so a directory this process cannot traverse renders as
            # every layer missing, which is a full set of confident false findings rather
            # than an error. `os.access(X_OK)` is the exact permission those stats need.
            #
            # THE FIRST VERSION OF THIS GUARD CALLED `iterdir()` AND WAS WRONG for the layer
            # loop alone: that loop stats children by name, so an execute-only package
            # (mode 0o111) answers every one of its probes correctly while `iterdir()`
            # raises. X_OK is the right test for the stats, and it stays.
            if not os.access(package, os.X_OK):
                unmeasurable.append(
                    f"src/{package.name}/ is not traversable by this process, so every "
                    f"`is_dir()` below it answers False; the layers would be reported "
                    f"missing rather than unmeasured"
                )
                continue
            # CLAUSE 2b NOW LISTS THE PACKAGE, AND THAT CHANGES THE ANSWER FOR 0o111. The
            # closed layer set asks what ELSE sits in `src/<package>/`, which only a
            # listing can say. A package this process can traverse but not list can have
            # its six required layers confirmed and its undeclared seventh never seen, so
            # "clean" would be a claim about a directory the gate did not read. It is
            # therefore REFUSED as unmeasurable -- a named refusal, never a finding (the
            # tree may be fine) and never a pass (nobody looked). Checked here, before the
            # package is counted as probed, so the floors cannot certify it either.
            try:
                listing = sorted(package.iterdir())
            except OSError as error:
                unmeasurable.append(
                    f"src/{package.name}/ is traversable but cannot be listed by this "
                    f"process ({type(error).__name__}), so the closed layer set (clause 2b) "
                    f"cannot see what else is there; unmeasurable, not clean"
                )
                continue
            counts["packages_probed"] += 1
            # A LIBRARY is exempt from exactly two layers, and only those two.
            # `composition/` is a composition ROOT and `transport/` is an HTTP
            # SURFACE: both are meaningless for a package that is not deployed,
            # so requiring them is demanding files with nothing to put in them.
            # The other four describe domain logic and are required of anything
            # holding any. The exemption is by MEANING, not by what this
            # estate's one library happens to contain.
            role = role_of(package.name, entries)
            required = [
                layer
                for layer in REQUIRED_SOURCE_LAYERS
                if role == "service" or layer not in DEPLOYABLE_ONLY_LAYERS
            ]
            present = [layer for layer in required if (package / layer).is_dir()]
            counts["source_layers"] += len(present)
            counts["layer_probes"] += len(required)
            for layer in required:
                if layer not in present:
                    findings.append(
                        f"src/{package.name}/{layer}/ is missing. §4's layers are the shape a "
                        f"reader navigates by and the shape the dependency-direction gate "
                        f"reads (package role: {role}).\n"
                        f"    THE REMEDY IS NOT FOUR EMPTY DIRECTORIES. If this package really "
                        f"has no {layer} of its own, it is not a deployable service, and the "
                        f"answer is to declare it in {PACKAGE_ROLES_FILE} as `role: library` "
                        f"with a reason -- which withdraws the two deployable-only layers "
                        f"{sorted(DEPLOYABLE_ONLY_LAYERS)} and leaves the four that describe "
                        f"domain logic still required. Creating the directory to clear this "
                        f"line satisfies the gate and tells the next reader something false.\n"
                        f"    A package role is DECLARED, never inferred from a directory listing."
                    )
            for half in ("real", "fake"):
                if (package / "adapters").is_dir() and not (package / "adapters" / half).is_dir():
                    findings.append(
                        f"src/{package.name}/adapters/{half}/ is missing. Both halves are "
                        f"required: `fake` with no `real` is a service that never reaches a "
                        f"resource, and `real` with no `fake` is a test suite that must mock"
                    )

            # 2b. the SAME closed set, one level down. A directory here that is
            # neither a known layer nor a declared exception is invisible to
            # check_layering.py's direction matrix too: both
            # gates classify KNOWN layers and must not silently skip the rest.
            declared = declared_extra_layers(package.name, entries)
            for sub in listing:
                if not sub.is_dir() or sub.name in IGNORED_TOP_LEVEL:
                    continue
                counts["package_top_level"] += 1
                if sub.name in KNOWN_SOURCE_LAYERS or sub.name in declared:
                    continue
                findings.append(
                    f"src/{package.name}/{sub.name}/ is a top-level layer this gate does not "
                    f"know ({sorted(KNOWN_SOURCE_LAYERS)}). check_layering.py cannot enforce a "
                    f"direction it has never heard of either, so an unlisted layer imports "
                    f"whatever it wants with neither gate reporting it. Move it into an "
                    f"existing layer, or declare it in {PACKAGE_ROLES_FILE} as "
                    f"`extra_layers: {sub.name}` with a reason and a bead"
                )

    # 4. container files only where they may live
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_TOP_LEVEL or part == ".git" for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if not _is_container_file(relative):
            continue
        counts["container_files"] += 1
        if FIXTURE_ROOT.exists() and FIXTURE_ROOT in path.parents:
            continue
        parent = Path(relative).parent.as_posix()
        # `startswith(tuple)` would also accept `deployment-notes/`, which is a
        # different directory that happens to share five letters.
        if any(parent == home or parent.startswith(f"{home}/") for home in CONTAINER_FILE_HOMES):
            continue
        findings.append(
            f"{relative} is a container file outside {' or '.join(CONTAINER_FILE_HOMES)}. "
            f"The kit's image is the default and `deploy/` holds a registered override; a "
            f"copy anywhere else is invisible to check_kit_drift.py, which only compares "
            f"files INSIDE the kit"
        )

    return findings, unmeasurable, counts


def _dockerfile_uses_a_migrations_selector(dockerfile: Path) -> tuple[bool, str]:
    """Does the kit Dockerfile gate `COPY migrations` behind a stage selector?"""
    if not dockerfile.is_file():
        return False, f"{dockerfile} does not exist"
    lines = dockerfile.read_text().splitlines()
    stage = ""
    guarded = False
    unguarded: list[int] = []
    selector = False
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if line.startswith("#"):
            continue
        upper = line.upper()
        if upper.startswith("FROM "):
            parts = line.split()
            stage = parts[-1] if len(parts) >= 4 and parts[-2].upper() == "AS" else ""
            if "${HAS_MIGRATIONS}" in line or "$HAS_MIGRATIONS" in line:
                selector = True
            continue
        if upper.startswith("COPY ") and " migrations" in f" {line} ":
            if stage.startswith("migrations-"):
                guarded = True
            else:
                unguarded.append(number)
    if unguarded:
        return False, (
            f"COPY of migrations/ at line(s) {unguarded} sits outside a `migrations-*` "
            f"stage. An unconditional copy fails the build for every service with no "
            f"Alembic tree"
        )
    if not selector:
        return False, (
            "no stage selects on HAS_MIGRATIONS. Without `FROM migrations-${HAS_MIGRATIONS}` "
            "the two stages exist and neither is ever chosen"
        )
    if not guarded:
        return False, "a HAS_MIGRATIONS selector exists but no `migrations-*` stage copies anything"
    return True, "migrations copied through a HAS_MIGRATIONS stage selector"


#: A whole conforming service, as a list of files to create. HAND-WRITTEN, and
#: deliberately not derived from `REQUIRED_TOP_LEVEL` -- a spec generated from
#: the same constant the gate asserts against would agree with any constant,
#: including an empty one.
#:
#: Note every layer directory carries a file. Git does not track an empty
#: directory, and `is_dir()` does not care, so a spec of bare directory names
#: would pass here and describe a tree that cannot exist in a repository.
CONFORMING_TREE = (
    ".github/workflows/ci.yml",
    "README.md",
    "deploy/compose.yml",
    "pyproject.toml",
    "requirements/service.in",
    "service-kit/README.md",
    "src/fixture_service/__init__.py",
    "src/fixture_service/adapters/__init__.py",
    "src/fixture_service/adapters/fake/__init__.py",
    "src/fixture_service/adapters/real/__init__.py",
    "src/fixture_service/application/__init__.py",
    "src/fixture_service/composition/__init__.py",
    "src/fixture_service/domain/__init__.py",
    "src/fixture_service/ports/__init__.py",
    "src/fixture_service/transport/__init__.py",
    "tests/test_placeholder.py",
)


def floor_problem(counts: dict[str, int]) -> str | None:
    """The floors, as a FUNCTION so a control can drive them. None when they hold.

    THEY USED TO LIVE INLINE IN `main`, AND THAT IS WHY THE OLD ONE WAS WRONG FOR MONTHS.
    `_self_test` calls `analyse` directly, so every control case skipped the floors
    entirely -- including `missing-layer`, the case whose whole subject is the count the
    old floor read. Six controls, one of them aimed squarely at this clause, and not one of
    them could reach it. A rule this file already states about production applies to a
    gate's own body too: a check nothing calls is a check nobody has run.

    NONE OF THE THREE FIRES ON ANY SERVICE IN THE ESTATE TODAY, and that is stated rather
    than implied. Measured 2026-09-19 by calling `analyse` over eleven trees -- the template
    and every service at its own tip: every one reports discovered == probed, six layer
    probes per package (twenty-four for the four-package agent-runtime), and zero
    unmeasurable. The clauses are walls against shapes the estate does not currently
    contain. A clause whose fixtures are the only thing it has ever refused must not be
    described in the language of a clause that caught something.
    """
    if counts["top_level"] < MIN_TOP_LEVEL_ENTRIES:
        return (
            f"listed {counts['top_level']} top-level entr(ies), fewer than the floor of "
            f"{MIN_TOP_LEVEL_ENTRIES}. A scan that read nothing reports the same clean "
            f"tree as a scan that read everything"
        )
    if counts["layer_probes"] < MIN_LAYER_PROBES:
        return (
            f"ran {counts['layer_probes']} (package, layer) probe(s), fewer than the floor "
            f"of {MIN_LAYER_PROBES}. The layer loop did not reach a package. This counts "
            f"probes ATTEMPTED, so a package missing every layer still clears it and is "
            f"reported as findings -- which is the difference between this floor and the "
            f"`MIN_SOURCE_LAYERS` it replaced"
        )
    if counts["packages_probed"] != counts["packages_discovered"]:
        return (
            f"discovered {counts['packages_discovered']} package(s) and probed "
            f"{counts['packages_probed']}. Every discovered package is either probed or "
            f"named unmeasurable; a package in neither state is a silent skip inside a "
            f"count that still claims full scope"
        )
    return None


def _floor_cases() -> list[tuple[str, dict[str, int], str | None]]:
    """(name, counts, expected_marker) -- BOTH directions over `floor_problem`.

    Inline rather than a tree, because the subject is a COUNT. Materialising a service
    with an unreadable package would need a chmod that a root-owned CI container ignores,
    and a control that silently cannot fire is the thing this whole file is about.
    The unreadable-package path is proved separately, as a tree, in `_self_test`.
    """
    healthy = {
        "top_level": 8,
        "layer_probes": 6,
        "packages_discovered": 1,
        "packages_probed": 1,
    }
    library_only = {**healthy, "layer_probes": 4}
    starved = {**healthy, "top_level": 2}
    dead_loop = {**healthy, "layer_probes": 0}
    # The case the old floor got wrong: four layers missing out of six is a FINDING, and
    # the probe count is untouched by it.
    four_missing = {**healthy, "layer_probes": 6}
    skipped = {**healthy, "packages_discovered": 3, "packages_probed": 1}
    return [
        ("healthy", healthy, None),
        ("library-only-tree", library_only, None),
        ("model-foundry-shaped", four_missing, None),
        ("starved-top-level", starved, "top-level"),
        ("dead-layer-loop", dead_loop, "probe(s)"),
        ("silently-skipped-package", skipped, "probed"),
    ]


def _control_cases() -> list[tuple[str, int, str, str]]:
    """(name, expected_findings, clause_marker, why).

    `expected_findings` is an exact HAND-WRITTEN count, not a pass/fail, and
    that is the load-bearing part. The plan's named offending case trips TWO
    clauses at once, so a pass/fail arm stays red when either one is blinded
    and proves neither. A count moves, and the clause marker says which finding
    it must be.
    """
    return [
        ("conforming", 0, "", "a service with the declared shape is clean"),
        ("agent-instructions", 0, "", "the shared .claude instruction bridge is declared"),
        ("agent-near-miss", 1, ".claude-other/", "a similarly named root is still undeclared"),
        ("circleci-pipeline", 0, "", "a CircleCI pipeline directory is declared"),
        ("circleci-near-miss", 1, ".travis/", "another CI runner's root is still undeclared"),
        ("extra-root-dir", 1, "legacy/", "an undeclared top-level directory is a finding"),
        ("missing-deploy", 1, "deploy/compose.yml", "a missing required entry is a finding"),
        ("stray-dockerfile", 1, "Dockerfile", "a container file outside both homes is a finding"),
        ("missing-layer", 1, "domain", "a missing §4 layer is a finding"),
        (
            "plan-named-offender",
            2,
            "",
            "the plan's own case: an extra root directory AND a missing deploy/",
        ),
        (
            "undeclared-layer",
            1,
            "ops",
            "an undeclared src/<pkg>/ layer is a finding",
        ),
        (
            "declared-layer",
            0,
            "",
            "the SAME undeclared layer, declared in package_roles.yaml as "
            "extra_layers: ops -- the escape hatch stays quiet",
        ),
    ]


#: The `package_roles.yaml` body for the `declared-layer` control case: the
#: SAME registry shape `check_layering.py` reads, with the SAME two mandatory
#: fields (`reason`, `bead`) `validate_package_roles` requires of any entry.
DECLARED_OPS_LAYER_REGISTRY = """\
fixture_service:
  role: service
  reason: fixture for the extra_layers escape hatch
  bead: fixture-extra-layers
  extra_layers: ops
"""


def _materialise(case: str, root: Path) -> None:
    """Write one whole miniature service for one control case."""
    paths = list(CONFORMING_TREE)
    if case in ("missing-deploy", "plan-named-offender"):
        paths.remove("deploy/compose.yml")
    if case == "missing-layer":
        paths = [p for p in paths if not p.startswith("src/fixture_service/domain/")]
    if case in ("extra-root-dir", "plan-named-offender"):
        paths.append("legacy/handler.py")
    if case == "agent-instructions":
        paths.append(".claude/CLAUDE.md")
    if case == "agent-near-miss":
        paths.append(".claude-other/CLAUDE.md")
    if case == "circleci-pipeline":
        paths.append(".circleci/config.yml")
    if case == "circleci-near-miss":
        paths.append(".travis/config.yml")
    if case == "stray-dockerfile":
        paths.append("Dockerfile")
    if case in ("undeclared-layer", "declared-layer"):
        paths.append("src/fixture_service/ops/probe.py")
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    if case == "declared-layer":
        registry = root / "scripts" / PACKAGE_ROLES_FILE
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(DECLARED_OPS_LAYER_REGISTRY)


def _self_test() -> int | None:
    """Materialise every control case and run the PRODUCTION analyser over it.

    Temp trees rather than tracked fixtures, and the reason is this gate's
    subject. A tracked conforming service would place a second
    `.github/workflows/ci.yml`, a second `service-kit/` and fifteen source
    files INSIDE the real kit -- so the manifest would own them, ruff would lint
    them, this gate's own container-file scan would have to special-case them,
    and `check_gate_fixture_isolation.py`'s canary would plant flat copies of
    them one level up. The filesystem is also the medium this gate's verdict
    reads, so building the tree there is the control mutating through the right
    medium rather than a convenience.
    """
    for case, expected, marker, why in _control_cases():
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _materialise(case, root)
            findings, unmeasurable, _ = analyse(root)
            if unmeasurable:
                return refuse(
                    f"control '{case}' could not be measured: {unmeasurable}. A control that "
                    f"refuses is not a control that answered"
                )
            if len(findings) != expected:
                return refuse(
                    f"control '{case}' produced {len(findings)} finding(s), expected "
                    f"{expected} ({why}). findings={findings}"
                )
            if marker and not any(marker in item for item in findings):
                return refuse(
                    f"control '{case}' produced {expected} finding(s) but none names "
                    f"'{marker}'. The count is right and the reason is not, which is a gate "
                    f"reddening for the wrong clause. findings={findings}"
                )

    for case, counts, marker in _floor_cases():
        problem = floor_problem(counts)
        if marker is None:
            if problem is not None:
                return refuse(
                    f"floor control '{case}' refused a healthy count: {problem}. counts="
                    f"{counts}. A floor that fires on a tree it should pass is a false red "
                    f"naming a real package, which is the worse direction"
                )
            continue
        if problem is None:
            return refuse(
                f"floor control '{case}' was accepted; it must refuse. counts={counts}. "
                f"A floor that has never been shown to fire cannot certify a scan"
            )
        if marker not in problem:
            return refuse(
                f"floor control '{case}' refused without naming '{marker}': {problem}. "
                f"A count that is right for the wrong reason is a gate reddening on the "
                f"wrong clause"
            )

    return _unreadable_package_arm()


def _unreadable_package_arm() -> int | None:
    """Two permission arms, as TREES -- the medium this verdict reads.

    Arm 1, mode 0o000: the package cannot be read AT ALL. Measured rather than assumed, the
    gate refuses UPSTREAM of the new guard -- `source_roots` stats `__init__.py` to discover
    a package, that stat fails, the package is not discovered, and the existing
    `MIN_PACKAGES` path reports `src/ holds 0 importable package(s)`. The requirement is
    that the gate refuse rather than report a clean tree, and it does; the arm asserts
    WHICH refusal, so a later change that turns this into a silent zero goes red.

    Arm 2, mode 0o111: execute-only. The package IS discovered and every layer stat would
    answer correctly, but clause 2b (the closed layer set) must LIST the package to see
    what else is in it, and cannot. The gate must therefore REFUSE it as unmeasurable,
    naming the listing -- not report findings (nothing wrong was seen) and not report a
    clean tree (the listing was never read). This arm originally asserted a clean
    judgement; that was right before clause 2b existed and became a crash after it, since
    `iterdir()` raised straight out of `analyse`. A crash is caught here and refused by
    name, so a regression reads as this arm failing rather than as a traceback.

    A NAMED SKIP RATHER THAN A SILENT PASS. A process running as root ignores both modes,
    so neither arm can fire there and asserting them would be a control that silently
    cannot fail. `UNREADABLE_ARM_SKIPPED` carries the reason into the PASS line.
    """
    global UNREADABLE_ARM_SKIPPED
    UNREADABLE_ARM_SKIPPED = ""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        _materialise("conforming", root)
        package = root / "src" / "fixture_service"
        original = package.stat().st_mode
        try:
            package.chmod(0o000)
            if is_file_or_unreadable(package / "__init__.py"):
                UNREADABLE_ARM_SKIPPED = (
                    "permission arms SKIPPED: this process reads a 0o000 directory "
                    "(euid 0?), so neither arm can fire here"
                )
                return None
            _, unmeasurable, counts = analyse(root)
            if not any("importable package" in item for item in unmeasurable):
                return refuse(
                    f"arm 1 (mode 0o000): the gate did not refuse an unreadable package. "
                    f"unmeasurable={unmeasurable}, counts={counts}. A package the gate "
                    f"cannot read must never be reported as a package with no findings"
                )
            if counts["packages_probed"] or counts["layer_probes"]:
                return refuse(
                    f"arm 1 (mode 0o000): the unreadable package was counted as probed: "
                    f"{counts}. The floor then certifies a scan that did not happen"
                )

            package.chmod(0o111)
            try:
                findings, unmeasurable, counts = analyse(root)
            except OSError as error:
                return refuse(
                    f"arm 2 (mode 0o111): analysing an execute-only package raised "
                    f"{type(error).__name__}: {error}. An unlistable package must be a named "
                    f"refusal, never a traceback"
                )
            if not any("cannot be listed" in item for item in unmeasurable):
                return refuse(
                    f"arm 2 (mode 0o111): an execute-only package was not refused as "
                    f"unlistable. unmeasurable={unmeasurable}, findings={findings}. Clause "
                    f"2b cannot see an undeclared layer it cannot list, so a clean verdict "
                    f"here certifies a directory the gate never read"
                )
            if findings or counts["packages_probed"]:
                return refuse(
                    f"arm 2 (mode 0o111): the unlistable package was refused AND judged: "
                    f"findings={findings} counts={counts}. A package the gate cannot read "
                    f"must be neither counted as probed nor reported as defective"
                )
        finally:
            package.chmod(original)
    return None


#: The `package_roles.yaml` body for the STALE-declaration proof: `extra_layers`
#: names a directory `ops` that this tree never creates. Fails closed in the
#: OTHER direction from `declared-layer` above -- "a declaration naming no
#: package is a finding".
STALE_EXTRA_LAYER_REGISTRY = """\
fixture_service:
  role: service
  reason: fixture for the extra_layers stale-declaration proof
  bead: fixture-extra-layers
  extra_layers: ops
"""


def _self_test_stale_extra_layer() -> int | None:
    """A declared `extra_layers` naming a directory that does not exist must be
    UNMEASURABLE, never a silent pass. `_control_cases()` cannot carry this case:
    every case there asserts `findings == expected`, and a registry PROBLEM
    surfaces as `unmeasurable`, not a finding -- the same split
    `validate_package_roles` already uses for a stale `carve_out`. So this is a
    dedicated arm, proved against the identical conforming tree `declared-layer`
    uses, minus the one thing that makes the declaration true: `ops/` itself.
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for relative in CONFORMING_TREE:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        registry = root / "scripts" / PACKAGE_ROLES_FILE
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(STALE_EXTRA_LAYER_REGISTRY)
        _findings, unmeasurable, _counts = analyse(root)
        if not unmeasurable:
            return refuse(
                "a package_roles.yaml declaring extra_layers: ops with no ops/ directory "
                "on disk was measured as clean; a stale declaration must be UNMEASURABLE, "
                "not a silent pass"
            )
    return None


def main() -> int:
    failed = _self_test()
    if failed is not None:
        return failed
    failed = _self_test_stale_extra_layer()
    if failed is not None:
        return failed

    findings, unmeasurable, counts = analyse(PACKAGE_ROOT)

    # UNMEASURABLE FIRST, then the floors. An unreadable package trips both, and its own
    # message names the package while the floor can only say a number was low -- the
    # precise diagnosis must not be shadowed by the coarse one.
    if unmeasurable:
        for item in unmeasurable:
            print(f"  {item}", file=sys.stderr)
        return refuse(f"{len(unmeasurable)} thing(s) could not be measured")

    problem = floor_problem(counts)
    if problem is not None:
        return refuse(problem)

    ok, why = _dockerfile_uses_a_migrations_selector(
        PACKAGE_ROOT / "service-kit" / "docker" / "Dockerfile"
    )
    if not ok:
        findings.append(f"service-kit/docker/Dockerfile: {why}")

    #: Built ONCE and printed on the FAIL path and the PASS path alike. Scope belongs on
    #: the path a reader studies, and that is the failing one: a finding list says what
    #: was wrong with what the scan reached and nothing at all about what it reached.
    #: Printed on one path only, a coverage figure is absent from every run that matters.
    #: ONE variable feeding both paths makes DIVERGENCE unrepresentable -- the two cannot
    #: drift apart, which is the failure a second copy would eventually have. It does not
    #: make DELETION unrepresentable, and no control here covers that; stated rather than
    #: left for someone to assume the arms below are watching it.
    coverage = (
        f"{counts['layer_probes']} (package, layer) probe(s) over "
        f"{counts['packages_probed']}/{counts['packages_discovered']} package(s)"
    )

    if findings:
        print(f"check-folder-structure: FAIL {len(findings)} finding(s) [{coverage}]:")
        for item in findings:
            print(f"  {item}")
        return 1

    arms = (
        len(REQUIRED_TOP_LEVEL)
        + counts["top_level"]
        + len(REQUIRED_SOURCE_LAYERS)
        + counts["package_top_level"]
        + counts["container_files"]
        + 1
    )
    print(
        f"check-folder-structure: PASS {len(REQUIRED_TOP_LEVEL)} required entr(ies) + "
        f"{counts['top_level']} listed + {len(REQUIRED_SOURCE_LAYERS)} source layer(s) + "
        f"{counts['package_top_level']} package-top-level dir(s) checked against the closed "
        f"set + {counts['container_files']} container file(s) + 1 migrations-selector check = "
        f"{arms} arms; {arms} executed, 0 skipped, 0 unaccounted. {why}. "
        f"Controls: {len(_control_cases())} trees, "
        f"{sum(1 for c in _control_cases() if c[1] == 0)} quiet and "
        f"{sum(1 for c in _control_cases() if c[1] > 0)} flagged; "
        f"{len(_floor_cases())} floor case(s), "
        f"{sum(1 for c in _floor_cases() if c[2] is None)} accepted and "
        f"{sum(1 for c in _floor_cases() if c[2] is not None)} refused; "
        f"{coverage}."
        + (f" {UNREADABLE_ARM_SKIPPED}." if UNREADABLE_ARM_SKIPPED else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
