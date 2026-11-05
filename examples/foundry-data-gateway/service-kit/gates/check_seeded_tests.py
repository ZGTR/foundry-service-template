#!/usr/bin/env python3
"""S13/S14 gate: seeded-fixture API suites and e2e journeys.

Exit codes match the six §9 gates:
    0 green | 1 finding | 2 refusal | 3 named skip

Checks (docs/testing-framework.md §7):

  1. tests/{seeded,e2e,fixtures,support} exist.
  2. Every class under tests/seeded/ declares a non-empty ``seed_sources``.
  3. Every path named in a ``seed_sources`` resolves under tests/fixtures/.
  4. Every fixture file is named by at least one ``seed_sources`` (an orphan
     fixture is a lie about the corpus).
  5. Every fixture parses and every block carries target_class+data, or a
     known ``resource`` envelope.
  6. Every seeded class covers EC1..EC14 by test-id prefix, or carries an
     ``N/A:`` line with a reason for each missing code.
  7. At least one e2e journey exists whose assertions use a value taken from
     a PRIOR RESPONSE (the §5 seam rule) rather than only from the fixture.

The gate is AST-driven, never regex over source: a ``seed_sources`` spelled
across two lines, or a class whose body is generated, must not read as absent
— an absence claim from a weaker instrument is the failure mode the harness
verification rules exist to prevent.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

GREEN, FINDING, REFUSAL, SKIP = 0, 1, 2, 3

UNIVERSAL_CODES = [f"ec{n}" for n in range(1, 15)]

REQUIRED_DIRS = ("seeded", "e2e", "fixtures", "support")
KNOWN_RESOURCES = {"cache", "objectstore", "queue"}


@dataclass
class Report:
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def find(self, message: str) -> None:
        self.findings.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)


@dataclass
class SeededClass:
    name: str
    path: Path
    lineno: int
    sources: list[str]
    test_names: list[str]
    na_codes: set[str]


# --------------------------------------------------------------------------
# AST extraction
# --------------------------------------------------------------------------


def _module_constants(tree: ast.AST) -> dict[str, list[str]]:
    """Module-level `NAME = ["a", "b"]` bindings, so a SHARED corpus resolves.

    Declaring the corpus once as a module constant and pointing several classes
    at it is the normal shape — and the first version of this gate reported
    every such class as declaring nothing, which is a false RED on the
    convention the framework recommends. A gate whose failure mode is punishing
    correct code gets switched off.
    """
    constants: dict[str, list[str]] = {}
    for node in getattr(tree, "body", []):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        literal = _string_list(value)
        if literal is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = literal
    return constants


def _string_list(node: ast.AST, constants: dict[str, list[str]] | None = None) -> list[str] | None:
    constants = constants or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_list(node.left, constants)
        right = _string_list(node.right, constants)
        return None if left is None or right is None else left + right
    if isinstance(node, (ast.List, ast.Tuple)):
        out: list[str] = []
        for element in node.elts:
            resolved = _string_list(element, constants)
            if resolved is None:
                return None  # non-literal entry: cannot verify, treat as unknown
            out.extend(resolved)
        return out
    return None


def _na_codes_from_comments(source: str) -> set[str]:
    """Codes explicitly waived with a reason, e.g. ``# EC12 N/A: bounded set``.

    A bare ``# EC12 N/A`` does NOT count.  The reason is the whole value of the
    waiver: it is what a reviewer reads to decide the waiver is still true.
    """
    codes: set[str] = set()
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        upper = stripped.upper()
        if "N/A:" not in upper:
            continue
        for code in UNIVERSAL_CODES:
            token = code.upper()
            if token in upper and upper.index(token) < upper.index("N/A:"):
                codes.add(code)
    return codes


def collect_seeded_classes(seeded_dir: Path) -> tuple[list[SeededClass], list[str]]:
    classes: list[SeededClass] = []
    errors: list[str] = []
    for path in sorted(seeded_dir.rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path}: unparseable ({exc})")
            continue
        na_codes = _na_codes_from_comments(source)
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not node.name.startswith("Test"):
                continue
            sources: list[str] | None = None
            tests: list[str] = []
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == "seed_sources":
                            sources = _string_list(item.value, constants)
                elif isinstance(item, ast.AnnAssign):
                    target = item.target
                    if isinstance(target, ast.Name) and target.id == "seed_sources" and item.value:
                        sources = _string_list(item.value, constants)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("test"):
                        tests.append(item.name)
            classes.append(
                SeededClass(
                    name=node.name,
                    path=path,
                    lineno=node.lineno,
                    sources=sources if sources is not None else [],
                    test_names=tests,
                    na_codes=na_codes,
                )
            )
    return classes, errors


def collect_all_seed_sources(tests_dir: Path) -> set[str]:
    """Every ``seed_sources`` entry anywhere under tests/ (seeded AND e2e)."""
    found: set[str] = set()
    for path in sorted(tests_dir.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "seed_sources":
                        found.update(_string_list(node.value, constants) or [])
            elif isinstance(node, ast.AnnAssign):
                target = node.target
                if isinstance(target, ast.Name) and target.id == "seed_sources" and node.value:
                    found.update(_string_list(node.value, constants) or [])
    return found


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def classify_resource(block: dict) -> str | None:
    """Which non-database resource a block fills, from its SHAPE.

    The contract's §7 writes these envelopes WITHOUT a discriminator, and the
    service template's fixtures predate this gate. Requiring an explicit
    `"resource"` key would have this gate reject fixtures the contract itself
    specifies -- which it did, on first run against the template. An explicit
    key is accepted and wins when present.
    """
    declared = block.get("resource")
    if declared in KNOWN_RESOURCES:
        return str(declared)
    keys = set(block)
    if {"entries"} <= keys:
        return "cache"
    if {"bucket", "objects"} <= keys:
        return "objectstore"
    if {"queue", "messages"} <= keys:
        return "queue"
    return None


def check_fixture_payload(path: Path, report: Report, *, seeded: bool = True) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.find(f"fixture {path}: invalid JSON ({exc})")
        return

    if not seeded:
        return  # parsed, which is the whole claim for a non-corpus fixture
    blocks = payload if isinstance(payload, list) else [payload]
    if not isinstance(blocks, list):
        report.find(f"fixture {path}: top level must be a block or list of blocks")
        return
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            report.find(f"fixture {path} block {index}: not an object")
            continue
        if "target_class" in block:
            if ":" not in str(block["target_class"]):
                report.find(
                    f"fixture {path} block {index}: target_class must be 'module:Class'"
                )
            if not isinstance(block.get("data"), list):
                report.find(f"fixture {path} block {index}: 'data' must be a list")
            continue
        if classify_resource(block) is None:
            report.find(
                f"fixture {path} block {index}: neither 'target_class' nor a "
                f"recognisable resource envelope "
                f"({'|'.join(sorted(KNOWN_RESOURCES))}); got {sorted(block)}"
            )


def check_edge_codes(entry: SeededClass, report: Report) -> None:
    covered = {
        code
        for code in UNIVERSAL_CODES
        for name in entry.test_names
        if name.startswith(f"test_{code}_") or f"_{code}_" in name
    }
    missing = [c for c in UNIVERSAL_CODES if c not in covered and c not in entry.na_codes]
    if missing:
        report.find(
            f"{entry.path}:{entry.lineno} {entry.name}: edge codes not covered and not "
            f"waived with a reason: {', '.join(c.upper() for c in missing)}"
        )


def check_e2e_seam(e2e_dir: Path, report: Report) -> None:
    """§5: a journey's later step must consume an EARLIER RESPONSE.

    Heuristic, and deliberately a conservative one: a journey test that never
    binds a name out of a response and then feeds it onward is three seeded
    tests in a trench coat.  Proving the dataflow properly needs taint
    analysis; what this checks is that at least one test assigns from a
    response accessor and later references that name.

    MEASURED LIMITATION, recorded rather than left for the next reader to hit.
    `_reads_response` matches any call to an attribute named `json` or
    `headers`, so a test HELPER called `self.headers(...)` reads as
    response-derived. On workflow-service, stripping the real seam out
    of the journeys left this check GREEN for exactly that reason. It fires
    correctly on a journey that genuinely has no seam -- which is the case it
    exists for -- but it can be masked by a helper sharing those names. Renaming
    the helper is not the fix: the fix, if this ever matters, is taint analysis,
    and until then the mutation prover is the instrument that actually proves a
    journey bites.
    """
    journeys = sorted(e2e_dir.rglob("test_*.py"))
    if not journeys:
        report.find(f"{e2e_dir}: no e2e journey modules (S14)")
        return

    for path in journeys:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            report.find(f"{path}: unparseable ({exc})")
            continue
        if _module_has_seam(tree):
            return
    report.find(
        f"{e2e_dir}: no journey feeds a value from one step's RESPONSE into a later "
        f"step (§5 seam rule); every test reads all its inputs from the fixture"
    )


_RESPONSE_ACCESSORS = {"json", "get_json", "headers"}


def _module_has_seam(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        bound: set[str] = set()
        for statement in ast.walk(node):
            if isinstance(statement, ast.Assign) and _reads_response(statement.value):
                for target in statement.targets:
                    bound.update(_names_bound(target))
        if not bound:
            continue
        # A bound response value must then be USED somewhere that is not its
        # own binding statement — otherwise the journey read it and dropped it.
        for statement in ast.walk(node):
            if isinstance(statement, ast.Name) and statement.id in bound:
                if isinstance(statement.ctx, ast.Load):
                    return True
    return False


def _names_bound(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for element in target.elts:
            out |= _names_bound(element)
        return out
    return set()


def _reads_response(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in _RESPONSE_ACCESSORS:
            return True
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute) and func.attr in _RESPONSE_ACCESSORS:
                return True
    return False


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _filenames_referenced_under(tests: Path) -> set[str]:
    """Fixture filenames mentioned anywhere in the test tree.

    Deliberately a plain filename search rather than a path resolution: a test
    may build the path from `fixture_dir / "widgets.json"`, from a constant, or
    from a loop, and a gate that only understood one of those would report the
    other two as orphans.
    """
    names: set[str] = set()
    for path in tests.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for token in re.findall(r"[\w./-]+\.json", text):
            names.add(Path(token).name)
    return names


def run(repo: Path) -> tuple[int, Report]:
    report = Report()
    tests = repo / "tests"
    if not tests.is_dir():
        report.find(f"{repo}: no tests/ directory")
        return REFUSAL, report

    missing_dirs = [d for d in REQUIRED_DIRS if not (tests / d).is_dir()]
    if missing_dirs:
        for name in missing_dirs:
            report.find(f"{tests / name}: required test directory missing (S13/S14)")
        return FINDING, report

    seeded_dir, e2e_dir, fixtures_dir = tests / "seeded", tests / "e2e", tests / "fixtures"

    classes, errors = collect_seeded_classes(seeded_dir)
    for error in errors:
        report.find(error)
    if not classes:
        report.find(f"{seeded_dir}: no seeded test classes (S13)")

    for entry in classes:
        if not entry.sources:
            report.find(
                f"{entry.path}:{entry.lineno} {entry.name}: declares no seed_sources; "
                f"a class under tests/seeded/ without a corpus belongs in tests/unit/"
            )
            continue
        for source in entry.sources:
            if not (fixtures_dir / f"{source}.json").is_file():
                report.find(
                    f"{entry.path}:{entry.lineno} {entry.name}: seed_sources names "
                    f"{source!r} but {fixtures_dir / (source + '.json')} does not exist"
                )
        check_edge_codes(entry, report)

    declared = collect_all_seed_sources(tests)
    referenced = _filenames_referenced_under(tests)
    for fixture in sorted(fixtures_dir.rglob("*.json")):
        relative = fixture.relative_to(fixtures_dir).with_suffix("").as_posix()
        # A fixture named by NO seed_sources and mentioned by NO test file is an
        # orphan. Checking only `seed_sources` was too strict: the service
        # template loads its cache and object-store fixtures by PATH from
        # `tests/integration`, which is a legitimate use this gate does not get
        # to veto. A corpus nobody reads at all is still a lie about coverage.
        if relative not in declared and fixture.name not in referenced:
            report.find(
                f"{fixture}: orphan fixture — no seed_sources names it and no test "
                f"mentions it. A corpus nobody reads is a lie about what the suite covers."
            )
        # Parsing is universal -- unreadable JSON under tests/fixtures/ is a
        # finding whatever reads it. The SEED ENVELOPE is required only of a
        # corpus some `seed_sources` actually names. Measured on
        # workflow-service, whose tests/fixtures/ holds infrastructure
        # config (a grype scan, an haproxy conf, an otel collector yaml) that no
        # seeder ever loads: demanding a target_class of those files made the
        # gate red on a repo that was doing nothing wrong.
        check_fixture_payload(fixture, report, seeded=relative in declared)

    check_e2e_seam(e2e_dir, report)

    report.note(f"seeded classes: {len(classes)}")
    report.note(f"fixtures: {len(list(fixtures_dir.rglob('*.json')))}")
    report.note(f"declared sources: {len(declared)}")
    return (FINDING if report.findings else GREEN), report


def main(argv: list[str]) -> int:
    repo = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    code, report = run(repo)
    for note in report.notes:
        print(f"  {note}")
    for finding in report.findings:
        print(f"FINDING: {finding}")
    print("check_seeded_tests: " + ("GREEN" if code == GREEN else f"exit {code}"))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
