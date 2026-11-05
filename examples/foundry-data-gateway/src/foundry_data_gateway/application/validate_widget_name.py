"""The one validation rule this worked example enforces.

A free function rather than a method: it is shared by `CreateWidget` and by the
schema layer's own bound, and duplicating it would let the two drift with
nothing going red.
"""

from __future__ import annotations

from foundry_data_gateway.domain import InvalidWidgetName

MAX_NAME_LENGTH = 100


def validate_widget_name(name: str) -> str:
    """Return the normalised name, or raise `InvalidWidgetName`."""
    stripped = name.strip()
    if not stripped:
        raise InvalidWidgetName("widget name must not be empty")
    if len(stripped) > MAX_NAME_LENGTH:
        raise InvalidWidgetName(f"widget name must be at most {MAX_NAME_LENGTH} characters")
    return stripped
