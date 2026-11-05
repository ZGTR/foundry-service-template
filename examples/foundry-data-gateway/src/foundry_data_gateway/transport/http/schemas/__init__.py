"""Request/response DTO codecs. Wire shapes live here, never in `domain/`."""

from __future__ import annotations

from foundry_data_gateway.transport.http.schemas.widgets_schema import (
    WidgetCreateRequest,
    WidgetListResponse,
    WidgetResponse,
)

__all__ = ["WidgetCreateRequest", "WidgetListResponse", "WidgetResponse"]
