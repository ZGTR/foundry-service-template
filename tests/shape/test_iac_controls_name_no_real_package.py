"""`check_service_iac.sh`'s inline controls name a module no emitted service can own.

The controls were written against
`foundry_service_template.transport.events.outbox_drain`, and `new_service.sh`
renames the package prefix, so every emitted service inherited a control naming a
module in ITS package that it may never have -- memory-service's deploy-path test
went red on exactly that. The subject is now `example_service`, which substitution
leaves alone.

The rename is only safe if the controls still BITE, so this file also runs the
classifier and its controls out of the script and blinds each clause in turn: a
control that passes for the wrong reason after a rename is the risk the bead named.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[2]
SCRIPT = TEMPLATE / "scripts" / "check_service_iac.sh"
PACKAGE = "foundry_service_template"


def _inert_program() -> str:
    """The PYINERT heredoc up to its YAML read: imports, classifier, controls, check."""
    text = SCRIPT.read_text()
    match = re.search(r"<<'PYINERT'\n(.*?)\nPYINERT\n", text, re.S)
    assert match, "no PYINERT heredoc in check_service_iac.sh; the test reads nothing"
    body = match.group(1)
    cut = body.index("doc = yaml.safe_load")
    return body[:cut]


def _controls_block() -> str:
    program = _inert_program()
    return program[program.index("CONTROLS = ("):program.index("control_failures = [")]


def test_no_control_names_the_template_package() -> None:
    block = _controls_block()
    entries = re.findall(r"^\s*\((True|False),", block, re.M)
    assert len(entries) >= 6, f"parsed {len(entries)} control entries; the slice is wrong"
    assert PACKAGE not in block, (
        f"a control names {PACKAGE}; new_service.sh will rename it into every service"
    )


def _run(program: str) -> int:
    try:
        exec(compile(program, str(SCRIPT), "exec"), {"__name__": "controls"})
    except SystemExit as stop:
        return int(stop.code or 0)
    return 0


def test_the_controls_agree_with_the_classifier() -> None:
    assert _run(_inert_program()) == 0


@pytest.mark.parametrize("original, blinded", [
    # the import-only clause: a call after an import would read inert
    ("return all(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body)",
     "return True"),
    # the `-c` clause: `-m <module>` would read inert
    ('    if "-c" not in parts:\n        return False',
     '    if "-c" not in parts:\n        return True'),
    # the empty-command clause: no command would read inert
    ("    if not command:\n        return False", "    if not command:\n        return True"),
])
def test_each_clause_is_load_bearing_after_the_rename(original: str, blinded: str) -> None:
    program = _inert_program()
    assert program.count(original) == 1, f"mutation did not land: {original!r}"
    assert _run(program.replace(original, blinded)) == 1
