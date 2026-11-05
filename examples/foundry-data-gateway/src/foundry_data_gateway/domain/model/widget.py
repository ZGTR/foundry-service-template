"""The worked example domain type.

Deliberately small: a widget is a name that belongs to an org. It exists to
exercise every layer of the template, not to model a real product.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Widget:
    widget_id: str
    org_id: str
    name: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.widget_id:
            raise ValueError("Widget.widget_id must be non-empty")
        if not self.org_id:
            raise ValueError("Widget.org_id must be non-empty")
        if self.created_at.tzinfo is None:
            raise ValueError("Widget.created_at must be timezone-aware")
