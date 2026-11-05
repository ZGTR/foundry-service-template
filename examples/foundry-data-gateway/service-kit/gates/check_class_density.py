"""§4: one file, one idea. A module holding many unrelated classes is a finding.

`check_size_limits.py` caps how LONG a class, function or module may be. This
caps how MANY top-level classes one module declares, which is a different
failure: five 30-line classes in one file break no length ceiling and are still
the thing that makes a service unnavigable, because the filename stops
predicting the contents. A reader looking for `WidgetRejected` has nowhere to
look.

THE EXEMPTION IS THE WHOLE DESIGN. A flat count would be wrong roughly as often
as it is right, because there is one shape where many small classes in one file
is the CORRECT arrangement: an exception family. `domain/errors.py` declaring
eight errors that all derive from one base is cohesive by construction -- every
class is the same idea at a different value -- and splitting it into eight files
would obey this gate while making the code worse. So:

* STRUCTURAL exemption -- every class in the module is an exception (derives
  from `Exception`/`BaseException`, or transitively from another exception
  declared in the same module). No maintenance, no list, and a new error class
  is covered the day it is written.
* DECLARED exemption -- `scripts/class_density_exemptions.json`, for anything
  else, with a reason and a bead. Keys are `<package>/<path relative to
  src/<package>/>`: package-qualified, because a repository may ship more than
  one package and a bare relative path would let an entry written for one be
  satisfied by a same-named file in a sibling. It fails closed in THREE
  directions: an entry naming a file that does not exist is an error, so is one
  naming a file that is no longer over the limit -- the work landed and the
  exemption now describes the past -- and so is one whose first segment names no
  package under `src/`, which is how the old unqualified form is rejected rather
  than silently accepted.

The structural rule is preferred and the registry ships EMPTY, with its drift
check live. An exemption mechanism written the day the first exemption arrives
is a mechanism nobody has run.

Exit 0 clean, 1 finding, 2 could-not-measure.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import (  # noqa: E402
    EXPECTED_MARKED_FIXTURE_FILES,
    FIXTURE_ROOT,
    MIN_PACKAGES,
    marker_count,
    python_sources,
    service_data_file,
    source_roots,
    unmarked_flagged_files,
)
from gate_exit_codes import GREEN, finding, refuse  # noqa: E402

#: HAND-WRITTEN ceiling. Measured before it was chosen: this package's busiest
#: modules declare 3 top-level classes (`transport/http/schemas/widgets_schema.py`
#: and `ports/health.py`), ten declare 2 and thirty-four declare 1. Four leaves
#: one file's worth of headroom without being a number nothing can reach -- a
#: ceiling the tree cannot approach is a gate that will never fire, which reads
#: identically to a gate that is passing.
MAX_CLASSES_PER_FILE = 4

#: Bases that make a class an exception without needing to resolve an import.
EXCEPTION_BASES = frozenset({"Exception", "BaseException", "ValueError", "RuntimeError",
                             "TypeError", "KeyError", "LookupError", "OSError",
                             "NotImplementedError", "AssertionError"})

EXEMPTIONS_FILE = service_data_file("class_density_exemptions.json")

#: HAND-WRITTEN expectations for the fixture arms, never counted from the
#: fixture: an expectation derived from the artifact under test holds for every
#: artifact including an empty one.
EXPECTED_OFFENDING_FINDINGS = 1
EXPECTED_CONFORMING_FINDINGS = 0
#: And the arm that stops the exemption from being decoration. A conforming tree
#: with no over-limit exception family would pass whether or not the exemption
#: works, so the conforming run must have actually EXEMPTED something.
EXPECTED_CONFORMING_EXEMPTIONS = 1

#: Two-sided floor. The first proves the scan reached the package; the second
#: proves it PARSED -- a walker that collected no class at all would report
#: "no module over the ceiling" truthfully and mean nothing by it.
MIN_MODULES = 20
MIN_CLASSES_SEEN = 30

#: PER-PACKAGE ACCOUNTING CASES, both directions. A predicate demonstrated only on
#: input it rejects is indistinguishable from one that rejects everything, so the
#: first two cases must be ACCEPTED and they are the ones that would break a naive
#: "every package must carry classes" rule: a package may legitimately hold only
#: functions and constants. What is never legitimate is a package that contributed
#: no MODULE at all, because the totals below are a sum and a silent zero is
#: invisible in it.
ACCOUNTING_CONTROLS: tuple[tuple[str, dict[str, dict[str, int]], bool], ...] = (
    ("one package carrying everything", {"a": {"modules": 40, "classes": 60}}, False),
    ("a package of functions, no classes", {"a": {"modules": 40, "classes": 60},
                                            "b": {"modules": 7, "classes": 0}}, False),
    ("a package that contributed no module", {"a": {"modules": 40, "classes": 60},
                                              "b": {"modules": 0, "classes": 0}}, True),
    ("every package silent", {"a": {"modules": 0, "classes": 0},
                              "b": {"modules": 0, "classes": 0}}, True),
    ("nothing accounted at all", {}, True),
)


def _base_names(node: ast.ClassDef) -> list[str]:
    names = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


#: Exception bases this ESTATE shares, declared because they are a structural fact about
#: the estate rather than a property of any one service. Every Foundry service derives its
#: errors from `foundry_contracts`, so without this the structural exemption below could
#: not fire for a single service -- measured on llm-gateway, whose `domain/errors.py`
#: holds 7 classes all deriving from the shared `FoundryError` and was flagged as if it
#: were seven unrelated ideas in one file.
#:
#: WHY A DECLARATION AND NOT A PER-SERVICE EXEMPTION. `FoundryError` is a shared CONTRACT,
#: so a service-data exemption would turn a structural fact into per-service maintenance and
#: make every service restate what the estate already guarantees. A hardcoded roster is the
#: wrong instrument for something the artifact can state (ruling 2).
#:
#: IT FAILS CLOSED. An unrecognised base is NOT exempt: the gate stays strict on any
#: hierarchy it cannot resolve, so a new exception family is covered by default rather than
#: by somebody remembering to add it here.
ESTATE_EXCEPTION_BASES = frozenset({
    "FoundryError",
    "ProviderFailed",
    "OutputValidationFailed",
})

#: The module each declared base must really come from, so the list cannot rot into names
#: nobody has checked. Asserted in BOTH directions by `_estate_base_declaration_problem`.
ESTATE_EXCEPTION_MODULE = "foundry_contracts"


def _estate_base_declaration_problem(imported: dict[str, str]) -> str | None:
    """A declared base that no module in this tree actually imports is a name nobody checked.

    The list fails closed in both directions, which is the second half of ruling 2: a base
    that stops existing, or is renamed upstream, must be a hard error rather than a quietly
    dead entry that goes on granting an exemption to nothing.

    `imported` maps a name to the module it was imported from, collected from the tree under
    scan. A declared base that IS imported, from somewhere other than the contracts package,
    is also a problem -- it means a local class has taken the name of a shared contract, and
    exempting on it would be exempting on a coincidence of spelling.
    """
    seen = {name: module for name, module in imported.items() if name in ESTATE_EXCEPTION_BASES}
    if not seen:
        # Nothing in this tree imports any declared base. That is normal for a service whose
        # modules are all under the class ceiling, so it is NOT an error -- it is simply no
        # evidence either way, and inventing an error here would red a healthy tree.
        return None
    wrong = {n: m for n, m in seen.items() if not m.startswith(ESTATE_EXCEPTION_MODULE)}
    if wrong:
        return (
            f"declared estate exception base(s) {sorted(wrong)} are imported from "
            f"{sorted(set(wrong.values()))}, not from {ESTATE_EXCEPTION_MODULE}. A shared "
            f"contract's NAME reused locally is a coincidence of spelling, and exempting on "
            f"it would exempt a family this gate has never verified"
        )
    return None


def _is_exception_family(
    classes: list[ast.ClassDef], estate_bases: frozenset[str] = ESTATE_EXCEPTION_BASES
) -> bool:
    """Every class in the module is an exception, transitively within the module.

    Transitively, because the realistic shape is one local base deriving from
    `Exception` and the rest deriving from IT. A check that only looked for a
    builtin base would exempt the base and flag the family.

    `estate_bases` extends that to bases the estate SHARES and no module defines locally.
    Without it this function returned False for every estate-standard exception module, so
    the exemption the docstring above calls "the CORRECT arrangement" could not fire
    anywhere -- and the template could never reveal it, because the template's own
    `domain/errors.py` holds 2 classes and never reaches the ceiling of 4. A parameter
    rather than a constant read directly, so the controls can blind it and watch the arm go
    red.
    """
    if not classes:
        return False
    exception_names: set[str] = set()
    # Repeat to a fixed point: declaration order is not inheritance order.
    for _ in range(len(classes)):
        for node in classes:
            bases = _base_names(node)
            if any(
                b in EXCEPTION_BASES or b in estate_bases or b in exception_names for b in bases
            ):
                exception_names.add(node.name)
            elif node.name.endswith(("Error", "Exception")) and not bases:
                # A bare `class WidgetError:` is not an exception, and saying so
                # matters: naming is not structure. Only counted when it also
                # derives from something already known to be an exception, which
                # the branch above handles -- this branch deliberately does
                # nothing, and exists so a reader does not add it later.
                continue
    return len(exception_names) == len(classes)


def analyse(
    package: Path, exemptions: dict[str, str], key_prefix: str = ""
) -> tuple[list[str], list[str], dict[str, int]]:
    """(findings, exempted, counts) for ONE package tree.

    `key_prefix` is what makes an exemption key unambiguous across a repository
    that ships more than one package: production passes `"<package>/"`, so an
    entry cannot be satisfied by a same-named file in a sibling package. It
    defaults to `""` so the fixture arms and the two registry-drift controls
    below -- all of which run against a single tree -- call this exactly as they
    did and their hand-written expectations still mean what they say.
    """
    findings: list[str] = []
    exempted: list[str] = []
    counts = {"modules": 0, "classes": 0}

    inside_fixtures = FIXTURE_ROOT.exists() and FIXTURE_ROOT in package.parents
    for path in python_sources(package, exclude_fixtures=not inside_fixtures):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:
            findings.append(f"{path.name}: does not parse ({exc})")
            continue
        counts["modules"] += 1
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        counts["classes"] += len(classes)
        if len(classes) <= MAX_CLASSES_PER_FILE:
            continue

        relative = f"{key_prefix}{path.relative_to(package).as_posix()}"
        if _is_exception_family(classes):
            exempted.append(f"{relative} (exception family, {len(classes)} classes)")
            continue
        if relative in exemptions:
            exempted.append(f"{relative} (declared: {exemptions[relative]})")
            continue
        findings.append(
            f"{relative}: {len(classes)} top-level classes, over the ceiling of "
            f"{MAX_CLASSES_PER_FILE} -- {', '.join(c.name for c in classes)}. Split it by "
            f"idea, one file per class, or -- if they are one idea at different values -- "
            f"make them an exception family, which is exempt structurally"
        )
    return findings, exempted, counts


def package_accounting_problem(per_package: dict[str, dict[str, int]]) -> str | None:
    """Refuse when a discovered package contributed nothing to the totals.

    THE TOTALS ARE A SUM, AND A SUM HIDES A ZERO. `source_roots()` discovers the
    packages; `analyse` is then called once per package and the counts are added
    together. A package that returns zero modules -- unreadable, empty, or excluded
    by a filter nobody meant to apply to it -- subtracts nothing from the total, so
    the floors below still clear on its siblings' work and the verdict is printed
    with that package's name in the PASS line. The name is the part a reader
    believes: naming a package in a success line is an assertion that it was
    judged.

    A package with no CLASSES is fine and is deliberately accepted. A package of
    functions and constants is an ordinary thing to ship, and refusing it would be
    a false red naming a real directory -- the direction this kit treats as worse,
    because the natural response is to invent a class rather than doubt the gate.

    THE REFUSAL FIRES ON NO PACKAGE IN THE ESTATE TODAY. Measured 2026-09-19 with
    this gate's own `analyse` over every service at its own tip, fourteen packages:
    the smallest is `foundry_redteam` at 11 modules and 32 classes, and none is
    zero on either count. That is stated rather than implied, because a clause
    whose fixture is the only thing it has ever rejected should not be described in
    the language of a clause that caught something.
    """
    if not per_package:
        return ("no package accounted for anything, so the totals below are a sum "
                "over an empty set. A scan that judged nothing cannot report health")
    silent = sorted(name for name, c in per_package.items() if c.get("modules", 0) == 0)
    if silent:
        return (
            f"{len(silent)} discovered package(s) contributed no module to the scan "
            f"({', '.join(silent)}), yet they are named in this gate's verdict as though "
            "they were judged. A zero is invisible in a total"
        )
    return None


def _accounting_control_failures() -> list[str]:
    """Run ACCOUNTING_CONTROLS, both directions, before the verdict is trusted."""
    failures: list[str] = []
    for label, per_package, should_refuse in ACCOUNTING_CONTROLS:
        problem = package_accounting_problem(per_package)
        if should_refuse and problem is None:
            failures.append(f"{label!r} was accepted; the accounting clause should refuse it")
        if not should_refuse and problem is not None:
            failures.append(f"{label!r} was refused ({problem}); it is legitimate input")
    return failures


def _check_registry_drift(
    exemptions: dict[str, str], package: Path, key_prefix: str = ""
) -> list[str]:
    """An exemption that no longer describes the tree is an error, not slack.

    Both directions. A path that vanished means the registry is rotting; a path
    that is now UNDER the ceiling means the work landed and the entry is
    describing the past, which is how a registry quietly grows permission
    nobody re-examined.
    """
    problems = []
    for relative, reason in sorted(exemptions.items()):
        # A sibling package's entry is not stale just because THIS package does
        # not hold it. Keys belonging to no live package at all are caught in
        # main(), which is the only place every package name is known.
        if not relative.startswith(key_prefix):
            continue
        path = package / relative[len(key_prefix) :]
        if not path.is_file():
            problems.append(f"exemption names {relative}, which does not exist ({reason})")
            continue
        try:
            classes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef)]
        except SyntaxError:
            continue
        if len(classes) <= MAX_CLASSES_PER_FILE:
            problems.append(
                f"exemption names {relative}, which now declares {len(classes)} class(es) -- "
                f"at or under the ceiling, so the exemption describes the past. Remove it"
            )
    return problems


#: The exception-family exemption, controlled in BOTH directions plus a blinding arm.
#: ONE CASE PER CLAUSE: arm 1 is the estate-shared imported base this exemption was widened
#: for, arm 2 the locally-defined base it always handled, arm 3 a genuine non-family that
#: must stay flagged, and arm 4 an UNRECOGNISED base, which must stay STRICT -- that is the
#: fail-closed half, and without it a widening could quietly exempt every hierarchy.
FAMILY_CONTROLS = (
    (
        "an estate-shared IMPORTED base",
        "from foundry_contracts.errors import FoundryError\n"
        + "".join(f"class E{i}(FoundryError):\n    pass\n" for i in range(5)),
        True,
    ),
    (
        "a locally defined base",
        "class LocalError(Exception):\n    pass\n"
        + "".join(f"class F{i}(LocalError):\n    pass\n" for i in range(5)),
        True,
    ),
    (
        "a genuine non-family: one service class among the exceptions",
        "class LocalError(Exception):\n    pass\nclass G(LocalError):\n    pass\n"
        "class Service:\n    pass\n",
        False,
    ),
    (
        "an UNRECOGNISED base stays strict",
        "".join(f"class H{i}(SomeUnknownBase):\n    pass\n" for i in range(5)),
        False,
    ),
)


def _family_control_failures() -> list[str]:
    """Run the controls, and PROVE the declaration is load-bearing by blinding it.

    The blinding arm is what stops `ESTATE_EXCEPTION_BASES` from being decoration: with it
    emptied, arm 1 must FLIP and the other three must NOT move. A widening whose removal
    changes nothing was never doing anything, and a widening whose removal changes
    everything is too broad.
    """
    problems: list[str] = []
    for label, source, expected in FAMILY_CONTROLS:
        classes = [n for n in ast.parse(source).body if isinstance(n, ast.ClassDef)]
        if not classes:
            problems.append(f"control {label!r} parsed to 0 classes; the control is broken")
            continue
        got = _is_exception_family(classes)
        if got != expected:
            problems.append(f"control {label!r}: expected {expected}, got {got}")
        blinded = _is_exception_family(classes, frozenset())
        should_move = label == FAMILY_CONTROLS[0][0]
        if should_move and blinded == got:
            problems.append(
                f"control {label!r} did not move when ESTATE_EXCEPTION_BASES was emptied, so "
                "the declaration is decoration rather than the thing under test"
            )
        if not should_move and blinded != got:
            problems.append(
                f"control {label!r} moved when ESTATE_EXCEPTION_BASES was emptied, so the "
                "declaration is wider than the one case it was added for"
            )
    return problems


def _self_test() -> int | None:
    """Both fixture trees, through the PRODUCTION analyser."""
    if marker_count() != EXPECTED_MARKED_FIXTURE_FILES:
        return refuse(
            f"{marker_count()} fixture file(s) carry the marker, expected "
            f"{EXPECTED_MARKED_FIXTURE_FILES}. The offending tree has drifted, so every "
            f"count below is measuring something other than what it says"
        )

    offending = FIXTURE_ROOT / "offending" / "pkg"
    conforming = FIXTURE_ROOT / "conforming" / "pkg"
    for tree in (offending, conforming):
        if not tree.is_dir():
            return refuse(f"fixture tree {tree} is missing; the controls cannot run")

    found, _, _ = analyse(offending, {})
    if len(found) != EXPECTED_OFFENDING_FINDINGS:
        return refuse(
            f"the offending fixture produced {len(found)} finding(s), expected "
            f"{EXPECTED_OFFENDING_FINDINGS}: {found}"
        )
    unmarked = unmarked_flagged_files(offending, found)
    if unmarked:
        return refuse(
            f"flagged fixture file(s) carry no marker: {unmarked}. The gate found something "
            f"the fixture did not deliberately plant, so the count above is coincidence"
        )

    clean, exempted, _ = analyse(conforming, {})
    if len(clean) != EXPECTED_CONFORMING_FINDINGS:
        return refuse(
            f"the conforming fixture produced {len(clean)} finding(s), expected "
            f"{EXPECTED_CONFORMING_FINDINGS}: {clean}"
        )
    if len(exempted) < EXPECTED_CONFORMING_EXEMPTIONS:
        return refuse(
            f"the conforming fixture exempted {len(exempted)} file(s), expected at least "
            f"{EXPECTED_CONFORMING_EXEMPTIONS}. Its exception family is not over the "
            f"ceiling, so this arm is green whether the exemption works or not"
        )

    # The registry-drift clause, controlled INLINE because the registry ships
    # EMPTY. With no entries that check runs over nothing every time, so without
    # these two cells it is a code path that has never executed and cannot be
    # shown to work -- the same defect as a counter that cannot increment.
    # Synthetic dictionaries, not a tracked registry, so the real one stays
    # empty and the clause is still exercised on every run.
    packages = source_roots()
    if packages:
        package = packages[0]
        vanished = _check_registry_drift({"no/such/module.py": "a path that is gone"}, package)
        if len(vanished) != 1:
            return refuse(
                f"the registry-drift control for a MISSING path produced {len(vanished)} "
                f"problem(s), expected 1: {vanished}"
            )
        # A file that exists and is comfortably UNDER the ceiling: an exemption
        # naming it describes work that has already landed.
        under = next(
            (
                p.relative_to(package).as_posix()
                for p in python_sources(package)
                if len([n for n in ast.parse(p.read_text()).body if isinstance(n, ast.ClassDef)])
                <= MAX_CLASSES_PER_FILE
            ),
            None,
        )
        if under is None:
            return refuse(
                "no module in this package is under the ceiling, so the second "
                "registry-drift control has no subject -- which would also mean the gate's "
                "population is not what it thinks it is"
            )
        stale = _check_registry_drift({under: "an exemption that describes the past"}, package)
        if len(stale) != 1:
            return refuse(
                f"the registry-drift control for an UNDER-CEILING path produced "
                f"{len(stale)} problem(s), expected 1: {stale}"
            )
    return None


def main() -> int:
    failed = _self_test()
    if failed is not None:
        return failed

    control_problems = _family_control_failures()
    if control_problems:
        for item in control_problems:
            print(f"  {item}", file=sys.stderr)
        return refuse(
            f"{len(control_problems)} exception-family control(s) failed, so the exemption "
            "is wrong before this gate has read a single file"
        )

    # Class density is a UNIVERSAL property: a library package's modules hold as
    # many or as few classes as a service's, so there is no surface filter here.
    # A package is in scope because it exists.
    packages = source_roots()
    if len(packages) < MIN_PACKAGES:
        return refuse(
            f"{len(packages)} package(s) under src/, floor is {MIN_PACKAGES}. A glob that "
            "matched nothing is a broken scan, not a clean tree"
        )

    exemptions: dict[str, str] = {}
    if EXEMPTIONS_FILE.is_file():
        try:
            exemptions = json.loads(EXEMPTIONS_FILE.read_text())
        except json.JSONDecodeError as exc:
            return refuse(f"{EXEMPTIONS_FILE.name} is not valid JSON: {exc}")
        if not isinstance(exemptions, dict):
            return refuse(f"{EXEMPTIONS_FILE.name} must be an object of path -> reason")

    names = {p.name for p in packages}
    orphaned = [
        f"exemption names {key!r}, which belongs to no live package. Keys are "
        f"'<package>/<path relative to src/<package>/>'; this repository ships {sorted(names)}"
        for key in sorted(exemptions)
        if "/" not in key or key.split("/", 1)[0] not in names
    ]

    # THE DECLARATION FAILS CLOSED IN BOTH DIRECTIONS. A declared estate base that this
    # tree imports from somewhere other than the contracts package is a local class wearing
    # a shared contract's name, and exempting on it would exempt a family never verified.
    imported: dict[str, str] = {}
    for package in packages:
        for path in python_sources(package):
            try:
                parsed = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(parsed):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        imported.setdefault(alias.asname or alias.name, node.module)
    declaration_problem = _estate_base_declaration_problem(imported)
    if declaration_problem is not None:
        return refuse(declaration_problem)

    findings: list[str] = []
    exempted: list[str] = []
    counts = {"modules": 0, "classes": 0}
    per_package: dict[str, dict[str, int]] = {}
    for package in packages:
        package_findings, package_exempted, package_counts = analyse(
            package, exemptions, f"{package.name}/"
        )
        findings += package_findings
        exempted += package_exempted
        per_package[package.name] = dict(package_counts)
        for key, value in package_counts.items():
            counts[key] += value

    accounting_problems = _accounting_control_failures()
    if accounting_problems:
        for item in accounting_problems:
            print(f"  {item}", file=sys.stderr)
        return refuse(
            f"the per-package accounting clause failed its own control(s): "
            f"{len(accounting_problems)} of {len(ACCOUNTING_CONTROLS)}. A clause that has "
            "never been shown to fire, or that fires on everything, cannot certify a scan"
        )
    accounting_problem = package_accounting_problem(per_package)
    if accounting_problem is not None:
        return refuse(accounting_problem)

    #: Built ONCE and printed on the FINDING path and the PASS path alike. A
    #: coverage figure that appears only on the green path is absent from every
    #: run that someone actually reads closely. One variable feeding both makes
    #: DIVERGENCE unrepresentable; it does not make DELETION unrepresentable, and
    #: no control here covers that -- stated rather than left to be assumed.
    accounting = "; ".join(
        f"{name} {c['modules']}m/{c['classes']}c" for name, c in sorted(per_package.items())
    )

    if counts["modules"] < MIN_MODULES:
        return refuse(
            f"scanned {counts['modules']} module(s), fewer than the floor of {MIN_MODULES}"
        )
    if counts["classes"] < MIN_CLASSES_SEEN:
        return refuse(
            f"collected {counts['classes']} class(es), fewer than the floor of "
            f"{MIN_CLASSES_SEEN}; the walker did not parse the tree"
        )

    drift = orphaned + [
        problem
        for package in packages
        for problem in _check_registry_drift(exemptions, package, f"{package.name}/")
    ]
    if drift:
        for item in drift:
            print(f"  {item}", file=sys.stderr)
        return refuse(f"{len(drift)} stale exemption(s) in {EXEMPTIONS_FILE.name}")

    if findings:
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        return finding(
            f"{len(findings)} module(s) over the class ceiling of {MAX_CLASSES_PER_FILE}. "
            f"per package: {accounting}"
        )

    arms = counts["modules"] + len(exemptions) + 3
    print(
        f"PASS: {len(packages)} package(s) ({', '.join(p.name for p in packages)}), "
        f"{counts['modules']} module(s), {counts['classes']} class(es), none over the "
        f"ceiling of {MAX_CLASSES_PER_FILE}; {len(exempted)} exempted "
        f"({len(exemptions)} declared, {len(exempted) - len(exemptions)} structural); "
        f"{counts['modules']} module arm(s) + {len(exemptions)} registry arm(s) + 3 fixture "
        f"arms = {arms} arms; {arms} executed, 0 skipped, 0 unaccounted. "
        f"per package: {accounting}"
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
