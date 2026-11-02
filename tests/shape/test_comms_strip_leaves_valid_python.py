"""Every marked Python file still parses once the comms strip has run.

WHAT WENT WRONG. `ruff check --fix` sorted two import blocks in this repository
and wrapped a marked import, because the marker comment pushed the line past 100
columns:

    from pkg.adapters.real import (
        SqsPublisher,  # COMMS-FLAVOUR-STRIP-LINE
    )

`scripts/apply_comms_flavour.py` drops every line that names a marker, and only
that line. What a `comms-*` service would have received is `from pkg.adapters.real
import (` followed by `)`, which is a SyntaxError in two of its test files. ruff
was green on the template, because the template's file is valid; the defect only
exists in the emission.

`test_new_service_script.py` emits a comms service and runs its suite, so it
would have gone red too. It takes minutes and reports the emitted suite's whole
output. This names the file and the line in under a second.

WHAT IT DOES NOT PROVE. A stripped file that parses can still be wrong: an import
the strip removed while a use of the name survives is valid Python and fails at
run time. The emitted suite is the proof for that.
"""

from __future__ import annotations

import ast
from importlib import util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "apply_comms_flavour.py"
MODULE = "foundry_service_template"

#: Six marked files are Python today. A scan that saw fewer was not reading the
#: manifest it claims to read.
PYTHON_FLOOR = 6

WRAPPED_BY_RUFF = (
    "from pkg.adapters.real import Other\n"
    "from pkg.adapters.real import (\n"
    "    SqsPublisher,  # COMMS-FLAVOUR-STRIP-LINE\n"
    ")\n"
)
ON_ONE_LINE = (
    "from pkg.adapters.real import Other\n"
    "from pkg.adapters.real import SqsPublisher  # COMMS-FLAVOUR-STRIP-LINE\n"
)


def _flavour():
    spec = util.spec_from_file_location("apply_comms_flavour_under_test", SCRIPT)
    assert spec and spec.loader
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _marked_python_files() -> list[str]:
    marked = [entry.format(module=MODULE) for entry in _flavour().MARKED_FILES]
    return [rel for rel in marked if rel.endswith(".py")]


def test_the_check_can_tell_a_broken_strip_from_a_whole_one() -> None:
    flavour = _flavour()
    with pytest.raises(SyntaxError):
        ast.parse(flavour.strip_markers(WRAPPED_BY_RUFF, source="wrapped"))
    ast.parse(flavour.strip_markers(ON_ONE_LINE, source="one line"))


def test_every_marked_python_file_parses_after_the_strip() -> None:
    flavour = _flavour()
    python_files = _marked_python_files()
    assert len(python_files) >= PYTHON_FLOOR, (
        f"only {len(python_files)} marked Python file(s) found in MARKED_FILES; "
        f"expected at least {PYTHON_FLOOR}"
    )
    broken = []
    for rel in python_files:
        stripped = flavour.strip_markers((REPO / rel).read_text(), source=rel)
        try:
            ast.parse(stripped, filename=rel)
        except SyntaxError as exc:
            broken.append(f"{rel}: line {exc.lineno} of the stripped file: {exc.msg}")
    assert broken == [], (
        "a comms- service would receive Python that does not parse:\n  " + "\n  ".join(broken)
    )
