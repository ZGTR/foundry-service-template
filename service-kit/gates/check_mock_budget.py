#!/usr/bin/env python3
"""§7: minimal mocks. A RATCHET that may only shrink.

`unittest.mock`, `MagicMock` and `monkeypatch` on a COLLABORATOR are not
permitted for anything that has a port: the substitute is the `adapters/fake/`
implementation, or a real resource from `docker/compose/`. Every port in this
package has a fake, and `tests/contract/` runs the same contract suite against
the fake and the real adapter -- so substituting a fake is a measured claim,
where a mock is an assertion about a mock.

WHY A RATCHET AND NOT A WALL. Two uses of `monkeypatch` here are legitimate and
would make a wall red on a healthy tree:

* `monkeypatch.setenv` / `delenv` -- the ENVIRONMENT is not a collaborator and
  has no port. It is process state, and setting it is how you drive
  `ServiceConfig.from_environment()` at all.
* `monkeypatch.setattr(socket, "socket", ...)` -- a guard that the container
  graph opens NO socket. There is no port to fake here; the assertion is
  precisely that the standard library call never happens.

Both are counted in the budget rather than exempted by name, because a name-based
exemption is a list standing in for the artifact, and the number is what the
ratchet is for. `mock_budget.json` records the measured count and the inventory;
the count may only SHRINK, and it fails in BOTH directions -- beating the ratchet
without lowering it is a finding too, because an unlowered ratchet is a ceiling
nobody is holding.

An INVENTORY, not just a number: 7 and 7 look identical when one call was removed
and another introduced the same day.

Exit codes: `scripts/gate_exit_codes.py`.
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
    PACKAGE_ROOT,
    marker_count,
    python_sources,
    service_data_file,
)
from gate_exit_codes import GREEN, finding, refuse, skip  # noqa: E402

BUDGET_FILE = service_data_file("mock_budget.json")
TESTS_DIR = PACKAGE_ROOT / "tests"

MOCK_MODULES = frozenset({"unittest.mock", "mock"})
MOCK_NAMES = frozenset(
    {"MagicMock", "Mock", "AsyncMock", "NonCallableMock", "patch", "PropertyMock"}
)
MONKEYPATCH_METHODS = frozenset({"setattr", "delattr", "setitem", "delitem", "setenv", "delenv"})

#: HAND-WRITTEN fixture expectations.
EXPECTED_OFFENDING_USES = 3
EXPECTED_CONFORMING_USES = 0
#: Two-sided floor: the scan must have read real test files, and the classifier
#: must have distinguished a mock use from an ordinary call. A detector that has
#: never fired cannot report an empty estate.
MIN_TEST_FILES = 10


def _uses(path: Path) -> list[str]:
    """Every mock-shaped use in one file, as the KIND of use.

    Deliberately NOT keyed by line number. A line-keyed inventory moves on any
    unrelated edit above it, so the gate would report drift for a reformatted
    docstring -- a false red naming a real file, the worse direction, and one
    that trains a reader to re-record the inventory without looking.
    """
    found: list[str] = []
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in MOCK_MODULES:
            found.append(f"imports from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in MOCK_MODULES:
                    found.append(f"imports {alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in MOCK_NAMES:
                found.append(f"constructs {func.id}")
            elif isinstance(func, ast.Attribute) and func.attr in MONKEYPATCH_METHODS:
                target = func.value
                if isinstance(target, ast.Name) and "monkeypatch" in target.id.lower():
                    found.append(f"monkeypatch.{func.attr}")
    return found


def inventory(root: Path, *, is_fixture: bool = False) -> tuple[list[str], int]:
    """(every use as `relative_path::kind`, files scanned). Duplicates KEPT.

    Keeping duplicates is what lets the COUNT and the INVENTORY be the same
    artifact: a set would collapse three `monkeypatch.setenv` calls in one file
    into one entry, and removing two of them would leave the inventory
    unchanged while the count fell -- which is exactly the repaired-versus-
    renamed ambiguity the inventory exists to resolve.
    """
    uses: list[str] = []
    files = 0
    for path in python_sources(root, exclude_fixtures=not is_fixture):
        files += 1
        uses.extend(f"{path.relative_to(root)}::{use}" for use in _uses(path))
    return sorted(uses), files


def main() -> int:
    if not TESTS_DIR.is_dir():
        return skip(f"{TESTS_DIR} does not exist; there is no mock budget to hold")
    if not BUDGET_FILE.is_file():
        return refuse(
            f"{BUDGET_FILE} is missing. A ratchet with no recorded value is not a ratchet; "
            "record the measured count and inventory before this gate can issue a verdict."
        )

    fixture_tests = FIXTURE_ROOT / "offending" / "tests"
    conforming_tests = FIXTURE_ROOT / "conforming" / "tests"
    if not fixture_tests.is_dir() or not conforming_tests.is_dir():
        return refuse(f"a mock-budget fixture is missing under {FIXTURE_ROOT}")
    offending_uses, _of = inventory(fixture_tests, is_fixture=True)
    conforming_uses, conforming_files = inventory(conforming_tests, is_fixture=True)
    if len(offending_uses) != EXPECTED_OFFENDING_USES:
        return refuse(
            f"the OFFENDING fixture produced {len(offending_uses)} mock use(s), expected the "
            f"hand-written {EXPECTED_OFFENDING_USES}: {offending_uses}"
        )
    if len(conforming_uses) != EXPECTED_CONFORMING_USES:
        return refuse(
            f"the CONFORMING fixture produced {len(conforming_uses)} mock use(s), expected "
            f"{EXPECTED_CONFORMING_USES}: {conforming_uses}. A fake-driven test must stay quiet, "
            "or this gate is counting ordinary calls."
        )
    if conforming_files < 1:
        return refuse("the conforming mock fixture holds no file; the quiet arm read nothing")
    marked = marker_count()
    if marked != EXPECTED_MARKED_FIXTURE_FILES:
        return refuse(
            f"{marked} offending fixture file(s) carry the marker, expected "
            f"{EXPECTED_MARKED_FIXTURE_FILES}; the control has been neutralised"
        )

    budget = json.loads(BUDGET_FILE.read_text())
    recorded = list(budget["inventory"])
    ceiling = int(budget["ratchet"])
    if ceiling != len(recorded):
        return refuse(
            f"{BUDGET_FILE.name}: ratchet {ceiling} disagrees with its own inventory of "
            f"{len(recorded)}; a bare number cannot tell a repaired use from a renamed one"
        )

    current, files = inventory(TESTS_DIR)
    if files < MIN_TEST_FILES:
        return refuse(
            f"only {files} test file(s) scanned, floor is {MIN_TEST_FILES}; an empty scan "
            "reports a clean estate it never read"
        )
    if len(current) > ceiling:
        added = sorted(set(current) - set(recorded))
        for item in added:
            print(f"  {item}", file=sys.stderr)
        return finding(
            f"{len(current)} mock use(s) against a ratchet of {ceiling}. Use an "
            "adapters/fake/ implementation or a real resource from docker/compose/."
        )
    if len(current) < ceiling:
        removed = sorted(set(recorded) - set(current))
        return finding(
            f"{len(current)} mock use(s) BEATS the ratchet of {ceiling} -- lower it and update "
            f"the inventory. Removed: {removed}. An unlowered ratchet is a ceiling nobody holds."
        )
    drift = sorted(set(current) ^ set(recorded))
    if drift:
        return finding(
            f"the mock count still matches {ceiling} but the INVENTORY moved: {drift}. "
            "One use repaired and another introduced is not a steady state."
        )
    print(
        f"PASS: {files} test file(s), {len(current)} mock use(s) == ratchet {ceiling}, "
        "inventory identical; 2 fixture arms + 3 ratchet directions = 5 arms; "
        "5 executed, 0 skipped, 0 unaccounted."
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
