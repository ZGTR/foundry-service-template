"""`transport` -- MVC "View" and "Controller", for every inbound protocol.

THE PUBLIC CONTRACT of this submodule. `transport` may import `application`,
`ports` and `domain`. It may NOT import `adapters` or `composition`: a
controller that reaches for a concrete adapter has made the wiring decision
that belongs to exactly one module, and `scripts/check_layering.py` fails the
build on it.
"""

from __future__ import annotations

from foundry_service_template.transport import events, http

__all__ = ["events", "http"]
