"""The default tracer. Zero cost: it allocates nothing per span and keeps no
state beyond the drop counter it never increments -- there is nothing to drop
when nothing is ever recorded."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_agent_trace._span import Span, _NoopSpan


class NoopTracer:
    """The tracer every host gets by default until it wires a real one."""

    def __init__(self) -> None:
        self.dropped_spans = 0

    def start_span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attributes: Mapping[str, object] | None = None,
        links: Sequence[str] = (),
    ) -> Span:
        del name, kind, attributes, links
        return _NoopSpan()
