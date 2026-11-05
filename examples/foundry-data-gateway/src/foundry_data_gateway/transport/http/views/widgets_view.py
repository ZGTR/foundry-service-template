"""The widget resource's presenter.

Every response shape for `/widgets` is decided HERE. A controller that formats
a response inline is a §2 violation, and `scripts/check_mvc_separation.py`
fails the build on one: the measured failure mode is a list endpoint growing a
comprehension in the route body, which is the moment the wire shape stops being
reviewable in one place.
"""

from __future__ import annotations

from collections.abc import Iterable

from foundry_data_gateway.domain import Widget


class WidgetView:
    """Stateless by design -- a presenter that holds state is a use case."""

    @staticmethod
    def one(widget: Widget) -> dict[str, object]:
        return {
            "widget_id": widget.widget_id,
            "org_id": widget.org_id,
            "name": widget.name,
            "created_at": widget.created_at,
        }

    @classmethod
    def many(cls, widgets: Iterable[Widget]) -> dict[str, object]:
        return {"widgets": [cls.one(widget) for widget in widgets]}
