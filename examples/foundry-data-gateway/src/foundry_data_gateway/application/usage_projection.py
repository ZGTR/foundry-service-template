"""The `data_tool.called` subscriber: keeps the per-tenant usage read model current.

It is a consumer like any other on the bus (in-process locally; the outbox/SQS path in a
deployment calls the same `handle`), so metering, billing or alerting are added as more
subscribers, never as more code in the call path.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_data_gateway.domain import DATA_TOOL_CALLED, Event
from foundry_data_gateway.ports import Subscriber, UsageStore


@dataclass
class UsageProjection:
    usage: UsageStore

    def handle(self, event: Event) -> None:
        payload = event.payload
        self.usage.add(
            event.org_id,
            str(payload["tool"]),
            allowed=payload["decision"] == "allow",
            rows=int(payload.get("rows", 0)),  # type: ignore[call-overload]
        )

    def subscribe_to(self, bus: Subscriber) -> None:
        bus.subscribe(DATA_TOOL_CALLED, self.handle)
