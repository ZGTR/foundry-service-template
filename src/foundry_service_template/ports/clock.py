"""The clock port.

Re-exported from `foundry_contracts` rather than redefined: the estate rule is
"shared protocols are allowed", and a second, structurally identical `Clock`
Protocol in every service is how two definitions drift. This module exists so
that `ports/__init__.py` can present ONE list of the collaborators this service
injects, including the ones whose definition it borrows.
"""

from __future__ import annotations

from foundry_contracts.clock import Clock, FrozenClock, SystemClock

__all__ = ["Clock", "FrozenClock", "SystemClock"]
