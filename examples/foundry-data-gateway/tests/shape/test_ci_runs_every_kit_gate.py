"""Every `service-kit/gates/check_*.py` has its own step in `.github/workflows/ci.yml`.

`check_vendor_digests.py` was red on this template for four days
while CI stayed green: `service-kit/ci/verify.sh` globs every gate, but the workflow
names its gates one step each, and this one was never given a step. A gate CI does
not run is a gate the estate learns about from whoever next runs verify.sh by hand.
The population is the glob, never a list, so a gate added tomorrow is covered by default.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2]
GATES = TEMPLATE / "service-kit" / "gates"
CI = TEMPLATE / ".github" / "workflows" / "ci.yml"


def test_every_kit_gate_has_a_ci_step() -> None:
    gates = sorted(p.name for p in GATES.glob("check_*.py"))
    assert len(gates) >= 10, f"the glob found {len(gates)} gates; the population is not being read"
    run_lines = [line for line in CI.read_text().splitlines() if line.strip().startswith("run:")]
    invoked = {m.group(1) for line in run_lines
               for m in re.finditer(r"service-kit/gates/(check_\w+\.py)", line)}
    assert invoked, "no gate invocation parsed from ci.yml; the matcher is broken, not the workflow"
    missing = [g for g in gates if g not in invoked]
    assert not missing, f"kit gates with no ci.yml step: {missing}"
