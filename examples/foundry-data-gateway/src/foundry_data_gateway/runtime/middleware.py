"""The ASGI middleware that makes §6's guarantees reach a real request.

Two jobs, both of which have to happen per request and neither of which belongs
to a controller:

* bind a correlation id for the whole request, taken from `x-correlation-id`
  when the caller supplied one and minted when not, and echo it back on the
  response so a client can quote it in a bug report; and
* count the request as in-flight for its whole life, so SIGTERM can drain it.

`contextvars` rather than a global: the id must not leak between concurrent
requests, and the token is reset in a `finally` so a raised exception cannot
leave one bound.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from foundry_data_gateway.runtime.logging import (
    CORRELATION_ID_HEADER,
    correlation_id,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from foundry_data_gateway.runtime.shutdown import GracefulShutdown

__all__ = ["CorrelationIdMiddleware", "DrainTrackingMiddleware"]

_Next = Callable[[Request], Awaitable[Response]]

_ACCESS_LOG = logging.getLogger("foundry_data_gateway.access")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: _Next) -> Response:
        incoming = request.headers.get(CORRELATION_ID_HEADER) or new_correlation_id()
        token = set_correlation_id(incoming)
        try:
            response = await call_next(request)
            # Logged HERE, inside the bound context, which is the only place the
            # id is still in scope. An access line emitted after the reset below
            # carries an empty id, which reads as a working trace until someone
            # tries to follow one.
            _ACCESS_LOG.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                },
            )
        finally:
            correlation = correlation_id()
        # Read the id BEFORE unbinding it: resetting first echoes an empty header.
        response.headers[CORRELATION_ID_HEADER] = correlation
        reset_correlation_id(token)
        return response


class DrainTrackingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, shutdown: GracefulShutdown) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._shutdown = shutdown

    async def dispatch(self, request: Request, call_next: _Next) -> Response:
        with self._shutdown.track_request():
            return await call_next(request)
