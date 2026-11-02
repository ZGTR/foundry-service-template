"""Run the SERVICE's own gates: every `scripts/check_*.py`, found by glob.

`.github/workflows/ci.yml` is a kit PLACEMENT: its bytes are
hashed into MANIFEST.json and `check_kit_drift.py` reds a service that edits it. That is
right for the kit gates, which the template owns, and it left a service with no place to
wire a gate of its own. Measured in comms-core 8e80b9f: `check_product_neutral_core.py`
(C-2/AC13) was run by no CI step at all, and the service had to smuggle its gates into
the unit suite as subprocesses to get them run anywhere.

This gate is the one extension point. The template's ci.yml calls it, and it runs
whatever `scripts/check_*.py` the service ships, so a service adds a gate by adding a
file and never touches the workflow.

VERDICTS, per the kit vocabulary (`gate_exit_codes.py`):

* a service gate exiting 0 is green, and 3 is a NAMED SKIP it printed itself. Both are
  tolerated, and the skip is counted and printed, never folded into "passed";
* 1 is a finding, and 2 is a gate that could not measure. Both are red here: a service
  gate that cannot trust its own measurement is not a green one;
* any other code, a crash (a traceback on stderr) or a timeout is red too, because a
  crash exits 1 and would otherwise read exactly like a finding the gate reasoned about.
  The exit code decides first: a gate that exits 0 or 3 after printing a traceback it
  caught is green or skipped, and "crashed" only names a red exit.

Every `scripts/check_*` file is accounted for, not only the `.py` ones. `RUN_ELSEWHERE` names the gates the workflow runs in their own step,
and the coverage line counts them; any other suffix is a refusal naming the file,
because a glob that silently skips a suffix reports a population it never saw.

A service with NO gates of its own is legitimate (the template's one,
`check_kit_ownership_single_reader.py`, is template-only and never emitted), so
an empty glob is green and says so in its coverage line. What cannot be empty is the
CONTROL: before any verdict, the classifier is run over a scratch directory holding one
gate per exit class, and it must call exactly the red ones red. A runner that has never
been shown to fail has not been shown to run.

The classifier is a parameter of `control_problems` and `main` (default `run_one`), so
a test blinds it by passing one rather than by patching this module.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from gate_common import PACKAGE_ROOT
from gate_exit_codes import FINDING, GREEN, NAMED_SKIP, refuse

#: Codes a service gate may exit with and still leave CI green.
TOLERATED = frozenset({GREEN, NAMED_SKIP})
TIMEOUT_SECONDS = 600
TRACEBACK = "Traceback (most recent call last)"

#: Service gates ci.yml runs in a step of their own, so this runner leaves them alone.
RUN_ELSEWHERE = frozenset({"check_service_iac.sh"})

#: The inline control: one scratch gate per class, and the verdict each must get.
CONTROL_GATES = {
    "check_green.py": ("raise SystemExit(0)", True),
    "check_named_skip.py": ("raise SystemExit(3)", True),
    "check_finding.py": ("raise SystemExit(1)", False),
    "check_refusal.py": ("raise SystemExit(2)", False),
    "check_crash.py": ("raise RuntimeError('boom')", False),
    "check_caught_traceback.py": (
        "import traceback\ntry:\n    raise ValueError('handled')\n"
        "except ValueError:\n    traceback.print_exc()\nraise SystemExit(0)",
        True,
    ),
}


def discover(root: Path) -> list[Path]:
    return sorted((root / "scripts").glob("check_*.py"))


def unrunnable(root: Path) -> list[Path]:
    """`scripts/check_*` files this runner neither runs nor knows to be run elsewhere."""
    return sorted(
        path
        for path in (root / "scripts").glob("check_*")
        if path.is_file() and path.suffix != ".py" and path.name not in RUN_ELSEWHERE
    )


def run_one(gate: Path, root: Path) -> tuple[bool, str]:
    """(tolerated, one-line account) for one service gate, run from the service root."""
    try:
        done = subprocess.run(
            [sys.executable, str(gate)], cwd=root, capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"{gate.name}: timed out after {TIMEOUT_SECONDS}s"
    ok = done.returncode in TOLERATED
    if not ok and TRACEBACK in done.stderr:
        last = done.stderr.strip().splitlines()[-1]
        return False, f"{gate.name}: crashed (exit {done.returncode}): {last}"
    label = {GREEN: "green", NAMED_SKIP: "named skip", FINDING: "finding", 2: "refusal"}
    kind = label.get(done.returncode, "unexpected exit")
    detail = "" if ok else f"\n{(done.stdout + done.stderr).strip()}"
    return ok, f"{gate.name}: exit {done.returncode} ({kind}){detail}"


Classifier = Callable[[Path, Path], tuple[bool, str]]


def control_problems(classify: Classifier = run_one) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="service-gates-control-") as tmp:
        root = Path(tmp)
        (root / "scripts").mkdir()
        for name, (body, _) in CONTROL_GATES.items():
            (root / "scripts" / name).write_text(body + "\n")
        found = [g.name for g in discover(root)]
        if sorted(found) != sorted(CONTROL_GATES):
            return [f"control: discovery found {found}, expected {sorted(CONTROL_GATES)}"]
        return [
            f"control: {g.name} classified tolerated={ok}, expected {CONTROL_GATES[g.name][1]}"
            for g in discover(root)
            for ok, _ in [classify(g, root)]
            if ok != CONTROL_GATES[g.name][1]
        ]


def main(argv: list[str] | None = None, classify: Classifier = run_one) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=PACKAGE_ROOT, help="the service root")
    root = parser.parse_args(argv).root.resolve()

    problems = control_problems(classify)
    if problems:
        return refuse("the runner's own control failed:\n" + "\n".join(f"  {p}" for p in problems))

    stray = unrunnable(root)
    if stray:
        names = ", ".join(p.name for p in stray)
        return refuse(
            f"scripts/ holds gate(s) this runner cannot run: {names}. Write the gate in "
            "Python, or run it in its own ci.yml step and add it to RUN_ELSEWHERE."
        )
    present = (p.name for p in (root / "scripts").glob("check_*"))
    elsewhere = sorted(name for name in present if name in RUN_ELSEWHERE)
    gates = discover(root)
    results = [classify(g, root) for g in gates]
    for _, line in results:
        print(f"  {line}")
    red = [line for ok, line in results if not ok]
    skipped = sum(1 for _, line in results if "(named skip)" in line)
    coverage = (
        f"{len(gates)} service gate(s) under {root / 'scripts'}; {len(gates)} ran, "
        f"{len(gates) - skipped - len(red)} green, {skipped} named skip(s), {len(red)} red; "
        f"{len(elsewhere)} run by their own ci.yml step ({', '.join(elsewhere) or 'none'})"
    )
    if red:
        print(f"FINDING: {coverage}", file=sys.stderr)
        return FINDING
    print(f"PASS: {coverage}; control: {len(CONTROL_GATES)} classes classified correctly.")
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
