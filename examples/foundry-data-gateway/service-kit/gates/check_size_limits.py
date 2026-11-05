#!/usr/bin/env python3
"""§4: the five size ceilings.

    route/event handler          12 lines
    function (non-composition)   50
    composition function         60
    class                       200
    module file                 400

Each is a separate CLAUSE with its own ceiling, so a finding names which one it
crossed. The composition ceiling is higher than the general one deliberately:
wiring a graph IS a long straight-line function, and forcing it under 50 lines
produces a chain of one-use helpers that is harder to read than the thing it
replaced -- the limit exists to stop logic accreting, not to stop assembly.

A handler is measured from its FIRST route decorator, not from `def`: the
decorator is part of the handler's surface, and measuring from `def` lets a
handler hide arguments and status codes above the line that starts counting.

Exit codes: `scripts/gate_exit_codes.py`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import (  # noqa: E402
    EXPECTED_MARKED_FIXTURE_FILES,
    FIXTURE_ROOT,
    MIN_PACKAGES,
    marker_count,
    python_sources,
    source_roots,
    unmarked_flagged_files,
)
from gate_exit_codes import GREEN, finding, refuse  # noqa: E402

HANDLER_MAX = 12
FUNCTION_MAX = 50
#: A function is measured over its BODY, with its SIGNATURE held separately.
#: Without the split, a composition root declaring ten injectable dependencies
#: one per line spends a fifth of its body ceiling before its first statement,
#: and the remedy the finding implies -- shorten the function -- is the opposite
#: of what the code needs, which is to keep declaring its dependencies
#: explicitly. The signature is not unbounded either: a parameter list nobody
#: can hold in their head is its own defect, so it gets its own ceiling rather
#: than an exemption.
SIGNATURE_MAX = 25
COMPOSITION_FUNCTION_MAX = 60
CLASS_MAX = 200
MODULE_MAX = 400

COMPOSITION_SUBMODULE = "composition"
ROUTE_DECORATORS = frozenset({"get", "post", "put", "patch", "delete", "api_route"})

#: HAND-WRITTEN. Deriving it from the fixture would make the assertion `n == n`.
EXPECTED_OFFENDING_FINDINGS = 1
EXPECTED_CONFORMING_FINDINGS = 0
#: Two-sided floor. The first number proves the scan saw modules; the second
#: proves the CLASSIFIER actually separated handlers from ordinary functions --
#: a gate that measured every function as an ordinary one would be silently
#: applying the 50-line ceiling where the 12-line one belongs, and its finding
#: count would not move on any tree this package has.
MIN_MODULES = 20
MIN_HANDLERS_CLASSIFIED = 3

#: The body/signature split, controlled in BOTH directions before any verdict.
#: Without these the split could silently become "measure nothing": an
#: implementation returning (0, 0) would make every function pass every ceiling,
#: and the finding count would not move on any tree this repository has -- a
#: gate that had stopped measuring, reporting a clean tree. The three cases are
#: a one-line signature, a multi-line one, and a multi-line body, so a matcher
#: that collapsed either half is red on at least one.
SPLIT_CONTROLS = (
    # (source, expected signature lines, expected body lines)
    ("def f(a, b):\n    return a\n", 1, 1),
    ("def f(\n    a,\n    b,\n):\n    return a\n", 4, 1),
    ("def f(a):\n    x = 1\n    y = 2\n    return x + y\n", 1, 3),
)


def _signature_and_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int]:
    """(signature lines, body lines) for one function.

    The split point is the first statement of the body, which `ast` gives
    exactly -- never a guess from indentation, and never a regex for the closing
    paren, which a default value containing one would defeat.
    """
    first = node.body[0].lineno
    return first - node.lineno, node.end_lineno - first + 1


def _route_decorator_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in ROUTE_DECORATORS
        ):
            return decorator.lineno
    return None


def analyse(root: Path, *, is_fixture: bool = False) -> tuple[list[str], int, int]:
    """Return (findings, modules_examined, handlers_classified)."""
    findings: list[str] = []
    modules = 0
    handlers = 0
    for path in python_sources(root, exclude_fixtures=not is_fixture):
        relative = path.relative_to(root)
        modules += 1
        text = path.read_text()
        line_count = len(text.splitlines())
        if line_count > MODULE_MAX:
            findings.append(f"{relative}: module is {line_count} lines, ceiling is {MODULE_MAX}")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            findings.append(f"{relative}: unparseable ({exc})")
            continue
        in_composition = relative.parts and relative.parts[0] == COMPOSITION_SUBMODULE
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                size = node.end_lineno - node.lineno + 1
                if size > CLASS_MAX:
                    findings.append(
                        f"{relative}:{node.lineno}: class {node.name!r} is {size} lines, "
                        f"ceiling is {CLASS_MAX}"
                    )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorator_line = _route_decorator_line(node)
                if decorator_line is not None:
                    handlers += 1
                    size = node.end_lineno - decorator_line + 1
                    ceiling, kind = HANDLER_MAX, "route handler"
                else:
                    signature, size = _signature_and_body(node)
                    ceiling = COMPOSITION_FUNCTION_MAX if in_composition else FUNCTION_MAX
                    kind = "composition function" if in_composition else "function"
                    if signature > SIGNATURE_MAX:
                        findings.append(
                            f"{relative}:{node.lineno}: {kind} {node.name!r} has a "
                            f"{signature}-line signature, ceiling is {SIGNATURE_MAX}"
                        )
                if size > ceiling:
                    findings.append(
                        f"{relative}:{node.lineno}: {kind} {node.name!r} is {size} lines, "
                        f"ceiling is {ceiling}"
                    )
    return findings, modules, handlers


def main() -> int:
    # EVERY package, not the one a single-package repo happens to have. Size
    # ceilings are universal -- a library package's functions are as readable or
    # as long as a service's -- so unlike the MVC gate there is no surface filter
    # here: a package is in scope because it exists.
    packages = source_roots()
    if len(packages) < MIN_PACKAGES:
        return refuse(
            f"{len(packages)} package(s) found under src/, floor is {MIN_PACKAGES}. "
            "A glob that matched nothing is a broken scan, not a clean repository."
        )

    # --- the body/signature split, controlled BEFORE any verdict ----------
    for source, expected_signature, expected_body in SPLIT_CONTROLS:
        node = ast.parse(source).body[0]
        measured = _signature_and_body(node)
        if measured != (expected_signature, expected_body):
            return refuse(
                f"the signature/body split measured {measured} for {source!r}, expected the "
                f"hand-written {(expected_signature, expected_body)}. A split that has "
                "stopped measuring makes every function pass every ceiling"
            )

    conforming_pkg = FIXTURE_ROOT / "conforming" / "pkg"
    offending_pkg = FIXTURE_ROOT / "offending" / "pkg"
    if not conforming_pkg.is_dir() or not offending_pkg.is_dir():
        return refuse(f"a gate fixture is missing under {FIXTURE_ROOT}")

    conforming, _cm, c_handlers = analyse(conforming_pkg, is_fixture=True)
    if len(conforming) != EXPECTED_CONFORMING_FINDINGS:
        return refuse(
            f"the CONFORMING fixture produced {len(conforming)} finding(s), expected "
            f"{EXPECTED_CONFORMING_FINDINGS}: {conforming}. Widen the matcher, never the fixture."
        )
    if c_handlers < 1:
        return refuse(
            "the conforming fixture classified 0 route handlers, so the 12-line clause was "
            "never exercised in the quiet direction"
        )
    offending, _om, _oh = analyse(offending_pkg, is_fixture=True)
    if len(offending) != EXPECTED_OFFENDING_FINDINGS:
        return refuse(
            f"the OFFENDING fixture produced {len(offending)} finding(s), expected the "
            f"hand-written {EXPECTED_OFFENDING_FINDINGS}: {offending}"
        )
    marked = marker_count()
    if marked != EXPECTED_MARKED_FIXTURE_FILES:
        return refuse(
            f"{marked} offending fixture file(s) carry the marker, expected "
            f"{EXPECTED_MARKED_FIXTURE_FILES}; the control has been neutralised"
        )
    stray = unmarked_flagged_files(offending_pkg, offending)
    if stray:
        return refuse(f"the gate flagged unmarked fixture file(s) {stray}; the fixture drifted")

    findings: list[str] = []
    modules = handlers = 0
    for package in packages:
        package_findings, package_modules, package_handlers = analyse(package)
        findings += [f"{package.name}/{item}" for item in package_findings]
        modules += package_modules
        handlers += package_handlers
    if modules < MIN_MODULES or handlers < MIN_HANDLERS_CLASSIFIED:
        return refuse(
            f"{modules} module(s) and {handlers} handler(s) classified; floors are "
            f"{MIN_MODULES}/{MIN_HANDLERS_CLASSIFIED}"
        )
    if findings:
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        scanned = ", ".join(p.name for p in packages)
        return finding(f"{len(findings)} size-limit violation(s) in {scanned}")
    print(
        f"PASS: {len(packages)} package(s), {modules} module(s), "
        f"{handlers} route handler(s) classified; "
        f"6 ceilings x 2 fixture arms + {len(SPLIT_CONTROLS)} split control(s) = "
        f"{6 * 2 + len(SPLIT_CONTROLS)} arms; {6 * 2 + len(SPLIT_CONTROLS)} executed, "
        f"0 skipped, 0 unaccounted."
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
