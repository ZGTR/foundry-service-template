"""The span shape every adapter exposes, plus the machinery shared by adapters
that actually record.

Lossiness is contractual, not accidental: a tracer may drop a span under
back-pressure (a full buffer, a writer that fails), and dropping must never
raise into the caller or change the caller's result. ``start_span`` therefore
always returns a usable ``Span`` -- a recording one, or ``_NoopSpan`` when the
span was dropped -- and every method on it is safe to call however the span's
life unfolds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Span(Protocol):
    def set_attribute(self, key: str, value: object) -> None: ...

    def record_error(self, error: BaseException) -> None: ...

    def end(self) -> None: ...

    def __enter__(self) -> Span: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None: ...


class _NoopSpan:
    """Returned when there is nothing to record: the default tracer, or a span
    a recording tracer dropped under back-pressure. Every call is a no-op."""

    def set_attribute(self, key: str, value: object) -> None:
        return None

    def record_error(self, error: BaseException) -> None:
        return None

    def end(self) -> None:
        return None

    def __enter__(self) -> _NoopSpan:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


@dataclass
class RecordedSpan:
    """What a recording adapter keeps about one span. Mutable in place so a
    live ``_RecordingSpan`` and its finished record are the same object."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    started_at: datetime
    ended_at: datetime | None = None
    attributes: dict[str, object] = field(default_factory=dict)
    links: tuple[str, ...] = ()
    error: str | None = None


class _RecordingSpan:
    """Shared span implementation for adapters that keep a ``RecordedSpan``.

    ``on_end`` is where an adapter's own failure mode lives (a full buffer, a
    writer exception) -- this class never decides whether a span was kept or
    dropped, it only guarantees the span's own lifecycle methods never raise.
    """

    def __init__(
        self,
        record: RecordedSpan,
        *,
        redactor,
        clock,
        on_end: Callable[[RecordedSpan], None],
    ) -> None:
        self._record = record
        self._redactor = redactor
        self._clock = clock
        self._on_end = on_end
        self._ended = False

    def set_attribute(self, key: str, value: object) -> None:
        if self._ended:
            return
        self._record.attributes[key] = self._redactor.redact(key, value)

    def record_error(self, error: BaseException) -> None:
        if self._ended:
            return
        self._record.error = f"{type(error).__name__}: {error}"

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        self._record.ended_at = self._clock.now()
        self._on_end(self._record)

    def __enter__(self) -> _RecordingSpan:
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> bool:
        if exc is not None:
            self.record_error(exc)
        self.end()
        return False


def new_attributes(attributes: Mapping[str, object] | None, redactor) -> dict[str, object]:
    """Apply ``redactor`` to a caller-supplied attribute mapping at span start."""
    if not attributes:
        return {}
    return {key: redactor.redact(key, value) for key, value in attributes.items()}
