#!/usr/bin/env python3
"""§16: persistence is SQLAlchemy 2's ORM, and the three ways out of it are closed.

WHY A GATE RATHER THAN A CONVENTION. The template shipped two hand-written SQL
adapters -- `sqlite3` for local, `pymysql` for a MySQL nothing in this estate
deploys -- and a service emitted from it inherited both. Every one of the three
clauses below is a route by which raw SQL comes back one file at a time, and
each is invisible in review because each is locally reasonable:

1. **A DBAPI DRIVER IMPORTED UNDER `src/`.** `import sqlite3` in a use case is
   how the ORM stops being the persistence layer and becomes a thing the
   repository happens to use. The one file allowed to name a driver is
   `engine_factory.py`, because a service may genuinely need a driver-specific
   connect argument -- and this template's own `engine_factory.py` imports none,
   which is why the exception has its own FIXTURE rather than leaning on
   production to exercise it.

2. **A `DeclarativeBase` SUBCLASS OUTSIDE `adapters/real/orm/`.** A second base
   is a second half of the schema that `migrations/env.py` cannot see: the
   tables mapped on it exist in the code, are absent from every revision, and
   `alembic upgrade head` succeeds having created none of them. Silently, at
   exit 0.

3. **`text(...)` WITH NO DECLARED REASON.** Escaping to raw SQL is sometimes
   right -- a readiness probe, a vector operator no dialect exposes -- and it is
   a decision somebody should have to write down. `scripts/orm_text_allowlist.json`
   is where, and it FAILS CLOSED IN BOTH DIRECTIONS: an undeclared call is a
   finding, and so is an entry naming a file that no longer makes one, because a
   stale exemption reads as review and is not.

SCOPE. The gate runs on services that own a schema, which is what a `migrations/`
directory means. A service with no database exits 3 -- a NAMED skip, never a
pass, because "found no driver imports" and "looked at nothing" are the same
output otherwise.

FIXTURES ARE TEMP-MATERIALISED, not tracked, and that is deliberate for two
measured reasons. A tracked offending package under `gate_fixtures/` would be
found by the OTHER gates' production scans and would move
`EXPECTED_MARKED_FIXTURE_FILES`, which five gates assert against; and
`check_gate_fixture_isolation.py` plants flat canary copies of the tracked
fixture tree one level up, which this gate's own `src/` glob would then read as
production. `check_folder_structure.py` and `check_kit_drift.py` take the same
route and say so. The filesystem is also the medium this gate's verdict reads,
so building the tree there is the control mutating through the right medium
rather than a convenience.

Exit codes: `service-kit/gates/gate_exit_codes.py`.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import (  # noqa: E402
    MIN_PACKAGES,
    PACKAGE_ROOT,
    python_sources,
    service_data_file,
    source_roots,
)
from gate_exit_codes import GREEN, refuse, skip  # noqa: E402

#: DBAPI drivers and the ORM's escape hatches into them. HAND-WRITTEN: a list
#: derived from what happens to be installed would shrink to nothing on a
#: checkout with no database driver, which is every CI job that has not
#: installed the lockfile yet.
DRIVER_MODULES = frozenset(
    {"sqlite3", "pymysql", "psycopg", "psycopg2", "psycopg2cffi", "MySQLdb", "asyncpg", "aiosqlite"}
)

#: The ONE file under `src/` that may name a driver, by basename.
DRIVER_EXCEPTION = "engine_factory.py"

#: Where record classes and the declarative base live, relative to `src/<pkg>/`.
ORM_PACKAGE = ("adapters", "real", "orm")

#: The service-owned register of deliberate raw-SQL escapes.
ALLOWLIST_FILE = "orm_text_allowlist.json"

#: HAND-WRITTEN floors. A scan that read nothing reports the same clean tree as
#: a scan that read everything, and only a floor tells them apart. The second is
#: the two-sided half: a classifier that has never resolved a sqlalchemy import
#: has not demonstrated it can see the ORM at all, so its silence about drivers
#: and bases means nothing.
MIN_MODULES = 20
MIN_SQLALCHEMY_MODULES = 3


def _module_roots(tree: ast.AST) -> list[tuple[str, int]]:
    """(top-level module name, line) for every import in the file."""
    roots: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend((alias.name.split(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.append((node.module.split(".")[0], node.lineno))
    return roots


def _sqlalchemy_text_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to `sqlalchemy.text`, and module aliases for `sqlalchemy`.

    Resolved from the IMPORTS rather than matched as the word `text`, because
    `response.text(...)`, `element.text` and a local variable called `text` are
    all ordinary and none of them is raw SQL. A matcher that read the token
    would flag them, and a false red naming a real file and line is the worse
    direction -- the natural response is to rewrite the file.
    """
    bound: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] != "sqlalchemy":
                continue
            for alias in node.names:
                if alias.name == "text":
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "sqlalchemy":
                    modules.add(alias.asname or alias.name)
    return bound, modules


def _text_calls(tree: ast.AST) -> list[int]:
    """Line numbers of every `text(...)` that is SQLAlchemy's `text`."""
    bound, modules = _sqlalchemy_text_names(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in bound:
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "text":
            value = func.value
            if isinstance(value, ast.Name) and value.id in modules:
                lines.append(node.lineno)
    return lines


def _declarative_bases(tree: ast.AST) -> list[tuple[str, int]]:
    """(class name, line) for every class deriving from `DeclarativeBase`.

    Both spellings, because `class Base(DeclarativeBase)` and
    `class Base(orm.DeclarativeBase)` are the same declaration and a matcher
    that saw only the first would be blind to whichever one a service happened
    to write.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            named = (
                base.id
                if isinstance(base, ast.Name)
                else base.attr
                if isinstance(base, ast.Attribute)
                else ""
            )
            if named == "DeclarativeBase":
                found.append((node.name, node.lineno))
                break
    return found


def load_allowlist(path: Path) -> tuple[dict[str, str], list[str]]:
    """Return ({relative path: reason}, problems). A missing file is EMPTY, not an error.

    A service with nothing to declare should not have to carry a file, and an
    absent register is indistinguishable from an empty one for the purpose of
    this gate: both mean no escape has been declared. A MALFORMED one is
    different and is a problem, because it means somebody wrote a declaration
    the gate is not reading.
    """
    problems: list[str] = []
    if not path.is_file():
        return {}, problems
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {}, [f"{path.name} is not valid JSON: {exc}"]
    entries = payload.get("allowed")
    if not isinstance(entries, list):
        return {}, [f"{path.name} has no 'allowed' list; it must be a list of entries"]
    allowed: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("path"):
            problems.append(f"{path.name} entry {index} has no 'path'")
            continue
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(
                f"{path.name} entry for {entry['path']!r} carries no 'reason'. An escape "
                f"nobody had to justify is not an exemption, it is a hole"
            )
            continue
        allowed[str(entry["path"])] = reason
    return allowed, problems


def analyse(
    package: Path, allowed: dict[str, str], key_prefix: str = ""
) -> tuple[list[str], dict[str, int]]:
    """(findings, counts) for ONE source package.

    `key_prefix` is what makes an allowlist key unambiguous across a repository
    that ships more than one package: production passes `"<package>/"`, so an
    entry reads `foundry_sandbox/adapters/real/orm/queries.py` and cannot be
    satisfied by a same-named file in a sibling package. It defaults to `""`, so
    the control trees below -- which are single synthetic packages -- call this
    exactly as they did before and their expectations still mean what they say.

    Every finding and every lookup goes through the QUALIFIED path rather than
    the bare relative one. Qualifying the message but not the lookup, or the
    other way round, would produce a gate whose findings name a file its
    allowlist can never match.
    """
    findings: list[str] = []
    counts = {"modules": 0, "sqlalchemy_modules": 0, "text_calls": 0}
    seen_text_files: set[str] = set()

    for path in python_sources(package, exclude_fixtures=False):
        relative = f"{key_prefix}{path.relative_to(package).as_posix()}"
        counts["modules"] += 1
        tree = ast.parse(path.read_text(), filename=str(path))

        roots = _module_roots(tree)
        if any(root == "sqlalchemy" for root, _ in roots):
            counts["sqlalchemy_modules"] += 1

        # CLAUSE 1 -- a DBAPI driver named outside engine_factory.py
        if path.name != DRIVER_EXCEPTION:
            for root, line in roots:
                if root in DRIVER_MODULES:
                    findings.append(
                        f"{relative}:{line}: imports the DBAPI driver {root!r}. Persistence "
                        f"goes through SQLAlchemy; the driver is selected by DATABASE_URL's "
                        f"dialect at first connect. Only {DRIVER_EXCEPTION} may name one"
                    )

        # CLAUSE 2 -- a declarative base outside adapters/real/orm/
        if path.relative_to(package).parts[: len(ORM_PACKAGE)] != ORM_PACKAGE:
            for name, line in _declarative_bases(tree):
                findings.append(
                    f"{relative}:{line}: {name} subclasses DeclarativeBase outside "
                    f"{'/'.join(ORM_PACKAGE)}/. A second base is a second half of the schema "
                    f"that migrations/env.py cannot see"
                )

        # CLAUSE 3 -- raw SQL with no declared reason
        lines = _text_calls(tree)
        counts["text_calls"] += len(lines)
        if lines:
            seen_text_files.add(relative)
        if lines and relative not in allowed:
            findings.append(
                f"{relative}:{lines[0]}: calls sqlalchemy text() and is not in "
                f"{ALLOWLIST_FILE}. Raw SQL is sometimes right and is always a decision; "
                f"declare it there with a reason"
            )

    # CLAUSE 3, the other direction -- a declaration nobody needs any more.
    # Scoped to THIS package's keys: a sibling package's entry is not stale just
    # because this package does not need it. An entry whose prefix names no live
    # package at all is caught in main(), where every package name is known.
    for declared in sorted(allowed):
        if not declared.startswith(key_prefix):
            continue
        if declared not in seen_text_files:
            findings.append(
                f"{ALLOWLIST_FILE}: the entry for {declared!r} names no text() call. A stale "
                f"exemption reads as review and is not; remove it"
            )

    return findings, counts


#: A whole conforming service package, as {relative path: source}. HAND-WRITTEN,
#: and deliberately not generated from the constants this gate asserts against --
#: a fixture built from the same declarations would agree with any of them,
#: including an empty set.
_CONFORMING: dict[str, str] = {
    "__init__.py": "",
    "adapters/__init__.py": "",
    "adapters/real/__init__.py": "",
    "adapters/real/orm/__init__.py": "from fixture_pkg.adapters.real.orm.base import Base\n",
    "adapters/real/orm/base.py": (
        "from sqlalchemy import MetaData\n"
        "from sqlalchemy.orm import DeclarativeBase\n\n\n"
        "class Base(DeclarativeBase):\n"
        "    metadata = MetaData()\n"
    ),
    "adapters/real/orm/widget_record.py": (
        "from sqlalchemy.orm import Mapped, mapped_column\n\n"
        "from fixture_pkg.adapters.real.orm.base import Base\n\n\n"
        "class WidgetRecord(Base):\n"
        '    __tablename__ = "widgets"\n'
        "    widget_id: Mapped[str] = mapped_column(primary_key=True)\n"
    ),
    "adapters/real/engine_factory.py": (
        "from sqlalchemy import create_engine\n\n\n"
        "def build_engine(url: str):\n"
        "    return create_engine(url)\n"
    ),
    "adapters/real/sql_widget_repository.py": (
        "from sqlalchemy import select\n\n"
        "from fixture_pkg.adapters.real.orm.widget_record import WidgetRecord\n\n\n"
        "def list_widgets(session):\n"
        "    return list(session.scalars(select(WidgetRecord)))\n"
    ),
    "application/__init__.py": "",
    "application/create_widget.py": (
        "def create_widget(repository, widget):\n    repository.add(widget)\n"
    ),
    "domain/__init__.py": "",
    "domain/model.py": (
        "from dataclasses import dataclass\n\n\n"
        "@dataclass(frozen=True)\nclass Widget:\n    widget_id: str\n"
    ),
}

#: One MUTATION per clause, applied to the conforming package. One fixture per
#: CLAUSE, never one per defect class: a fixture that trips two clauses stays red
#: when either is blinded and therefore proves neither.
_MUTATIONS: dict[str, dict[str, str]] = {
    "driver-import": {
        "application/create_widget.py": (
            "import sqlite3\n\n\n"
            "def create_widget(repository, widget):\n"
            "    sqlite3.connect(':memory:')\n"
            "    repository.add(widget)\n"
        )
    },
    "driver-import-in-engine-factory": {
        "adapters/real/engine_factory.py": (
            "import psycopg\n"
            "from sqlalchemy import create_engine\n\n\n"
            "def build_engine(url: str):\n"
            "    return create_engine(url, connect_args={'prepare_threshold': None})\n"
        )
    },
    "base-outside-orm": {
        "domain/model.py": (
            "from sqlalchemy.orm import DeclarativeBase\n\n\n"
            "class DomainBase(DeclarativeBase):\n    pass\n"
        )
    },
    "undeclared-text": {
        "adapters/real/sql_widget_repository.py": (
            "from sqlalchemy import text\n\n\n"
            "def probe(session):\n"
            '    return session.execute(text("SELECT 1"))\n'
        )
    },
    "text-that-is-not-sql": {
        "application/create_widget.py": (
            "def create_widget(repository, response):\n"
            "    def text(value):\n"
            "        return value\n"
            '    repository.add(text("not sql at all"))\n'
            "    return response.text()\n"
        )
    },
}


def _control_cases() -> list[tuple[str, dict[str, str], int, str, str]]:
    """(name, allowlist, expected_findings, clause_marker, why).

    `expected_findings` is an exact HAND-WRITTEN count and `clause_marker` names
    the finding it must be. A pass/fail arm cannot tell a gate reddening for its
    own clause from one reddening for a neighbour's, and this gate has three
    clauses over one tree.
    """
    return [
        ("conforming", {}, 0, "", "a package on the ORM standard is clean"),
        (
            "driver-import",
            {},
            1,
            "'sqlite3'",
            "a DBAPI driver imported outside engine_factory.py is a finding",
        ),
        (
            "driver-import-in-engine-factory",
            {},
            0,
            "",
            "THE NEGATIVE CONTROL FOR CLAUSE 1'S EXCEPTION. This template's own "
            "engine_factory.py imports no driver, so without this case the exception "
            "would be unexercised code that a rewrite could delete silently",
        ),
        (
            "base-outside-orm",
            {},
            1,
            "DeclarativeBase outside",
            "a second declarative base is a finding",
        ),
        (
            "undeclared-text",
            {},
            1,
            ALLOWLIST_FILE,
            "raw SQL with no declared reason is a finding",
        ),
        (
            "undeclared-text",
            {"adapters/real/sql_widget_repository.py": "readiness probe"},
            0,
            "",
            "the same raw SQL, declared with a reason, is clean",
        ),
        (
            "conforming",
            {"adapters/real/sql_widget_repository.py": "nothing raw here any more"},
            1,
            "names no text() call",
            "a declaration for a file that makes no text() call is a finding, so the "
            "register cannot rot into prose nobody reads",
        ),
        (
            "text-that-is-not-sql",
            {},
            0,
            "",
            "A LOCAL FUNCTION AND AN ATTRIBUTE, BOTH CALLED text. The matcher resolves "
            "the name through the imports; one that read the token would flag both, and "
            "a false red naming a real file is the worse direction",
        ),
    ]


def _materialise(case: str, root: Path) -> None:
    """Write one whole miniature source package for one control case."""
    sources = dict(_CONFORMING)
    sources.update(_MUTATIONS.get(case, {}))
    for relative, body in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def _self_test() -> int | None:
    """Run the PRODUCTION analyser over every control case, in a temp tree."""
    for case, allowed, expected, marker, why in _control_cases():
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "fixture_pkg"
            _materialise(case, package)
            findings, counts = analyse(package, allowed)
            if counts["modules"] < len(_CONFORMING):
                return refuse(
                    f"control '{case}' read {counts['modules']} module(s) of "
                    f"{len(_CONFORMING)}; the fixture did not reach the analyser"
                )
            if len(findings) != expected:
                return refuse(
                    f"control '{case}' (allowlist={sorted(allowed)}) produced "
                    f"{len(findings)} finding(s), expected {expected} ({why}). "
                    f"findings={findings}"
                )
            if marker and not any(marker in item for item in findings):
                return refuse(
                    f"control '{case}' produced {expected} finding(s) but none names "
                    f"{marker!r}. The count is right and the reason is not, which is a gate "
                    f"reddening for the wrong clause. findings={findings}"
                )
    return None


def main() -> int:
    failed = _self_test()
    if failed is not None:
        return failed

    # The ORM standard is a UNIVERSAL property: a library package that reaches
    # for a DBAPI driver or declares a second base is the same defect as a
    # service doing it, so there is no surface filter here. Every package is in
    # scope because it exists.
    packages = source_roots()
    if len(packages) < MIN_PACKAGES:
        return refuse(
            f"{len(packages)} package(s) under {PACKAGE_ROOT / 'src'}, floor is "
            f"{MIN_PACKAGES}. This is a broken checkout rather than a service without a "
            "database"
        )
    if not (PACKAGE_ROOT / "migrations").is_dir():
        return skip(
            "this service has no migrations/ directory, so it owns no schema and the ORM "
            "standard has nothing to hold it to. Add an alembic tree to bring it in scope"
        )

    allowed, problems = load_allowlist(service_data_file(ALLOWLIST_FILE))
    findings = [*problems]

    # An entry whose prefix names no live package. Checked HERE rather than in
    # analyse(), which sees one package and cannot tell an entry for a sibling
    # from an entry for a package that no longer exists -- and reporting the
    # first as stale would be a false red on a correct allowlist.
    names = {p.name for p in packages}
    for key in sorted(allowed):
        if "/" not in key or key.split("/", 1)[0] not in names:
            findings.append(
                f"{ALLOWLIST_FILE}: the entry for {key!r} names no live package. Keys are "
                f"'<package>/<path relative to src/<package>/>'; this repository ships "
                f"{sorted(names)}"
            )

    counts = {"modules": 0, "sqlalchemy_modules": 0, "text_calls": 0}
    for package in packages:
        package_findings, package_counts = analyse(package, allowed, f"{package.name}/")
        findings += package_findings
        for key, value in package_counts.items():
            counts[key] += value

    if counts["modules"] < MIN_MODULES:
        return refuse(
            f"read {counts['modules']} module(s), fewer than the floor of {MIN_MODULES}. "
            f"A scan that reached nothing reports the same clean tree as one that read "
            f"everything"
        )
    if counts["sqlalchemy_modules"] < MIN_SQLALCHEMY_MODULES:
        return refuse(
            f"resolved a sqlalchemy import in {counts['sqlalchemy_modules']} module(s), "
            f"fewer than the floor of {MIN_SQLALCHEMY_MODULES}. A classifier that has "
            f"never seen the ORM has proved half of nothing about raw SQL"
        )

    if findings:
        print(f"check-orm-standard: FAIL {len(findings)} finding(s):")
        for item in findings:
            print(f"  {item}")
        return 1

    controls = _control_cases()
    arms = counts["modules"] * 3 + len(allowed)
    print(
        f"check-orm-standard: PASS {len(packages)} package(s) "
        f"({', '.join(p.name for p in packages)}), "
        f"{counts['modules']} module(s) x 3 clause(s) + "
        f"{len(allowed)} declared escape(s) = {arms} arms; {arms} executed, 0 skipped, "
        f"0 unaccounted. {counts['sqlalchemy_modules']} module(s) import sqlalchemy, "
        f"{counts['text_calls']} text() call(s) found. Controls: {len(controls)} trees, "
        f"{sum(1 for c in controls if c[2] == 0)} quiet and "
        f"{sum(1 for c in controls if c[2] > 0)} flagged."
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
