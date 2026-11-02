#!/usr/bin/env python3
"""A source module no test can reach is a module nobody has tested. Refuse it.

WHAT "REACHABLE" MEANS HERE, AND WHY IT IS NOT "A TEST IMPORTS IT". The obvious gate --
flag a module no test file imports by name -- was written first and MEASURED against this
template: it flags 10 of 40 modules, and almost every one of them is correct. A controller
is exercised through the FastAPI app, `composition.py` through every `build_container`
call, `ports/auth.py` through the adapter that implements it. Shipping that matcher would
have handed an author ten findings with real file paths, of which roughly two were real --
and this repository's own history records that a false red naming a real file is the worse
direction, because the natural response is to "fix" it rather than doubt the detector.

So the gate walks the IMPORT CLOSURE: seed with everything the tests import, follow
intra-package edges to a fixed point, and flag what is left. On this tree that is 3 modules
instead of 10, and all three were genuinely uncovered -- two shipped fakes nothing
exercised, and `main.py`, the one module that runs at import time in the container.

WHAT IT CANNOT SEE, stated rather than implied. Reachability is not coverage: a module
imported by a test that never calls it counts as reached. This gate is a FLOOR -- it
catches the module nothing can even get to -- and a floor is worth having precisely
because it cannot be satisfied by accident.

THE ESCAPE IS A REGISTRY, AND IT SHIPS EMPTY. `scripts/test_first_exemptions.yaml` takes a
module, a reason and a bead. It is empty today because every finding on this tree was fixed
rather than exempted, and its drift check is live in BOTH directions: an entry naming a
module that no longer exists is an error, and so is an entry for a module that is now
reachable. An exemption mechanism written the day the first exemption arrives is a
mechanism nobody has ever run.

Exit 0 clean, 1 on a finding, 2 if the gate could not measure.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import PACKAGE_ROOT, service_data_file, source_roots  # noqa: E402

ROOT = PACKAGE_ROOT
#: Service-owned, outside the kit: an exemption is a statement about ONE
#: service's modules, so the kit must not carry it.
EXEMPTIONS = service_data_file("test_first_exemptions.yaml")

#: Hand-written floors. Both are literals rather than counts taken from the tree, because
#: an expectation derived from the artifact under test holds for any artifact, including an
#: empty one -- the defect `check_tautological_expectation.py` exists to prevent.
MIN_TEST_FILES = 10
MIN_MODULES = 20


def fail(message: str) -> None:
    print(f"[test-first] {message}", file=sys.stderr)


def refuse(message: str) -> int:
    fail(f"REFUSED: {message}")
    return 2


#: WHY THIS GATE KEEPS A NOTE AND NOT A FUNCTION. It used to carry its OWN enumerator,
#: `package_roots`, byte-equivalent to `gate_common.source_roots` and answering to a
#: different name. That is a second authority for one idea, and this file is the worst
#: possible place for one: the whole reason this gate was missed from an earlier change's
#: inventory of eight is that the other eight called `source_root()` and this one carried
#: its own copy -- `packages[0] if len(packages) == 1 else None` -- so a search keyed on the
#: identifier could not see it. Replacing one private copy with another private copy fixes
#: the behaviour and leaves the property that hid it. Proved equivalent on four real trees
#: (template, agent-runtime 3 packages, llm-gateway, model-foundry) plus an empty-tree and a
#: one-package-plus-one-non-package control before the local copy was deleted. bead
#: an earlier change.
#:
#: WHAT IS GENUINELY THIS GATE'S OWN, and is why the note stays: IT TAKES NO PER-PACKAGE
#: LOOP. The other eight aggregate per-package results, so their floors sum. This gate's
#: subject is an import CLOSURE, which crosses package boundaries -- agent-runtime
#: has 12 such edges -- and its floor counts the SCAN. Partitioning it was measured:
#:
#:     foundry_agent_kernel      9 modules  -> a per-package loop REFUSES (floor is 20)
#:     foundry_agent_runtime    42 modules  -> verdict
#:     foundry_sandbox          15 modules  -> a per-package loop REFUSES
#:     UNION                     66 modules  -> one verdict
#:
#: Two thirds of a healthy service's packages fall under a floor nobody changed, purely
#: because the scan was partitioned. So the closure runs ONCE over the union of every
#: package, and the package filter widens from one name to the set.


def python_files(directory: Path):
    return [p for p in directory.rglob("*.py") if "__pycache__" not in p.parts]


def dotted(path: Path, src: Path) -> str:
    parts = list(path.relative_to(src).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def named_module_strings(path: Path, modules: set[str]) -> set[str]:
    """Dotted module paths a test names as a STRING, matched against the real module set.

    WHY THIS CLAUSE EXISTS, AND WHY IT IS A WIDENING RATHER THAN A FIXTURE CHANGE. A test
    that imports through `importlib.import_module("pkg.main")` -- the only honest way to
    import an entrypoint under a controlled environment, since a plain import would execute
    it against whatever the developer has exported -- creates no static import edge, so the
    closure cannot see it and the module reads as untested. `mock.patch` and
    `monkeypatch.setattr` name their targets the same way.

    Reworking that test to satisfy the matcher is exactly what this repository's history
    forbids: it would convert a control into decoration and leave the detector unable to see
    a whole legitimate idiom. So the matcher widens instead.

    The widening is narrow: a string counts only if it EXACTLY equals a known module's
    dotted name. A coincidental match would have to be the full package path, which is not
    something prose or a fixture produces by accident.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in modules
    }


def intra_package_imports(path: Path, package: str | tuple[str, ...]) -> set[str]:
    """Every `package.*` name this file imports, module and symbol form alike.

    `package` may be ONE name or a tuple of them, because `str.startswith` accepts a tuple
    natively -- so a multi-package service widens the filter without changing the matcher.
    A cross-package edge (`foundry_agent_runtime` importing `foundry_agent_kernel`) is an
    ordinary edge once both names are in the filter, and is invisible when only one is.

    Both forms on purpose: `from pkg.a import B` names `pkg.a` as the module and `pkg.a.B`
    as a symbol, and a caller cannot know which one is a module without resolving it. The
    caller filters against the real module set, so an over-broad name costs nothing and a
    missing one would silently shrink the closure -- which is the direction that produces a
    false finding.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(package):
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith(package))
    return found


def lazy_reexport_edges(path: Path, package_dotted: str) -> set[str]:
    """Submodules a package `__init__` publishes through PEP 562 lazy re-export.

    THE IDIOM, and why a static import scan cannot see it. `adapters/real/__init__.py`
    declares `_MODULE_BY_NAME = {"RepositoryReadinessCheck": "readiness", ...}` and resolves
    it in `__getattr__` with `importlib.import_module(f"{__name__}.{module_name}")`. The
    module name is built at run time from a dict value, so there is no `ImportFrom` node to
    read, and the two submodules reached only that way read as modules no test can touch.
    That is a false red naming a real file, and the remedy it implies -- re-export eagerly
    -- would import `boto3` and `pymysql` at package-import time and break the two import
    guards the package already pays for.

    THE MATCH IS NARROW IN TWO WAYS. The file must actually import `importlib`, so a
    package whose `__init__` merely holds strings gains no edges; and a literal counts only
    if it names a REAL sibling module, which the caller's own filter enforces. Both
    conditions have to hold, because either alone would let an ordinary docstring word
    become an import edge and buy a false green.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    imports_importlib = any(
        (isinstance(n, ast.Import) and any(a.name == "importlib" for a in n.names))
        or (isinstance(n, ast.ImportFrom) and n.module == "importlib")
        for n in ast.walk(tree)
    )
    if not imports_importlib:
        return set()
    return {
        f"{package_dotted}.{node.value}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.isidentifier()
    }


def unreachable(
    modules: set[str],
    edges: dict[str, set[str]],
    seeds: set[str],
    transparent: set[str] | None = None,
) -> list[str]:
    """The closure, as a PURE function over a graph. Extracted so it can be controlled.

    Everything above this line reads the filesystem; this is the only part that decides
    anything, and a decision procedure that can only be exercised by building a directory
    tree is a decision procedure nobody exercises. `META_CONTROL_DETECTOR` in the
    monorepo's wrapper calls exactly this with hand-built graphs, which is how the
    meta-gate proves the rule fires AND stays quiet without a fixture on disk.

    `transparent` NAMES THE PACKAGE `__init__` MODULES, and they are traversed without
    being judged. A package that re-exports is a real import edge -- importing
    `pkg.transport.http.views` EXECUTES `views/__init__.py`, which imports
    `views.widgets_view`, so the module is loaded -- but an `__init__` is not itself a unit
    anyone writes a test for, so counting it in the population would manufacture a finding
    for every package in the tree. It is therefore a node the walk may pass THROUGH and
    never a node the walk may report.

    MEASURED, and the reason this parameter exists at all: without it the template's own
    `WidgetView` presenter read as untested. The controller imports it as
    `from ...transport.http.views import WidgetView`, which names the PACKAGE, and with the
    package absent from the graph that edge died one hop short of the module it reaches.
    The finding named a real file and the natural response would have been to write a
    second test importing the submodule directly -- which is the false red this estate
    records as the worse direction, since it leaves the matcher wrong and the idiom
    unsupported.
    """
    transparent = transparent or set()
    walkable = modules | transparent
    reached: set[str] = set()
    queue = [name for name in seeds if name in walkable]
    while queue:
        name = queue.pop()
        if name in reached:
            continue
        reached.add(name)
        for dependency in edges.get(name, set()):
            if dependency in walkable and dependency not in reached:
                queue.append(dependency)
    return sorted(modules - reached)


def load_exemptions() -> dict[str, str]:
    if not EXEMPTIONS.is_file():
        return {}
    text = EXEMPTIONS.read_text(encoding="utf-8")
    entries: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            current = stripped[:-1].strip('"\'')
            entries[current] = ""
        elif current and stripped.startswith("reason:"):
            entries[current] = stripped.split("reason:", 1)[1].strip().strip('"\'')
    return entries


def unreachable_modules(root: Path):
    roots = source_roots(root)
    if not roots:
        return None, f"no importable package under {root / 'src'}", 0, 0
    src = roots[0].parent
    package = tuple(p.name for p in roots)

    modules = {
        dotted(p, src): p
        for src_parent in roots
        for p in python_files(src_parent)
        if p.name != "__init__.py"
    }
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return None, f"no tests/ directory under {root}", 0, 0
    test_files = [p for p in python_files(tests_dir) if p.name.startswith("test_")]

    if len(test_files) < MIN_TEST_FILES:
        return (
            None,
            f"found {len(test_files)} test files, expected at least {MIN_TEST_FILES}",
            len(modules),
            len(test_files),
        )
    if len(modules) < MIN_MODULES:
        return (
            None,
            f"found {len(modules)} modules, expected at least {MIN_MODULES}",
            len(modules),
            len(test_files),
        )

    seeds: set[str] = set()
    for test in test_files:
        seeds |= intra_package_imports(test, package)
        seeds |= named_module_strings(test, set(modules))

    # Package `__init__` files: traversed, never judged. See `unreachable`'s docstring.
    packages = {
        dotted(p, src): p
        for src_parent in roots
        for p in python_files(src_parent)
        if p.name == "__init__.py"
    }
    edges = {
        name: intra_package_imports(path, package)
        for name, path in {**modules, **packages}.items()
    }
    for name, path in packages.items():
        edges[name] |= lazy_reexport_edges(path, name)
    found = unreachable(set(modules), edges, seeds, set(packages))

    if len(found) == len(modules):
        # A closure that reached nothing means the seeds never resolved -- a renamed
        # package, a moved tests/ directory. Reporting every module as untested would be a
        # catastrophic-looking finding produced entirely by a broken instrument.
        return (
            None,
            "the import closure reached 0 modules; seeds did not resolve",
            len(modules),
            len(test_files),
        )

    return set(found), None, len(modules), len(test_files)


#: The closure is a pure function over a graph, so the multi-package behaviour is
#: controlled WITHOUT a directory tree. One fixture per CLAUSE: the first two cases are the
#: same graph with and without the cross-package edge, which is the only thing that
#: distinguishes a union closure from a partitioned one. The third proves the walk still
#: passes THROUGH a package `__init__` and never reports it, across packages.
#:
#: Each case is (label, modules, edges, seeds, transparent, expected-unreachable).
UNION_CONTROLS = (
    (
        "a module reachable ONLY through another package is reached",
        {"pkg_a.uses", "pkg_b.used"},
        {"pkg_a.uses": {"pkg_b.used"}},
        {"pkg_a.uses"},
        set(),
        [],
    ),
    (
        "the same graph without the cross-package edge leaves it unreachable",
        {"pkg_a.uses", "pkg_b.used"},
        {"pkg_a.uses": set()},
        {"pkg_a.uses"},
        set(),
        ["pkg_b.used"],
    ),
    (
        "a package __init__ is traversed across packages and never reported",
        {"pkg_a.uses", "pkg_b.used"},
        {"pkg_a.uses": {"pkg_b"}, "pkg_b": {"pkg_b.used"}},
        {"pkg_a.uses"},
        {"pkg_b"},
        [],
    ),
)


def _union_control_failures() -> list[str]:
    problems = []
    for label, modules, edges, seeds, transparent, expected in UNION_CONTROLS:
        got = unreachable(set(modules), dict(edges), set(seeds), set(transparent))
        if got != expected:
            problems.append(f"control {label!r}: expected {expected}, got {got}")
    return problems


def main() -> int:
    control_problems = _union_control_failures()
    if control_problems:
        for item in control_problems:
            fail(item)
        return refuse(
            f"{len(control_problems)} of {len(UNION_CONTROLS)} closure control(s) failed, so "
            "the decision procedure is wrong before it has read a single file"
        )

    scanned = [p.name for p in source_roots(ROOT)]
    found, problem, module_count, test_count = unreachable_modules(ROOT)
    if problem is not None:
        return refuse(problem)

    exemptions = load_exemptions()

    stale = sorted(name for name in exemptions if name not in found)
    # Drift, both directions. An entry for a module that is now reachable is as wrong as a
    # missing entry: it claims an exemption for a gap that has been closed, and the next
    # reader believes it.
    findings = sorted(found - set(exemptions))

    # The scanned counts are in the summary, not only the findings. A line that reports
    # what it FOUND and never what it LOOKED AT reads identically whether the scan covered
    # forty modules or none, and "0 finding(s)" is the most reassuring sentence a broken
    # instrument can print. The wrapper in the monorepo floors on these two numbers.
    # THE FIRST CLAUSE OF THIS LINE IS A CONTRACT WITH ANOTHER READER, and it is not
    # decoration. The monorepo's `check_template_test_first.py` parses
    # `scanned (\d+) module\(s\) against (\d+) test file\(s\);` out of this child's stdout
    # and REFUSES when it cannot find it -- so inserting the package list BETWEEN "module(s)"
    # and "against", which is where it reads most naturally, turns that wrapper red at
    # exit 2 while this gate exits 0. Measured while writing the multi-package conversion.
    # New information goes AFTER the existing clauses, never inside them.
    print(
        f"[test-first] scanned {module_count} module(s) against {test_count} test file(s); "
        f"{len(found)} unreachable, {len(exemptions)} exempt, {len(findings)} finding(s)"
        f" over {len(scanned)} package(s) {scanned}. ONE closure over the union, never "
        f"partitioned -- a per-package loop would refuse any package under the floor of "
        f"{MIN_MODULES}. Controls: {len(UNION_CONTROLS)} closure arms "
        f"({sum(1 for c in UNION_CONTROLS if c[5])} flagged, "
        f"{sum(1 for c in UNION_CONTROLS if not c[5])} quiet)."
    )

    if stale:
        for name in stale:
            fail(f"stale exemption: {name} is reachable from the tests, or does not exist")
        return 1

    for name in findings:
        fail(f"no test can reach {name}; add a covering test or register it with a reason")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
