"""The tracer port. Every adapter -- noop, in-memory, console, and anything a
host adds later -- implements this and nothing more."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from foundry_agent_trace._span import Span


@runtime_checkable
class Tracer(Protocol):
    dropped_spans: int

    def start_span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attributes: Mapping[str, object] | None = None,
        links: Sequence[str] = (),
    ) -> Span:
        """Start a span, usable as a context manager. Never raises, never
        returns ``None`` -- a span dropped under back-pressure still returns a
        usable (inert) ``Span``."""
        ...
