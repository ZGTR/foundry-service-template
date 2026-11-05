"""The persistence contract the widget use cases depend on.

A repository IS a port: it is a consumer-owned interface over an outbound
collaborator. It sat in its own `repositories/` directory until the v2
restructure, which gave the same concept two homes and made every gate over
"the ports" need a second path to be correct. §1 collapses them.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from foundry_data_gateway.domain import Widget


@runtime_checkable
class WidgetRepository(Protocol):
    def add(self, widget: Widget) -> None:
        """Persist `widget`. Raises if a widget with the same id already exists."""
        ...

    def get(self, org_id: str, widget_id: str) -> Widget | None:
        """Return the widget, or None if it does not exist for this org."""
        ...

    def list_for_org(self, org_id: str) -> list[Widget]:
        """Return every widget belonging to `org_id`, oldest first."""
        ...
