"""The fake for `repositories.widget_repository.WidgetRepository`."""

from __future__ import annotations

from foundry_data_gateway.domain import Widget


class InMemoryWidgetRepository:
    """A dict-backed repository. Every unit and integration test uses this."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], Widget] = {}

    def add(self, widget: Widget) -> None:
        key = (widget.org_id, widget.widget_id)
        if key in self._by_key:
            raise ValueError(f"widget {widget.widget_id} already exists for org {widget.org_id}")
        self._by_key[key] = widget

    def get(self, org_id: str, widget_id: str) -> Widget | None:
        return self._by_key.get((org_id, widget_id))

    def list_for_org(self, org_id: str) -> list[Widget]:
        # `widget_id` BREAKS TIES, matching `SqlWidgetRepository`'s ORDER BY.
        # Sorting on `created_at` alone is stable in Python, so two widgets
        # created in the same instant came back in INSERTION order here and in
        # index order from the database -- a divergence both adapters could pass
        # their own tests with. Found by the contract suite the moment it gained
        # a tie-breaking arm, which is the whole reason that suite runs one set
        # of assertions against both.
        return sorted(
            (w for (o, _), w in self._by_key.items() if o == org_id),
            key=lambda w: (w.created_at, w.widget_id),
        )
