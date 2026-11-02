"""The fake for `ports.event_bus.Publisher`."""

from __future__ import annotations

from foundry_service_template.ports import Event


class RecordingPublisher:
    """Captures every published event in order. Never raises, never delivers."""

    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, event: Event) -> None:
        self.published.append(event)
