"""Builds the FastAPI app from a `Container`.

No route registers a try/except -- domain errors are translated to HTTP status
codes here, once, so every controller handler stays a straight-line
parse/delegate/present.

This is also where §6's runtime concerns are attached to the app: the
correlation-id and drain-tracking middleware, the three probe endpoints, and
the lifespan hook that runs the idempotent startup and the graceful drain.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from foundry_contracts.errors import ConfigurationError, PolicyRefused, Unauthorized

from foundry_data_gateway.composition.container import Container
from foundry_data_gateway.domain import InvalidWidgetName, WidgetNotFound
from foundry_data_gateway.runtime import (
    CorrelationIdMiddleware,
    DrainTrackingMiddleware,
    build_router,
)
from foundry_data_gateway.transport.http.controllers import (
    data_tools_controller,
    widgets_controller,
)

_STATUS_BY_ERROR = (
    (Unauthorized, 401),
    (InvalidWidgetName, 422),
    (WidgetNotFound, 404),
    (PolicyRefused, 403),
    (ConfigurationError, 500),
)


def create_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # `start()` is idempotent by contract, which is what makes it safe here:
        # a reload worker, a cold Lambda container and a second TestClient all
        # enter this hook, and a second migration run is how a green deploy breaks.
        container.lifecycle.start()
        try:
            yield
        finally:
            container.lifecycle.stop()

    app = FastAPI(title="foundry-data-gateway", lifespan=lifespan)
    app.add_middleware(DrainTrackingMiddleware, shutdown=container.shutdown)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(build_router(container.health_reporter))
    app.include_router(
        widgets_controller.build_router(container.widget_use_cases, container.authenticate)
    )

    if container.data_tools is not None:
        app.include_router(
            data_tools_controller.build_router(
                container.data_tools,
                container.authenticate,
                container.config.tool_webhook_secret_file,
            )
        )

    for error_type, status_code in _STATUS_BY_ERROR:

        def _handler(request: Request, exc: Exception, *, _code: int = status_code) -> JSONResponse:
            return JSONResponse(status_code=_code, content={"detail": str(exc)})

        app.add_exception_handler(error_type, _handler)

    return app
