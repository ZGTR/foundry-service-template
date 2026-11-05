"""MVC "Controller" for the widget resource: route + delegate, nothing else.

Each handler is three lines of body at most and carries no try/except -- domain
errors become HTTP status codes in `composition/app_factory.py`, once, and
response shaping belongs to `WidgetView`. §2's ceiling is 12 lines per handler
and `scripts/check_size_limits.py` fails the build above it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from fastapi import APIRouter, Request, status
from foundry_contracts.principal import CallerPrincipalV1

from foundry_data_gateway.application import WidgetUseCases
from foundry_data_gateway.transport.http.schemas import (
    WidgetCreateRequest,
    WidgetListResponse,
    WidgetResponse,
)
from foundry_data_gateway.transport.http.views import WidgetView

Authenticate = Callable[[Mapping[str, str]], CallerPrincipalV1]


def build_router(use_cases: WidgetUseCases, authenticate: Authenticate) -> APIRouter:
    router = APIRouter(prefix="/widgets", tags=["widgets"])

    @router.post("", status_code=status.HTTP_201_CREATED, response_model=WidgetResponse)
    def create_widget(payload: WidgetCreateRequest, request: Request) -> dict[str, object]:
        widget = use_cases.create.execute(authenticate(request.headers), name=payload.name)
        return WidgetView.one(widget)

    @router.get("/{widget_id}", response_model=WidgetResponse)
    def get_widget(widget_id: str, request: Request) -> dict[str, object]:
        widget = use_cases.get.execute(authenticate(request.headers), widget_id=widget_id)
        return WidgetView.one(widget)

    @router.get("", response_model=WidgetListResponse)
    def list_widgets(request: Request) -> dict[str, object]:
        found = use_cases.list.execute(authenticate(request.headers))
        return WidgetView.many(found)

    return router
