"""Domain events: what happened, in this service's own vocabulary.

`Event` is a DOMAIN value, not a transport concern. It lived in
`ports/event_bus.py` until the v2 restructure, which made `domain` import
`ports` to describe its own facts -- the one direction §1 forbids outright.
The port now imports the domain value, which is the direction that was always
meant.
"""

from __future__ import annotations

from foundry_service_template.domain.events.event import Event, EventHandler
from foundry_service_template.domain.events.widget_events import WIDGET_CREATED, widget_created

__all__ = ["WIDGET_CREATED", "Event", "EventHandler", "widget_created"]
