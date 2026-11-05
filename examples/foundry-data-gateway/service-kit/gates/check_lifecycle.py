#!/usr/bin/env python3
"""§6: the production lifecycle is DECLARED in `runtime/` and TESTED.

Six behaviours, each a clause, each needing both halves:

    /health/live, /health/ready, /health/startup with distinct semantics
    graceful shutdown on SIGTERM with an in-flight request drain
    structured JSON logging with a correlation id
    readiness gated on real dependency probes
    explicit, single-headed migration application at startup
    a rollback-safe, idempotent startup path

DECLARED and TESTED are separate assertions and both are necessary. Declared
alone is the shape that reads as covered in review: a `runtime/` module with the
right names and no test is exactly as reassuring as one with a test, and exactly
as worthless. Tested alone cannot happen -- a test of an absent module does not
collect -- but a test file whose NAME claims a behaviour it never asserts can,
which is why the test half looks for the behaviour's own vocabulary inside the
test tree rather than at a filename.

WHAT THIS GATE CANNOT DO, stated rather than implied: it reads declarations and
citations, not semantics. It cannot tell a correct liveness probe from one that
consults the database. `tests/unit/test_runtime_probes.py` is where that is
asserted, and this gate's job is to make sure that file cannot quietly stop
existing.

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
    PACKAGE_ROLES_FILE,
    PACKAGE_ROOT,
    load_package_roles,
    marker_count,
    source_roots,
    validate_package_roles,
)
from gate_exit_codes import GREEN, finding, refuse  # noqa: E402

TESTS_DIR = PACKAGE_ROOT / "tests"

#: WHERE THE LIFECYCLE OWNER IS DECLARED, and why it is declared at all.
#:
#: §6 is a property of ONE deployable, not of every package a repository ships.
#: The obvious alternative -- treat every package holding a `runtime/` directory
#: as an owner -- was measured against agent-runtime and is WRONG: that
#: repository ships three packages, two of them hold `runtime/`, and the second
#: (`foundry_sandbox`) holds nothing in it but `__init__.py`. Running the six
#: clauses over it produces SIX findings naming a real directory, which is the
#: false-red direction this estate records as the worse one. Its lifecycle is
#: deliberately different: it verifies a provenance envelope before it binds and
#: binds to loopback only, so demanding a readiness endpoint on it would be
#: demanding a defect.
#:
#: So the owner is DECLARED rather than inferred, and the declaration is SERVICE
#: data under `scripts/`, never a constant here: this file is copied byte-for-byte
#: into every service, and a package name written in it would name one service's
#: package in all of them.
#:
#: It is ONE KEY in the service's single package registry rather than a file of
#: its own. Three other gates read that same file, and three registries would mean
#: three drift checks and three chances for one of them to be the one nobody runs.
#:
#: A single-package repository needs no declaration -- there is only one candidate
#: and inferring it cannot be ambiguous -- which is why the registry ships empty in
#: the template and in six of the nine services measured.
OWNER_FILE_NAME = PACKAGE_ROLES_FILE

#: Each clause: the tokens that must appear somewhere in `runtime/` (DECLARED)
#: and the tokens that must appear somewhere in `tests/` (TESTED). Tokens rather
#: than exact names so a service may rename a class without losing coverage --
#: the CONFORMING fixture deliberately uses `BootSequence`/`install_signal_traps`
#: instead of `ServiceLifecycle`/`install`, and a gate that only recognised the
#: template's own naming would be matching an idiom rather than the rule.
CLAUSES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "three distinct probes": (
        ("/health/live", "/health/ready", "/health/startup"),
        ("/health/live", "/health/ready", "/health/startup"),
    ),
    "SIGTERM graceful shutdown with drain": (
        ("signal", "SIGTERM"),
        ("SIGTERM", "drain"),
    ),
    # ADJUDICATED PER CLAUSE, never per copy. The kit and the mature service copy
    # diverge in BOTH directions here, so "the service copy is more evolved" is
    # true of the gate and false of one of its clauses. Taking either file whole
    # would carry a WEAKENING in alongside the repair:
    #
    #   clause                  kit                    service                 stricter
    #   structured JSON logging ("json","correlation")  ("json","correlation_id")  service
    #   readiness gated         ("ready","probe")       ("ready",)                 KIT
    #   idempotent startup      ("start","lock")        ("lock","start")           same set
    #
    # So logging takes the service's `correlation_id` -- bare `correlation` is
    # satisfied by the word in any identifier -- and readiness KEEPS the kit's
    # second token, because `ready` alone is matched by the probe path itself and
    # would stop asking whether anything is actually probed.
    "structured JSON logging with a correlation id": (
        ("json", "correlation_id"),
        ("correlation_id",),
    ),
    "readiness gated on dependency probes": (
        ("ready", "probe"),
        ("ready",),
    ),
    "explicit single-headed migrations": (
        ("head",),
        ("head",),
    ),
    # "lock" alongside "start" on purpose. An idempotent startup needs a GUARD,
    # and the guard is the structural signal; matching the word "startup" alone
    # would be satisfied by a comment that says the module has no startup path,
    # which is the prose-triggers-the-matcher defect .claude/CLAUDE.md records
    # from the other side. It was measured here: the offending fixture's own
    # explanatory comment contains "startup", so a single-token clause stayed
    # quiet on the file written to violate it.
    "idempotent startup": (
        ("start", "lock"),
        ("idempotent",),
    ),
}

#: HAND-WRITTEN fixture expectations.
EXPECTED_OFFENDING_FINDINGS = 6
EXPECTED_CONFORMING_DECLARATION_FINDINGS = 0
#: Two-sided floor: `runtime/` must hold real modules, and the test corpus must
#: be non-trivial. A scan of an empty corpus reports full coverage.
MIN_RUNTIME_MODULES = 3
MIN_TEST_BYTES = 10_000

#: Owner-resolution control cases, run BEFORE any verdict on the real tree.
#: `(label, packages, declared, expected_owner)`, where `expected_owner` is None
#: when the case must produce a PROBLEM. Inline rather than as fixture packages
#: because the subject is a repository LAYOUT, and planting three throwaway
#: packages under `src/` would put them in every other gate's production
#: population -- the leak `check_gate_fixture_isolation.py` exists to catch.
#:
#: BOTH DIRECTIONS, and the quiet half is the load-bearing half: a resolver that
#: refused everything would satisfy every problem case here and none of the three
#: that must resolve.
OWNER_CONTROLS: tuple[tuple[str, tuple[str, ...], tuple[str, ...], str | None], ...] = (
    ("one package, no declaration -> inferred", ("svc",), (), "svc"),
    ("three packages, no declaration -> ambiguous", ("a", "b", "c"), (), None),
    ("three packages, declaration resolves", ("a", "b", "c"), ("b",), "b"),
    ("three packages, declaration names an absent package", ("a", "b", "c"), ("z",), None),
    ("one package, declaration agrees", ("svc",), ("svc",), "svc"),
    ("one package, declaration names another", ("svc",), ("other",), None),
    ("two declarations name no single owner", ("a", "b"), ("a", "b"), None),
    ("no package at all", (), (), None),
)


#: The two ADJUDICATED tokens, controlled in both directions. Each case is a
#: synthetic corpus and the clause that must or must not be reported missing.
#: These exist because both real trees satisfy every clause, so a token that was
#: quietly loosened back would leave this gate green on every tree it has -- the
#: adjudication would be a comment describing a matcher nobody can observe.
#:
#: `(label, corpus, clause fragment, must_be_missing)`
TOKEN_CONTROLS: tuple[tuple[str, str, str, bool], ...] = (
    (
        "bare 'correlation' does NOT satisfy the logging clause",
        "import json\nlog(correlation=1)\nready probe start lock head "
        "/health/live /health/ready /health/startup signal sigterm",
        "structured json logging",
        True,
    ),
    (
        "'correlation_id' DOES satisfy it",
        "import json\nlog(correlation_id=1)\nready probe start lock head "
        "/health/live /health/ready /health/startup signal sigterm",
        "structured json logging",
        False,
    ),
    (
        "'ready' without 'probe' does NOT satisfy the readiness clause",
        "import json\ncorrelation_id\n/health/ready\nstart lock head "
        "/health/live /health/startup signal sigterm",
        "readiness gated",
        True,
    ),
    (
        "'ready' with 'probe' DOES satisfy it",
        "import json\ncorrelation_id\n/health/ready probe\nstart lock head "
        "/health/live /health/startup signal sigterm",
        "readiness gated",
        False,
    ),
)


def _token_control_failures() -> list[str]:
    """The adjudicated tokens, driven through the real matcher, both directions."""
    failures = []
    for label, corpus, fragment, must_be_missing in TOKEN_CONTROLS:
        reported = any(
            fragment in item.lower() for item in findings_over_corpus(corpus.lower())
        )
        if reported != must_be_missing:
            failures.append(
                f"{label}: clause {'was not' if must_be_missing else 'was'} reported missing"
            )
    return failures


def resolve_owner(
    packages: tuple[str, ...] | list[str], declared: tuple[str, ...] | list[str]
) -> tuple[str | None, str | None]:
    """Return `(owner, problem)`; exactly one of the two is ever set.

    Fails closed in BOTH directions: a declaration naming no live package is a
    problem, and so is an ambiguous repository with no declaration. Neither is a
    finding -- a gate that cannot decide WHICH package §6 is about has not
    measured that package and must say so rather than report on another one.
    """
    names = sorted(packages)
    wanted = sorted(declared)
    if wanted:
        if len(wanted) > 1:
            return None, (
                f"{OWNER_FILE_NAME} names {len(wanted)} owners {wanted}; §6 has exactly one "
                "lifecycle owner, and a file naming two declares none"
            )
        if wanted[0] not in names:
            return None, (
                f"the declared lifecycle owner {wanted[0]!r} is not among {names}; the "
                "declaration names no live package, so it is a sentence about a tree that "
                "no longer exists"
            )
        return wanted[0], None
    if len(names) == 1:
        return names[0], None
    if not names:
        return None, "no package under src/, so there is no runtime/ to hold §6"
    return None, (
        f"src/ holds {len(names)} packages {names} and {OWNER_FILE_NAME} declares no owner. "
        "§6 is a property of one deployable; running it over every package that happens to "
        "hold a runtime/ directory has been measured to produce false findings against a "
        "correct tree. Declare the owner."
    )


def _control_failures() -> list[str]:
    """Owner resolution driven through its real entry point, both directions."""
    failures = []
    for label, packages, declared, expected in OWNER_CONTROLS:
        owner, problem = resolve_owner(packages, declared)
        if expected is None:
            if problem is None or owner is not None:
                failures.append(f"{label}: expected a problem, got owner={owner!r}")
        elif owner != expected or problem is not None:
            failures.append(f"{label}: expected owner {expected!r}, got {owner!r} ({problem})")
    return failures


def _strip_prose(source: str) -> str:
    """Return the module's CODE, with docstrings and comments removed.

    A token search over raw text is satisfied by a COMMENT. Measured on this
    template: renaming `signal.SIGTERM` to `signal.SIGUSR1` in `runtime/shutdown.py`
    -- deleting the only real handler registration -- left this gate at exit 0,
    because `runtime/probes.py` and `runtime/middleware.py` each mention SIGTERM
    in prose explaining why they behave as they do. The gate then printed its full
    success line, "24 arms executed, 0 skipped, 0 unaccounted", for a service that
    could no longer drain on shutdown.

    That is the worst shape a gate can have: the better the code is explained, the
    less the gate checks, and the cheapest way to make it fire again is to delete
    the explanation. `ast.unparse` drops comments outright and the walk below drops
    docstrings, so only executable code remains.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _corpus(root: Path) -> str:
    if not root.is_dir():
        return ""
    return "\n".join(
        _strip_prose(path.read_text())
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    )


def declaration_findings(runtime_dir: Path, *also: Path) -> list[str]:
    """Clauses whose tokens are absent from the CODE of `runtime/` and its ports.

    `ports/` is in the corpus because the three probe PATHS legitimately live
    there: both `runtime/` and `transport/` need them, and §1's direction allows
    only one of the two to import the other, so the shared contract goes where a
    shared contract goes. Reading `runtime/` alone reports a service that
    SINGLE-SOURCED its probe paths as having stopped declaring them -- a finding
    produced by the refactor that improved the code.

    Measured: against agent-runtime this gate reported exactly one
    finding, "runtime/ declares no three distinct probes", for a service whose
    probe paths are declared once in `ports/` and imported by both callers.
    """
    corpus = "\n".join(
        [_corpus(runtime_dir), *(_corpus(path) for path in also)]
    ).lower()
    return findings_over_corpus(corpus)


def findings_over_corpus(corpus: str) -> list[str]:
    """Clauses whose declared tokens are absent from an already-lowered corpus.

    Split out from the reader so the token adjudication above can be CONTROLLED
    without touching the filesystem. Without it the stricter tokens are a
    judgement written in a comment: both real trees satisfy them, so tightening
    or loosening a token leaves the gate green and nothing says which one is in
    force.
    """
    findings = []
    for clause, (declared, _tested) in CLAUSES.items():
        missing = [token for token in declared if token.lower() not in corpus]
        if missing:
            findings.append(f"runtime/ declares no {clause}: missing {missing}")
    return findings


def test_findings(tests_dir: Path) -> list[str]:
    """Clauses whose tokens are absent from the TEST corpus."""
    corpus = _corpus(tests_dir).lower()
    findings = []
    for clause, (_declared, tested) in CLAUSES.items():
        missing = [token for token in tested if token.lower() not in corpus]
        if missing:
            findings.append(f"no test asserts {clause}: missing {missing}")
    return findings


def main() -> int:
    packages = source_roots()
    if len(packages) < MIN_PACKAGES:
        return refuse(
            f"{len(packages)} package(s) under {PACKAGE_ROOT / 'src'}, floor is {MIN_PACKAGES}. "
            "A glob that matched nothing is a broken scan, not a clean tree."
        )

    # --- owner resolution, controlled BEFORE it is trusted on the real tree ---
    control_failures = _control_failures()
    if control_failures:
        return refuse(
            "owner-resolution control(s) failed, so this run cannot be trusted to have "
            f"picked the right package: {control_failures}"
        )
    token_failures = _token_control_failures()
    if token_failures:
        return refuse(
            "adjudicated clause-token control(s) failed, so the clause matcher is not the "
            f"one this gate claims to run: {token_failures}"
        )
    entries, parse_problems = load_package_roles()
    registry_problems = parse_problems + validate_package_roles(entries, packages)
    if registry_problems:
        return refuse(
            f"the package registry is malformed or has rotted: {registry_problems}. A "
            "declaration whose reason is blank is a hole with a name, and one naming a "
            "package that is gone is a sentence about a tree that no longer exists."
        )
    declared = [
        name
        for name, entry in sorted(entries.items())
        if str(entry.get("lifecycle_owner", "")).lower() == "true"
    ]
    owner_name, problem = resolve_owner([p.name for p in packages], declared)
    if problem:
        return refuse(problem)
    root = next(p for p in packages if p.name == owner_name)
    runtime_dir = root / "runtime"
    if not runtime_dir.is_dir():
        return finding(f"{runtime_dir} does not exist; §6 requires a runtime/ submodule")

    conforming_runtime = FIXTURE_ROOT / "conforming" / "pkg" / "runtime"
    offending_runtime = FIXTURE_ROOT / "offending" / "pkg" / "runtime"
    if not conforming_runtime.is_dir() or not offending_runtime.is_dir():
        return refuse(f"a runtime fixture is missing under {FIXTURE_ROOT}")
    conforming = declaration_findings(conforming_runtime)
    if len(conforming) != EXPECTED_CONFORMING_DECLARATION_FINDINGS:
        return refuse(
            f"the CONFORMING runtime fixture produced {len(conforming)} finding(s), expected "
            f"{EXPECTED_CONFORMING_DECLARATION_FINDINGS}: {conforming}. Widen the matcher; never "
            "reword the fixture, which would turn the control into decoration."
        )
    offending = declaration_findings(offending_runtime)
    if len(offending) != EXPECTED_OFFENDING_FINDINGS:
        return refuse(
            f"the OFFENDING runtime fixture produced {len(offending)} finding(s), expected the "
            f"hand-written {EXPECTED_OFFENDING_FINDINGS}: {offending}"
        )
    marked = marker_count()
    if marked != EXPECTED_MARKED_FIXTURE_FILES:
        return refuse(
            f"{marked} offending fixture file(s) carry the marker, expected "
            f"{EXPECTED_MARKED_FIXTURE_FILES}; the control has been neutralised"
        )

    modules = [
        p for p in runtime_dir.glob("*.py") if p.name != "__init__.py"
    ]
    if len(modules) < MIN_RUNTIME_MODULES:
        return refuse(
            f"runtime/ holds {len(modules)} module(s), floor is {MIN_RUNTIME_MODULES}; "
            "an empty scan reports full coverage it never read"
        )
    test_bytes = len(_corpus(TESTS_DIR))
    if test_bytes < MIN_TEST_BYTES:
        return refuse(
            f"the test corpus is {test_bytes} byte(s), floor is {MIN_TEST_BYTES}; a gate that "
            "read no tests cannot report that six behaviours are tested"
        )

    findings = declaration_findings(runtime_dir, root / "ports") + test_findings(TESTS_DIR)
    if findings:
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        return finding(f"{len(findings)} lifecycle clause(s) undeclared or untested")
    total = len(CLAUSES) * 4 + len(OWNER_CONTROLS) + len(TOKEN_CONTROLS)
    source = "declared" if declared else "the only package"
    print(
        f"PASS: §6 owner {owner_name!r} ({source} of {len(packages)}), {len(modules)} runtime "
        f"module(s), {test_bytes} byte(s) of tests; {len(CLAUSES)} clauses x "
        f"(declared + tested) = {len(CLAUSES) * 2} arms, plus {len(CLAUSES)} x 2 fixture arms, "
        f"plus {len(OWNER_CONTROLS)} owner-resolution + {len(TOKEN_CONTROLS)} token "
        f"controls = {total} arms; "
        f"{total} executed, 0 skipped, 0 unaccounted."
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
