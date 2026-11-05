"""§6: structured JSON logging with a correlation id.

Two properties, both testable without a running server:

* every record is ONE line of JSON, so a log shipper parses it rather than
  guessing at a multi-line traceback boundary; and
* every record carries the correlation id of the request that produced it, from
  a `contextvars.ContextVar` -- not a parameter threaded through every call, and
  not a module-level global, which would leak one request's id into another's
  log line under concurrency.

The id is read from the inbound `x-correlation-id` header when present and
minted when not, so a caller's trace survives the hop.
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from typing import Any

__all__ = [
    "CORRELATION_ID_HEADER",
    "JsonLogFormatter",
    "configure_json_logging",
    "correlation_id",
    "new_correlation_id",
    "reset_correlation_id",
    "set_correlation_id",
]

CORRELATION_ID_HEADER = "x-correlation-id"

_CORRELATION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "foundry_correlation_id", default=""
)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def correlation_id() -> str:
    return _CORRELATION_ID.get()


def set_correlation_id(value: str) -> contextvars.Token[str]:
    return _CORRELATION_ID.set(value)


def reset_correlation_id(token: contextvars.Token[str]) -> None:
    """Unbind the id this request bound. Always called in a `finally`."""
    _CORRELATION_ID.reset(token)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


class JsonLogFormatter(logging.Formatter):
    """One record, one line of JSON, correlation id always present."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_json_logging(level: str = "INFO", *, stream: Any = None) -> logging.Handler:
    """Install the JSON formatter as the ROOT handler, replacing what is there.

    Replacing rather than appending is the point: a second plain-text handler
    left in place emits every record twice, once unparseable, and a log shipper
    that indexes both reports double the error rate.
    """
    handler = logging.StreamHandler(stream) if stream is not None else logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    return handler
