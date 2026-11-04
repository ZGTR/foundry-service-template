#!/usr/bin/env python3
"""§1: the dependency direction, and the submodule-contract import rule.

    composition -> runtime -> transport -> application -> domain
          |           |          |             |
          +-----------+----------+-------------+--> ports
          |
          +--> adapters --> ports, domain

FOUR rules, each a finding:

1. **Direction.** A submodule may import only what §1 allows it to. `domain`
   imports nothing but `domain` and stdlib; `ports` adds only `domain`;
   `application` may not see `transport`, `adapters`, `composition` or `runtime`.
2. **No framework or SDK in the inner layers.** `application` and `domain` that
   import `fastapi` or `boto3` are not testable without them, which is the whole
   reason the inner layers exist.
3. **The environment is read in exactly ONE module**, `composition/config.py`.
4. **No deep cross-submodule import.** `from pkg.domain.model.widget import
   Widget` bypasses `domain/__init__.py`, which IS the submodule's public
   contract -- so the contract stops describing the coupling and a rename inside
   `domain` breaks a module that was never supposed to see that path. A
   cross-submodule target must be a PACKAGE (a directory with `__init__.py`),
   which is enumerable from the filesystem rather than from a list someone
   maintains.
5. **The top-level layer itself must be KNOWN.** `ALLOWED` classifies exactly
   the seven declared layers. A file under an EIGHTH
   directory -- `src/<pkg>/ops/`, say -- had `ALLOWED.get("ops")` return `None`,
   which made rule 1 a no-op for it: an unrecognised layer could import
   `transport` and `adapters` directly and this gate reported PASS, because
   `allowed is not None` guards rule 1 and nothing else ever asked whether
   `"ops"` was a layer this gate had ever heard of. Enumerating known layers and
   silently skipping the rest is exactly the "list standing in for the
   artifact" defect `.claude/CLAUDE.md` names, aimed at this gate itself. So an
   unclassified top-level directory is now a finding on its own, independent of
   what it imports, unless the service DECLARES it in `package_roles.yaml` as
   `extra_layers: <name>` with a reason and a bead -- a reviewed, visible
   exception rather than an invisible one. A declared extra layer still gets no
   entry in `ALLOWED`: declaring it silences this rule, it does not grant it a
   direction, because inventing one per service is the per-service fork of the
   direction matrix this whole file exists to prevent.

WHY RULE 4 READS THE FILESYSTEM. The alternative -- a hardcoded list of
importable module paths -- is exactly the "list standing in for the artifact"
defect `.claude/CLAUDE.md` names: a new sub-package would be uncovered by
default, and a deleted one would leave a stale entry nothing notices. Rule 5
reads the filesystem for the same reason, one level up: `KNOWN_SOURCE_LAYERS`
is the closed set, and `package_roles.yaml`'s `extra_layers` is where a service
enumerates its own exceptions to it, never a per-gate list someone maintains
in this file.

Exit codes: `scripts/gate_exit_codes.py`.
"""

from __future__ import annotations

import ast
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import (  # noqa: E402
    EXPECTED_MARKED_FIXTURE_FILES,
    FIXTURE_ROOT,
    KNOWN_SOURCE_LAYERS,
    MIN_PACKAGES,
    PACKAGE_ROOT,
    declared_extra_layers,
    imports_in,
    load_package_roles,
    marker_count,
    package_of,
    python_sources,
    source_roots,
    unmarked_flagged_files,
    validate_package_roles,
)
from gate_exit_codes import GREEN, finding, refuse  # noqa: E402

#: §1's direction, written out. HAND-WRITTEN, never derived from the tree: a
#: matrix computed from the imports that exist is satisfied by any tree,
#: including a broken one.
ALLOWED: dict[str, frozenset[str]] = {
    "domain": frozenset({"domain"}),
    "ports": frozenset({"ports", "domain"}),
    "application": frozenset({"application", "ports", "domain"}),
    "adapters": frozenset({"adapters", "ports", "domain"}),
    "transport": frozenset({"transport", "application", "ports", "domain"}),
    "runtime": frozenset({"runtime", "transport", "application", "ports", "domain"}),
    "composition": frozenset(
        {"composition", "runtime", "transport", "adapters", "application", "ports", "domain"}
    ),
}

#: Frameworks and I/O SDKs the inner layers may not name.
#: ADJUDICATED against the mature service copy, which carried five roots this kit
#: did not: `cryptography`, `docker`, `jsonschema`, `pydantic` and `pydantic_ai`.
#: The service is STRICTER, so the service wins, by the same per-clause rule the
#: lifecycle tokens follow -- take the stricter of each rather than either file
#: whole.
#:
#: Measured before widening rather than after, because a wider forbidden set is
#: the direction that manufactures false reds: against this template the five add
#: ZERO findings, and against agent-runtime they add exactly two, both
#: `jsonschema` in `domain/schema_validation.py` -- the one file that service's
#: own registry carves out. So the widening is what makes that carve-out mean
#: anything; without it the kit never flagged the import the exemption exempts.
FRAMEWORK_ROOTS = frozenset(
    {
        "boto3",
        "botocore",
        "cryptography",
        "docker",
        "fastapi",
        "httpx",
        "jsonschema",
        "pydantic",
        "pydantic_ai",
        "pymysql",
        "requests",
        "sqlalchemy",
        "starlette",
        "urllib3",
        "uvicorn",
    }
)
INNER_LAYERS = frozenset({"domain", "ports", "application"})

#: Framework roots ONE named inner layer may import despite rule 2, per owner ruling on
#: an earlier change.
#:
#: WHY THIS EXISTS RATHER THAN A DELETION FROM FRAMEWORK_ROOTS. This estate's standing goal
#: is "use pydantic", and rule 2 refused `application imports framework/SDK pydantic`; those
#: two could not both stand, and the owner relaxed. But `FRAMEWORK_ROOTS` is consulted for
#: every member of `INNER_LAYERS`, so deleting `pydantic` from it would have relaxed
#: `domain` and `ports` as well -- and that is not theoretical. Measured across the template
#: and eight services before this was written:
#:
#:     pydantic in application   1 hit   llm-gateway/application/completion_profiles.py
#:     pydantic in domain        1 hit   workflow-service/domain/model/jobs.py
#:     pydantic in ports         0 hits
#:     pydantic_ai in any inner  0 hits
#:
#: So a one-line deletion would have silently cleared a SECOND real refusal, in the
#: innermost layer, which the ruling did not authorise. The domain instance is reported
#: rather than folded in; it needs its own decision.
#:
#: RELAXING IS HARDER TO REVERSE THAN TIGHTENING once services rely on it, which is why the
#: allowance is keyed by exact package root and by exact layer name. `pydantic_ai` is a
#: DIFFERENT root and stays banned everywhere -- membership is exact, never a prefix, so
#: `pydantic_ai` can never be admitted by the entry for `pydantic`.
LAYER_FRAMEWORK_ALLOWANCES: dict[str, frozenset[str]] = {
    "application": frozenset({"pydantic"}),
}

#: The ONE module allowed to read the environment.
ENVIRONMENT_OWNER = ("composition", "config.py")

#: HAND-WRITTEN EXPECTATIONS for the fixture arms. Never `len(findings)` over the
#: fixture: that reduces the assertion to `n == n` and holds for any detector,
#: including one blinded to the rule it exists to enforce.
#: 4 -> 5 when `offending/pkg/ops/probe.py` was planted for rule 5
#:: one finding, for the undeclared layer itself, and no
#: more -- the two imports it carries deliberately target a PACKAGE
#: (`transport.http.controllers`, `adapters.real`), not a leaf module, so
#: rule 4's deep-import clause stays quiet and this fixture proves only rule 5.
#: 5 -> 8 when the owner's relaxation planted
#: `offending/pkg/application/pricing_policy.py`: one finding each for `fastapi`,
#: `pydantic_ai` and `sqlalchemy` in the `application` layer. The three are the
#: negative half of `LAYER_FRAMEWORK_ALLOWANCES` -- relaxing `pydantic` there must not
#: have relaxed a genuine framework, an ORM, or an SDK whose name starts with the same
#: seven letters. Derived by hand (5 + 3) and then confirmed against the gate, never
#: read off it. (The branch that planted it counted 4 + 3 = 7; rule 5's `ops/probe.py`
#: landed on develop first, so the merged tree is 5 + 3.)
EXPECTED_OFFENDING_FINDINGS = 8
#: Still 0, and that is now an assertion rather than a leftover: the conforming fixture's
#: `application/register_gadget.py` imports `pydantic`, which this gate refused until
#: Blinding `LAYER_FRAMEWORK_ALLOWANCES` makes this arm produce 1 and the
#: gate refuse, which is direction A of the control.
EXPECTED_CONFORMING_FINDINGS = 0
#: A two-sided floor: the gate must have INCLUDED real modules and it must have
#: resolved at least one cross-submodule import. A classifier that has never
#: demonstrated it can see anything has proved half of nothing.
MIN_PRODUCTION_MODULES = 20
MIN_CROSS_SUBMODULE_IMPORTS = 10


def _submodule_of(path: Path, root: Path) -> str | None:
    relative = path.relative_to(root).parts
    return relative[0] if len(relative) > 1 else None


def _module_file(root: Path, dotted: str) -> Path | None:
    """The file that defines intra-package module `dotted`: its `__init__.py` or `.py`.

    Matched against the directory LISTING, not `is_file()`: on a case-insensitive
    filesystem (macOS) `ports/Clock.py` "exists" because `ports/clock.py` does, which
    would read `from pkg.ports import Clock` -- a class -- as an import of a module.
    """
    directory = root.joinpath(*dotted.split(".")[1:])
    if directory == root:
        return root / "__init__.py" if (root / "__init__.py").is_file() else None
    try:
        siblings = {entry.name for entry in directory.parent.iterdir()}
    except OSError:
        return None
    if directory.name in siblings and (directory / "__init__.py").is_file():
        return directory / "__init__.py"
    if f"{directory.name}.py" in siblings:
        return directory.with_suffix(".py")
    return None


def _declared_exports(source: Path) -> list[str] | None:
    """A module's literal `__all__` of strings; None when absent, computed or extended."""
    exports: list[str] | None = None
    for node in ast.parse(source.read_text()).body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, (ast.AnnAssign, ast.AugAssign))
            else []
        )
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        value = node.value if not isinstance(node, ast.AugAssign) else None
        literal = isinstance(value, (ast.List, ast.Tuple)) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
        )
        if not literal:
            return None
        exports = [e.value for e in value.elts]
    return exports


def _imports(
    tree: ast.AST, path: Path, root: Path
) -> tuple[list[tuple[str, int]], list[tuple[int, str]]]:
    """(edges, problems): every module this file loads, and every import no rule can judge.

    Relative imports are resolved by `gate_common.imports_in`. A `from X import name`
    inside the package is an edge to `X.name` when that name is itself a submodule or
    subpackage, because that is the module Python loads: `from pkg import transport`
    reaches `pkg.transport` and `from pkg.domain.model import widget` reaches the leaf
    `pkg.domain.model.widget`, the same edges the `import` spellings make. Any other
    name (`__version__`, a class) leaves the edge at `X`.

    A wildcard from any module inside the package is a problem, whatever `X` declares.
    QA rounds 1 and 2 showed that reading `__all__` to expand one
    always leaves a spelling behind (`.append`, a conditional assignment, an alias
    re-export), and each one hid a forbidden edge. The edge `X` itself is still
    judged. A wildcard of a third-party module is out of scope. A None module (a
    relative import above the package) is a problem too.

    ONE EXCEPTION, AND IT IS THE CONTRACT ITSELF. Below the package root, a submodule
    that `X/__init__.py` lists in a literal `__all__` stays the edge `X`:
    `transport/http/controllers/__init__.py` publishes `bytes_controller`, so importing
    that name from the package goes through the package's declared contract, which is
    the thing rule 4 protects. At the root the member is always promoted, because there
    the member IS the layer the direction rule has to see. Every way `_declared_exports`
    can misread `__all__` shrinks the published set, so a misreading makes this stricter.
    """
    package = root.name
    edges: list[tuple[str, int]] = []
    problems: list[tuple[int, str]] = []
    for statement in imports_in(tree, package_of(path, root.parent)):
        module, line = statement.module, statement.lineno
        if module is None:
            problems.append(
                (
                    line,
                    f"relative import climbs above {package}; Python refuses it at import "
                    f"time and no direction rule can judge it",
                )
            )
            continue
        if not statement.is_from or module.split(".")[0] != package:
            edges.append((module, line))
            continue
        members = [name for name, _bound in statement.names]
        source = _module_file(root, module)
        if "*" in members:
            problems.append(
                (
                    line,
                    f"wildcard import from {module} inside the package hides dependency "
                    f"edges; import names explicitly",
                )
            )
            edges.append((module, line))
            continue
        published = (
            set(_declared_exports(source) or [])
            if module != package and source is not None and source.name == "__init__.py"
            else set()
        )
        targets = {
            f"{module}.{name}"
            if name not in published and _module_file(root, f"{module}.{name}")
            else module
            for name in members
        } or {module}
        edges.extend((target, line) for target in sorted(targets))
    return edges, problems


def _reads_environment(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                return True
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            if any(alias.name in {"environ", "getenv"} for alias in node.names):
                return True
    return False


def _is_package(root: Path, dotted_tail: Iterable[str]) -> bool:
    candidate = root.joinpath(*dotted_tail)
    return (candidate / "__init__.py").is_file()


def analyse(
    root: Path,
    *,
    is_fixture: bool = False,
    carve_out: dict[str, set[str]] | None = None,
    known: frozenset[str] | None = None,
) -> tuple[list[str], int, int, set[str], int]:
    """Return (findings, modules, cross_imports, located_layers, unclassified_modules).

    THE LAST TWO EXIST BECAUSE THIS GATE COULD JUDGE NOTHING AND SAY PASS. Every rule here
    is keyed on `relative.parts[0]` being one of ALLOWED's seven names. A package that
    organises its code under any other directory name is scanned, counted, and then not
    judged by a single rule -- and the verdict printed was a clean PASS.

    So the gate reports how many of the seven layers it located and how many modules it
    judged, and refuses a package it could not judge at all. A module in a top-level
    directory that is neither a known layer nor a declared `extra_layers` entry is a
    finding in its own right (rule 5).

    ONE analyser, used for production AND for both fixture arms. A second copy
    specialised to the fixtures is how a control comes to measure something the
    production scan does not do -- the gate would then be green on a fixture its
    real path would have flagged, and red on one it would not.

    `known` is this PACKAGE's closed set of top-level layers -- `KNOWN_SOURCE_LAYERS`
    plus whatever it declared as `extra_layers`. Both fixture arms pass `None`,
    which defaults to the bare `KNOWN_SOURCE_LAYERS`: a fixture has no
    `package_roles.yaml` of its own, so its offending arm's `ops/` stays
    undeclared, which is exactly the case rule 5 exists to catch.
    """
    package = root.name
    findings: list[str] = []
    modules = 0
    cross_imports = 0
    located: set[str] = set()
    unclassified = 0
    allowed_here = carve_out or {}
    known_layers = known if known is not None else KNOWN_SOURCE_LAYERS

    for path in python_sources(root, exclude_fixtures=not is_fixture):
        relative = path.relative_to(root)
        if len(relative.parts) < 2:
            continue
        submodule = relative.parts[0]
        modules += 1
        if submodule in ALLOWED:
            located.add(submodule)
        else:
            unclassified += 1
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:
            findings.append(f"{relative}: unparseable ({exc})")
            continue

        # Rule 5: the layer itself must be known or declared. Fires once per file
        # regardless of what the file imports -- an unclassified layer is the
        # finding, not merely what it happens to reach into.
        if submodule not in known_layers:
            findings.append(
                f"{relative}: {submodule!r} is not a known top-level layer "
                f"({sorted(KNOWN_SOURCE_LAYERS)}); check_layering cannot enforce a "
                f"direction it has never heard of. Move it into an existing layer, or "
                f"declare it in package_roles.yaml as `extra_layers: {submodule}` with a "
                f"reason and a bead"
            )

        # Rule 3: the environment is read in exactly one module.
        if _reads_environment(tree) and relative.parts != ENVIRONMENT_OWNER:
            findings.append(
                f"{relative}: reads os.environ/os.getenv outside {'/'.join(ENVIRONMENT_OWNER)}"
            )

        allowed = ALLOWED.get(submodule)
        edges, problems = _imports(tree, path, root)
        findings.extend(f"{relative}:{lineno}: {problem}" for lineno, problem in problems)
        for name, lineno in edges:
            parts = name.split(".")
            # Rule 2: no framework/SDK in an inner layer.
            carved = allowed_here.get(relative.as_posix(), set())
            layer_allowance = LAYER_FRAMEWORK_ALLOWANCES.get(submodule, frozenset())
            if (
                submodule in INNER_LAYERS
                and parts[0] in FRAMEWORK_ROOTS
                and parts[0] not in carved
                and parts[0] not in layer_allowance
            ):
                findings.append(f"{relative}:{lineno}: {submodule} imports framework/SDK {name}")
            if parts[0] != package or len(parts) < 2:
                continue
            target = parts[1]
            if target == submodule:
                continue
            cross_imports += 1
            # Rule 1: direction.
            if allowed is not None and target not in allowed and target not in carved:
                findings.append(
                    f"{relative}:{lineno}: {submodule} imports {target} "
                    f"(allowed: {sorted(allowed)})"
                )
            # Rule 4: the target must be a PACKAGE, i.e. a declared contract.
            if len(parts) > 2 and not _is_package(root, parts[1:]):
                findings.append(
                    f"{relative}:{lineno}: deep cross-submodule import {name} bypasses "
                    f"{package}.{target}'s __init__.py contract"
                )
    return findings, modules, cross_imports, located, unclassified


def location_problem(located: set[str], unclassified: int, modules: int) -> str | None:
    """None when the scan located at least one declared layer. A FUNCTION so a control can
    drive it -- a floor that is not a callable is not testable.

    WHY THE THRESHOLD IS ZERO AND NOT A FRACTION. `MIN_SOURCE_LAYERS = 6` in
    check_folder_structure was exactly this shape at a non-zero threshold, and it converted
    a service's four missing layers from FINDINGS into a refusal whose text was a false
    diagnosis. A floor must count something that proves THE SCAN RAN, never something that
    proves THE TREE CONFORMS. Locating none is the first: not one module sat in a directory
    any rule here knows, so every rule ran zero times and the PASS would be about nothing.
    Locating three of seven is the second -- a real verdict over the part the kit can see --
    and it is REPORTED rather than refused.

    WHAT THIS DOES NOT CLAIM. It does not make partial blindness an error. rag-service
    locates 3 of 7 and keeps 97 of its 139 modules outside every rule, and this returns None
    for it. The count is printed on BOTH the PASS and the FINDING path so that blindness is
    visible and so a 2b rename is observable as it happens; the decision about the remaining
    services is an earlier change, not this floor.
    """
    if modules and not located:
        return (
            f"located 0 of {len(ALLOWED)} declared layer(s) across {modules} module(s); "
            f"all {unclassified} sit in directories no rule here knows, so every rule ran "
            f"zero times and a PASS would describe nothing that was judged. Either this "
            f"package uses different layer names -- rename to {sorted(ALLOWED)}, or declare "
            f"a service-local layer map -- or it is not layered at all"
        )
    return None


#: BOTH DIRECTIONS over `location_problem`. Hand-written inputs, never derived from a tree:
#: the point of the clause is what it does with a count, and a count read off the artifact
#: would make the assertion `n == n`.
LOCATION_CONTROLS: list[tuple[str, set[str], int, int, bool]] = [
    # (name, located, unclassified, modules, must_refuse)
    ("template-shaped", {"domain", "ports", "application"}, 0, 75, False),
    ("rag-shaped, mostly blind", {"ports", "adapters", "composition"}, 97, 139, False),
    ("one layer only", {"domain"}, 120, 121, False),
    ("empty package, nothing to judge", set(), 0, 0, False),
    ("redteam-shaped, judged nothing", set(), 48, 48, True),
]


def _location_control_failures() -> list[str]:
    """Run every case and say which moved. An exit code cannot name the case that broke."""
    problems = []
    for name, located, unclassified, modules, must_refuse in LOCATION_CONTROLS:
        got = location_problem(located, unclassified, modules)
        if must_refuse and got is None:
            problems.append(f"control '{name}' was accepted and must refuse")
        if not must_refuse and got is not None:
            problems.append(f"control '{name}' was refused and must pass: {got}")
    # The one-layer case is the whole reason the threshold is zero rather than a fraction:
    # it must PASS while being 120/121 blind, or this clause has become the MIN_SOURCE_LAYERS
    # defect wearing a new name. Asserted separately so a widened threshold cannot be
    # excused by the list above being edited in the same commit.
    if location_problem({"domain"}, 120, 121) is not None:
        problems.append(
            "the threshold has moved off zero: a package with one located layer is now "
            "refused, which converts partial blindness from a reported fact into a refusal "
            "-- exactly the MIN_SOURCE_LAYERS defect this clause was written to avoid"
        )
    return problems


def _unrecognised_layout_arm() -> list[str]:
    """Drive the PRODUCTION analyser over a package laid out the way the blind services are.

    The inline cases above test what `location_problem` does with numbers. This tests that
    `analyse` PRODUCES those numbers from a real directory tree -- a control that cannot
    reach the code the verdict comes from is decoration, and the two halves fail
    independently: a matcher that never populates `located` would pass every inline case.

    A temp tree rather than a tracked fixture, because a third tree under
    `gate_fixtures/` would be found by every other gate's production scan and by
    check_gate_fixture_isolation's canary.
    """
    layout = {
        # model-service's real shape: two recognised, seven not.
        "blind": ["ports/x.py", "adapters/y.py", "controllers/a.py", "services/b.py"],
        # red-team's real shape: nothing a rule here knows.
        "invisible": ["controllers/a.py", "services/b.py", "handlers/c.py"],
    }
    problems = []
    with tempfile.TemporaryDirectory() as raw:
        for case, files in layout.items():
            root = Path(raw) / case / "pkg"
            (root).mkdir(parents=True)
            (root / "__init__.py").write_text("")
            for rel in files:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                (path.parent / "__init__.py").write_text("")
                path.write_text("")
            _, modules, _, located, unclassified = analyse(root, is_fixture=True)
            if case == "blind":
                if located != {"ports", "adapters"}:
                    problems.append(f"'blind' located {sorted(located)}, expected ports+adapters")
                if location_problem(located, unclassified, modules) is not None:
                    problems.append(
                        "'blind' was refused; partial blindness is reported, not refused"
                    )
            else:
                if located:
                    problems.append(f"'invisible' located {sorted(located)}, expected none")
                if location_problem(located, unclassified, modules) is None:
                    problems.append(
                        "'invisible' was accepted: a package where no module sits in a "
                        "declared layer must refuse, not PASS"
                    )
    return problems


#: (import statement planted in `application/probe.py`, findings it must produce). BOTH
#: SPELLINGS of each edge, because this gate once kept an `ImportFrom` only at
#: `level == 0` and printed PASS on `from ..transport import http` while flagging its
#: absolute twin. HAND-WRITTEN counts, never read off the gate.
RELATIVE_IMPORT_CASES: tuple[tuple[str, int], ...] = (
    ("from pkg.transport import http", 1),
    ("from ..transport import http", 1),
    ("from pkg import transport", 1),
    ("from .. import transport", 1),
    ("from ..transport import http as wire", 1),
    ("from ..domain.model.widget import Widget", 1),
    ("from ... import anything", 1),
    # Package members (QA rounds 1 and 2): a leaf module named as a member, and any
    # wildcard inside the package, which is refused whatever `__all__` says.
    ("from .. import *", 1),
    ("from pkg import *", 1),
    ("from ..domain.model import widget", 1),
    ("from pkg.domain.model import widget", 1),
    ("from ..ports import *", 1),
    ("from ..domain import *", 1),
    ("from os.path import *", 0),
    ("from . import sibling", 0),
    ("from .sibling import helper as h", 0),
    ("from .. import domain", 0),
    ("from ..domain import model", 0),
)


def _relative_import_arm() -> list[str]:
    """Drive the PRODUCTION analyser over every spelling in `RELATIVE_IMPORT_CASES`.

    A temp tree, for the same reason `_unrecognised_layout_arm` gives: a tracked tree
    under `gate_fixtures/` is read by every other gate's production scan.
    """
    layout = {
        "application/sibling.py": "",
        "domain/model/widget.py": "",
        "ports/store.py": "",
        "transport/http/api.py": "",
        "__init__.py": '__all__ = ["transport"]\n',
        "domain/__init__.py": '__all__ = ["model"]\n',
    }
    problems = []
    for statement, expected in RELATIVE_IMPORT_CASES:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "pkg"
            for rel, body in {**layout, "application/probe.py": statement + "\n"}.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                parent = path.parent
                while parent != root.parent:
                    (parent / "__init__.py").touch()
                    parent = parent.parent
                path.write_text(body)
            findings = analyse(root, is_fixture=True)[0]
            if len(findings) != expected:
                problems.append(f"{statement!r} produced {findings}, expected {expected}")
    return problems


def _fixture_arm(name: str) -> tuple[list[str], int, int, set[str], int] | None:
    """Run the PRODUCTION analyser over a fixture package. None when absent."""
    root = FIXTURE_ROOT / name / "pkg"
    if not root.is_dir():
        return None
    return analyse(root, is_fixture=True)


def main() -> int:
    # Layering applies to EVERY package. A library package has the same inward
    # dependency direction as a service one, so unlike the MVC gate there is no
    # surface filter here: a package is in scope because it exists.
    # Rule 5 reads ONE closed set. If `ALLOWED`'s keys and `KNOWN_SOURCE_LAYERS` ever
    # drift, `check_layering.py` and `check_folder_structure.py` would disagree about
    # what "known" means -- exactly the two-copies defect `gate_common.py` warns
    # `source_root()` about, aimed at this gate's own direction matrix.
    if frozenset(ALLOWED) != KNOWN_SOURCE_LAYERS:
        return refuse(
            f"ALLOWED's keys {sorted(ALLOWED)} and KNOWN_SOURCE_LAYERS "
            f"{sorted(KNOWN_SOURCE_LAYERS)} have drifted; the two gates would disagree "
            "about what a known top-level layer is"
        )

    packages = source_roots()
    if len(packages) < MIN_PACKAGES:
        return refuse(
            f"{len(packages)} package(s) under {PACKAGE_ROOT / 'src'}, floor is "
            f"{MIN_PACKAGES}. A glob that matched nothing is a broken scan, not a clean tree."
        )

    # --- both-direction control, BEFORE any verdict on production ---------
    conforming = _fixture_arm("conforming")
    offending = _fixture_arm("offending")
    if conforming is None or offending is None:
        return refuse(
            f"a gate fixture is missing under {FIXTURE_ROOT}; a detector that has never "
            "been shown to fire cannot report a clean tree"
        )
    if len(conforming[0]) != EXPECTED_CONFORMING_FINDINGS:
        return refuse(
            f"the CONFORMING fixture produced {len(conforming[0])} finding(s), expected "
            f"{EXPECTED_CONFORMING_FINDINGS}: {conforming[0]}. A false red names a real file "
            "and invites 'fixing' code that is already correct."
        )
    if len(offending[0]) != EXPECTED_OFFENDING_FINDINGS:
        return refuse(
            f"the OFFENDING fixture produced {len(offending[0])} finding(s), expected the "
            f"hand-written {EXPECTED_OFFENDING_FINDINGS}: {offending[0]}"
        )
    control_problems = (
        _location_control_failures() + _unrecognised_layout_arm() + _relative_import_arm()
    )
    if control_problems:
        return refuse(
            f"the layer-location or import-resolution clause failed its own control(s): "
            f"{control_problems}. "
            f"A floor that has never been shown to fire cannot certify a scan"
        )
    marked = marker_count()
    if marked != EXPECTED_MARKED_FIXTURE_FILES:
        return refuse(
            f"{marked} offending fixture file(s) carry the marker, expected the hand-written "
            f"{EXPECTED_MARKED_FIXTURE_FILES}; the control has been neutralised"
        )
    stray = unmarked_flagged_files(FIXTURE_ROOT / "offending" / "pkg", offending[0])
    if stray:
        return refuse(
            f"the gate flagged unmarked fixture file(s) {stray}; the fixture has drifted, so "
            "the hand-written count above is measuring something nobody planted"
        )

    # --- the fixture exclusion, asserted in BOTH directions ---------------
    production = [f for package in packages for f in python_sources(package)]
    leaked = [p for p in production if FIXTURE_ROOT in p.parents]
    if leaked:
        return refuse(f"gate fixtures leaked into the production population: {leaked}")
    if not list(FIXTURE_ROOT.rglob("*.py")):
        return refuse(
            "the fixture tree holds no .py file, so the exclusion above excluded nothing "
            "and is credited for an absence it did not cause"
        )

    # The service-owned carve-outs. A widening that names a package cannot live in
    # a byte-identical kit -- the moment this file contains `foundry_agent_runtime`
    # it is nine copies that happen to agree today -- and a per-service FORK of the
    # ALLOWED matrix is the shape this whole bead exists to prevent. So a service
    # DECLARES the exception, keyed by (package, path), and the kit reads it.
    entries, problems = load_package_roles()
    problems += validate_package_roles(entries, packages)
    if problems:
        return refuse(
            f"the package registry is malformed or has rotted: {problems}. A carve-out "
            "naming a file that is gone reads as review and is not."
        )

    findings: list[str] = []
    modules = cross = unclassified = 0
    located: set[str] = set()
    per_package: list[str] = []
    for package in packages:
        carve_out = {
            path: {token.strip() for token in modules_allowed.split(",") if token.strip()}
            for path, modules_allowed in entries.get(package.name, {})
            .get("carve_out", {})
            .items()
        }
        known = KNOWN_SOURCE_LAYERS | declared_extra_layers(package.name, entries)
        (
            package_findings,
            package_modules,
            package_cross,
            package_located,
            package_unclassified,
        ) = analyse(package, carve_out=carve_out, known=known)
        findings += [f"{package.name}/{item}" for item in package_findings]
        modules += package_modules
        cross += package_cross
        located |= package_located
        unclassified += package_unclassified
        per_package.append(
            f"{package.name} {len(package_located)}/{len(ALLOWED)}"
            + (f" ({package_unclassified} unjudged)" if package_unclassified else "")
        )
    scanned = ", ".join(p.name for p in packages)
    # The coverage clause is built ONCE and printed on BOTH exits. check_folder_structure
    # printed its package accounting only on PASS, so on a FAIL nobody could tell whether it
    # had read one package or three -- which is what forced a wrapper to be written around it
    # to answer that question.
    coverage = (
        f"located {len(located)}/{len(ALLOWED)} declared layer(s) {sorted(located)}, "
        f"{unclassified} module(s) in unrecognised directories; per package: "
        f"{'; '.join(per_package)}"
    )
    if modules < MIN_PRODUCTION_MODULES:
        return refuse(
            f"only {modules} module(s) examined under {scanned}, floor is "
            f"{MIN_PRODUCTION_MODULES}; an empty scan reports a clean tree it never read"
        )
    if cross < MIN_CROSS_SUBMODULE_IMPORTS:
        return refuse(
            f"only {cross} cross-submodule import(s) resolved, floor is "
            f"{MIN_CROSS_SUBMODULE_IMPORTS}; the direction rule may be matching nothing"
        )
    blind = location_problem(located, unclassified, modules)
    if blind is not None:
        return refuse(f"{blind}. Scanned: {scanned}. {coverage}")
    if findings:
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        return finding(f"{len(findings)} layering violation(s) in {scanned}. {coverage}")
    print(
        f"PASS: {len(packages)} package(s), {modules} module(s), "
        f"{cross} cross-submodule import(s) resolved, "
        f"5 rules x 2 fixture arms = 10 arms; 10 executed, 0 skipped, 0 unaccounted. "
        f"Import-resolution control: {len(RELATIVE_IMPORT_CASES)} spellings, "
        f"{sum(1 for _, n in RELATIVE_IMPORT_CASES if n)} flagged and "
        f"{sum(1 for _, n in RELATIVE_IMPORT_CASES if not n)} quiet. "
        f"{coverage}."
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
