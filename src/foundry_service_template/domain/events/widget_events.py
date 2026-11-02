"""The one event this domain publishes."""

from __future__ import annotations

from foundry_contracts.clock import Clock

from foundry_service_template.domain.events.event import Event
from foundry_service_template.domain.model.widget import Widget

WIDGET_CREATED = "widget.created"


def widget_created(widget: Widget, clock: Clock) -> Event:
    return Event(
        event_type=WIDGET_CREATED,
        org_id=widget.org_id,
        occurred_at=clock.now(),
        payload={"widget_id": widget.widget_id, "name": widget.name},
    )
