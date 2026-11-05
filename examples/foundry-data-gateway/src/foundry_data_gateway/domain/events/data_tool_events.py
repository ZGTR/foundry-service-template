"""The fact every data-tool call publishes, allowed or refused."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_contracts.clock import Clock

from foundry_data_gateway.domain.events.event import Event

DATA_TOOL_CALLED = "data_tool.called"


def data_tool_called(org_id: str, receipt: Mapping[str, object], clock: Clock) -> Event:
    return Event(
        event_type=DATA_TOOL_CALLED, org_id=org_id, occurred_at=clock.now(), payload=receipt
    )
