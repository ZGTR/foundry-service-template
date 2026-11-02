"""A tracer that emits one JSON line per completed span through an injected
writer -- never ``print``. A tracer that hardcodes stdout cannot be redirected,
captured in a test, or pointed at a log shipper; injecting the stream is what
keeps this adapter usable anywhere a file-like ``write(str)`` object exists.
"""

from __future__ import annotations

import contextvars
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from foundry_contracts.canon import canonical_json
from foundry_contracts.clock import Clock, SystemClock
from foundry_contracts.ids import new_id

from foundry_agent_trace._redact import DenyListRedactor, Redactor
from foundry_agent_trace._span import RecordedSpan, Span, _RecordingSpan, new_attributes


@runtime_checkable
class Writer(Protocol):
    def write(self, line: str) -> None: ...


class ConsoleTracer:
    """Writes a JSON line per finished span to an injected ``Writer``.

    A write failure (a broken pipe, a full disk, a closed stream) is exactly
    the back-pressure case ``dropped_spans`` exists for: the span is counted as
    dropped and the failure never reaches the caller whose code the span was
    wrapping.
    """

    def __init__(
        self,
        writer: Writer,
        *,
        clock: Clock | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self._writer = writer
        self._clock = clock or SystemClock()
        self._redactor = redactor or DenyListRedactor()
        self._current: contextvars.ContextVar[RecordedSpan | None] = contextvars.ContextVar(
            f"foundry_agent_trace.console.{id(self)}", default=None
        )
        self.dropped_spans = 0

    def start_span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attributes: Mapping[str, object] | None = None,
        links: Sequence[str] = (),
    ) -> Span:
        parent = self._current.get()
        record = RecordedSpan(
            trace_id=parent.trace_id if parent else new_id("trace"),
            span_id=new_id("span"),
            parent_span_id=parent.span_id if parent else None,
            name=name,
            kind=kind,
            started_at=self._clock.now(),
            attributes=new_attributes(attributes, self._redactor),
            links=tuple(links),
        )
        token = self._current.set(record)

        def _on_end(finished: RecordedSpan) -> None:
            self._current.reset(token)
            try:
                self._writer.write(_to_json_line(finished))
            except Exception:
                self.dropped_spans += 1

        return _RecordingSpan(record, redactor=self._redactor, clock=self._clock, on_end=_on_end)


def _to_json_line(record: RecordedSpan) -> str:
    payload = {
        "trace_id": record.trace_id,
        "span_id": record.span_id,
        "parent_span_id": record.parent_span_id,
        "name": record.name,
        "kind": record.kind,
        "started_at": record.started_at.isoformat(),
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
        "attributes": dict(record.attributes),
        "error": record.error,
        "links": list(record.links),
    }
    return canonical_json(payload) + "\n"
