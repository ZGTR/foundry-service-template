"""The Pub/Sub port: one `Publisher`, one `Subscriber`.

`Event` itself is NOT here -- it is a domain value (`domain/events/event.py`),
re-exported by `domain/__init__.py`, and this module imports it from there.
Three adapters implement `Publisher` (`adapters/real/in_process_event_bus.py`,
`adapters/real/sqs_event_bus.py`, `adapters/real/outbox_event_bus.py`); only
the in-process one also implements `Subscriber`, because SQS and outbox
delivery happen out of process -- a consumer subscribes to them by running a
separate handler, not by registering an in-memory callback.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from foundry_service_template.domain import Event, EventHandler

__all__ = ["Event", "EventHandler", "Publisher", "Subscriber"]


@runtime_checkable
class Publisher(Protocol):
    def publish(self, event: Event) -> None:
        """Publish `event`. What "published" means is the adapter's choice."""
        ...


@runtime_checkable
class Subscriber(Protocol):
    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register `handler` to be called for every future `event_type`."""
        ...
