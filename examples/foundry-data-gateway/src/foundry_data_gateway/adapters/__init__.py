"""`adapters` -- concrete port implementations.

THE PUBLIC CONTRACT of this submodule is the pair of sub-packages below, not a
flat name list: `real` and `fake` are selected as a SET by the composition root
(a local run takes the in-memory widget repository, a deployed run takes the
MySQL one), so the unit another submodule imports is the family, never a single
class plucked out of the middle.

`adapters` may import `ports` and `domain`. It may not import `application`,
`transport`, `runtime` or `composition` -- an adapter that knows which use case
calls it is not an adapter.
"""

from __future__ import annotations

from foundry_data_gateway.adapters import fake, real

__all__ = ["fake", "real"]
