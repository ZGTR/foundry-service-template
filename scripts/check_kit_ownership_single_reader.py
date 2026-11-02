"""The kit has ONE reader of the `[tool.foundry.kit]` declaration.

`service-kit/kit_ownership.py` parses a service's pyproject.toml into what the service
owns beside the template's kit, and both `sync.py` and `gates/check_kit_drift.py` import
it. Before it existed the flavour reader was written twice, once in each, with
different failure behaviour; a second reader of a declaration that decides what sync
DELETES is how the two halves come to disagree about whose bytes are whose.

A FINDING (exit 1) is any Python module under `service-kit/` other than
`kit_ownership.py` that either

* walks into the declaration: a `["kit"]` subscript or `.get("kit")` whose receiver
  chain also takes the `"foundry"` key -- `data["tool"]["foundry"]["kit"]` and the
  `.get(...)` spelling both; or
* holds a string constant naming it (`tool.foundry.kit`) outside a docstring -- the
  header sniff `"[tool.foundry.kit]" in text` that once read a dotted-key declaration
  as the default.

What is NOT a reader, each held quiet by the inline control. Two were false reds of
this gate's first matcher, which flagged any TOML parser import and any string naming
the table: importing `tomllib` to read ANOTHER table (`foundry_testkit/real_profile.py`
reads `[tool.foundry.test-resources]`), and a docstring that names the table (sync.py's
and the drift gate's own). The third is a module that only names the file
`pyproject.toml` (`check_folder_structure.py` lists it among a service's root files).
`service-kit/vendor/` is out of scope: vendored platform and service packages are not
kit code and do not read the declaration.

Exit 2 when the gate cannot measure: `kit_ownership.py` is missing (the reader this
gate protects is gone), fewer than MIN_MODULES modules were scanned, or the inline
control did not behave. A scan that read nothing is not a clean scan.
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = "service-kit"
READER = "kit_ownership.py"
OUT_OF_SCOPE = (f"{KIT}/vendor/",)
DECLARATION = "tool.foundry.kit"
#: HAND-WRITTEN floor: the kit held 97 Python modules outside vendor/ when this was
#: written, so a scan of fewer than 50 is a glob that read the wrong tree, not a small kit.
MIN_MODULES = 50


def refuse(message: str) -> int:
    print(f"check-kit-ownership-single-reader: REFUSE -- {message}", file=sys.stderr)
    return 2


def _key(node: ast.AST) -> tuple[str, ast.AST] | None:
    """(key, receiver) for `x["key"]` or `x.get("key", ...)`; None for anything else."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        if isinstance(node.slice.value, str):
            return node.slice.value, node.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value, node.func.value
    return None


def _walks_into_kit(node: ast.AST) -> bool:
    step = _key(node)
    if step is None or step[0] != "kit":
        return False
    receiver = step[1]
    while (step := _key(receiver)) is not None:
        if step[0] == "foundry":
            return True
        receiver = step[1]
    return False


def _docstrings(tree: ast.AST) -> set[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, owners)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def reasons(source: str) -> list[str]:
    """Why a module reads the declaration; empty when it does not."""
    tree = ast.parse(source)
    docstrings = _docstrings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if _walks_into_kit(node):
            found.append(f"line {node.lineno}: walks into tool.foundry.kit")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and DECLARATION in node.value
            and id(node) not in docstrings
        ):
            found.append(f"line {node.lineno}: names {DECLARATION!r} in a string")
    return found


def modules(root: Path) -> list[Path]:
    kit = root / KIT
    return sorted(
        path
        for path in kit.rglob("*.py")
        if "__pycache__" not in path.parts
        and not path.relative_to(root).as_posix().startswith(OUT_OF_SCOPE)
    )


def scan(root: Path) -> tuple[list[str], int]:
    """(findings, modules scanned) over `root`'s kit."""
    findings: list[str] = []
    scanned = modules(root)
    for path in scanned:
        if path.name == READER and path.parent == root / KIT:
            continue
        for reason in reasons(path.read_text(encoding="utf-8")):
            findings.append(f"{path.relative_to(root).as_posix()}: {reason}")
    return findings, len(scanned)


#: (relative path, source, expected to be flagged). The reader itself must be quiet,
#: a second parser must fire, and a module that only names the file must not.
CONTROL = (
    (f"{KIT}/{READER}", "import tomllib\nTABLE = 'tool.foundry.kit'\n", False),
    (f"{KIT}/gates/check_subscripts.py", "def f(d):\n    return d['tool']['foundry']['kit']\n",
     True),
    (f"{KIT}/gates/check_get_chain.py",
     "def f(d):\n    return d.get('tool', {}).get('foundry', {}).get('kit', {})\n", True),
    (f"{KIT}/ci/sniffs_the_header.py", "def f(text):\n    return '[tool.foundry.kit]' in text\n",
     True),
    (f"{KIT}/gates/check_names_it_in_prose.py",
     '"""Reads nothing; documents `[tool.foundry.kit]`."""\n', False),
    (f"{KIT}/testing/reads_another_table.py",
     "import tomllib\n"
     "def f(t):\n    return tomllib.loads(t)['tool']['foundry']['test-resources']\n",
     False),
    (f"{KIT}/gates/check_root_files.py", "ROOT_FILES = ('pyproject.toml', 'README.md')\n", False),
    (f"{KIT}/vendor/some_pkg/reads_it.py", "def f(d):\n    return d['foundry']['kit']\n", False),
)


def control_problems() -> list[str]:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for relative, source, _ in CONTROL:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        findings, _ = scan(root)
        for relative, _, flagged in CONTROL:
            hit = any(finding.startswith(f"{relative}:") for finding in findings)
            if hit != flagged:
                problems.append(
                    f"control {relative} was {'flagged' if hit else 'quiet'}; expected "
                    f"{'flagged' if flagged else 'quiet'}"
                )
    return problems


def main() -> int:
    problems = control_problems()
    if problems:
        return refuse("the inline control did not behave: " + "; ".join(problems))
    if not (ROOT / KIT / READER).is_file():
        return refuse(f"{KIT}/{READER} does not exist; the one reader this gate protects is gone")
    findings, scanned = scan(ROOT)
    if scanned < MIN_MODULES:
        return refuse(f"scanned {scanned} module(s) under {KIT}/, fewer than the floor of "
                      f"{MIN_MODULES}; the glob read the wrong tree")
    if findings:
        print(f"check-kit-ownership-single-reader: FAIL {len(findings)} second reader(s):")
        for finding in findings:
            print(f"  {finding} -- read the declaration through {KIT}/{READER}")
        return 1
    flagged = sum(1 for *_, hit in CONTROL if hit)
    print(
        f"check-kit-ownership-single-reader: PASS {scanned} module(s) under {KIT}/ "
        f"(vendor/ out of scope), one reader ({READER}). Inline control: {len(CONTROL)} "
        f"cases, {flagged} flagged and {len(CONTROL) - flagged} quiet."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
