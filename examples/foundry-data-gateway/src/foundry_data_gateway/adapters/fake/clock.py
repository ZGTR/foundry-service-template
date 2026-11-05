"""The fake for `ports.clock.Clock`.

`FrozenClock` comes from `foundry_contracts`, exactly like the real
`SystemClock` does. Re-exported here rather than redefined so that the
"every port has a fake" rule is satisfied by the implementation that already
exists instead of by a second, drifting copy of it.
"""

from __future__ import annotations

from foundry_contracts.clock import FrozenClock

__all__ = ["FrozenClock"]
