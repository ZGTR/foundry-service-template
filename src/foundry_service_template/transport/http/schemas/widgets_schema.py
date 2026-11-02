"""Wire shapes for the widget resource.

Kept separate from `domain.Widget` so the domain type can change without the
HTTP contract changing, and vice versa. These are DTO codecs only: no
`from_domain` constructor lives here any more, because that was the response
SHAPING decision and §2 puts shaping in the view. The schema declares what the
wire accepts and emits; `WidgetView` decides what to put in it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WidgetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WidgetResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    widget_id: str
    org_id: str
    name: str
    created_at: datetime


class WidgetListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    widgets: list[WidgetResponse]
