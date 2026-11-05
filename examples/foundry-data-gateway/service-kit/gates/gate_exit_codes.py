"""The exit-code vocabulary every §9 gate in this package shares.

    0  green      -- the gate ran over real input and found nothing
    1  finding    -- the gate ran and found a violation
    2  refusal    -- the gate could not make a trustworthy measurement
    3  named skip -- a required input is genuinely absent HERE, and the gate
                     says exactly what was missing. NEVER a pass.

The 2/3 distinction is the one that gets collapsed, and collapsing it is how a
gate becomes decorative. A REFUSAL means the gate's own controls failed, or its
population came back empty, or its fixture went missing -- the measurement
cannot be trusted, so no verdict is issued. A NAMED SKIP means the input is
legitimately not present in this checkout (an unchecked-out submodule, no
network, no Docker daemon) and the gate names it. A gate that exits 0 in either
case is reporting a green it never measured.
"""

from __future__ import annotations

import sys

__all__ = ["FINDING", "GREEN", "NAMED_SKIP", "REFUSAL", "finding", "refuse", "skip"]

GREEN = 0
FINDING = 1
REFUSAL = 2
NAMED_SKIP = 3


def refuse(message: str) -> int:
    print(f"REFUSED: {message}", file=sys.stderr)
    return REFUSAL


def skip(message: str) -> int:
    print(f"SKIPPED (exit {NAMED_SKIP}, NOT a pass): {message}", file=sys.stderr)
    return NAMED_SKIP


def finding(message: str) -> int:
    print(f"FINDING: {message}", file=sys.stderr)
    return FINDING
