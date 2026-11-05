"""Proves the InProcessEventBus's one real contract: a failing subscriber
never breaks the publisher, and never breaks its siblings either.
"""

from __future__ import annotations

from datetime import UTC, datetime

from foundry_data_gateway.adapters.real.in_process_event_bus import InProcessEventBus
from foundry_data_gateway.ports import Event

EVENT = Event(
    event_type="widget.created", org_id="org_1", occurred_at=datetime(2026, 1, 1, tzinfo=UTC)
)


def test_a_failing_subscriber_does_not_break_the_publisher() -> None:
    bus = InProcessEventBus()
    bus.subscribe("widget.created", lambda _e: (_ for _ in ()).throw(RuntimeError("boom")))

    bus.publish(EVENT)  # must not raise

    assert bus.failure_count == 1


def test_a_failing_subscriber_does_not_break_its_siblings() -> None:
    bus = InProcessEventBus()
    calls: list[str] = []

    def failing(_event: Event) -> None:
        calls.append("first")
        raise RuntimeError("boom")

    def healthy(_event: Event) -> None:
        calls.append("second")

    bus.subscribe("widget.created", failing)
    bus.subscribe("widget.created", healthy)

    bus.publish(EVENT)

    assert calls == ["first", "second"]
    assert bus.failure_count == 1


def test_multiple_failures_are_all_counted() -> None:
    bus = InProcessEventBus()

    def always_fails(_event: Event) -> None:
        raise RuntimeError("boom")

    bus.subscribe("widget.created", always_fails)
    bus.subscribe("widget.created", always_fails)
    bus.subscribe("widget.created", always_fails)

    bus.publish(EVENT)

    assert bus.failure_count == 3


def test_subscribers_of_other_event_types_are_not_called() -> None:
    bus = InProcessEventBus()
    calls: list[str] = []
    bus.subscribe("widget.deleted", lambda _e: calls.append("should not run"))

    bus.publish(EVENT)

    assert calls == []
