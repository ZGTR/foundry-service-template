"""W3C trace-context propagation helpers shared by service boundaries."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager

from opentelemetry import propagate
from opentelemetry.context import attach, detach


def inject_trace_context(carrier: MutableMapping[str, str]) -> None:
    """Inject the current span using the configured W3C propagator."""

    propagate.inject(carrier)


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    inject_trace_context(carrier)
    return carrier.get("traceparent")


@contextmanager
def remote_trace_context(carrier: Mapping[str, str]) -> Iterator[None]:
    """Make an extracted remote parent current for the enclosed server work."""

    normalized = {str(key).lower(): value for key, value in carrier.items()}
    token = attach(propagate.extract(normalized))
    try:
        yield
    finally:
        detach(token)
