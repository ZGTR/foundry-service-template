"""Domain-specific errors for the widget example.

Both extend `foundry_contracts.errors.FoundryError` -- callers of this service catch the
shared vocabulary, never a bespoke exception type per package.
"""

from __future__ import annotations

from foundry_contracts.errors import FoundryError


class InvalidWidgetName(FoundryError):
    """A widget name failed the one validation rule this domain enforces."""


class WidgetNotFound(FoundryError):
    """No widget with the given id exists for this org."""
