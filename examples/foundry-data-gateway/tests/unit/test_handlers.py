from __future__ import annotations

import functools
import json
from datetime import UTC, datetime

from foundry_data_gateway.adapters.fake import RecordingPublisher
from foundry_data_gateway.adapters.real.outbox_event_bus import OutboxEventBus, drain_outbox
from foundry_data_gateway.adapters.real.sql_unit_of_work import SqlUnitOfWork
from foundry_data_gateway.ports import Event
from foundry_data_gateway.transport.events import handle_scheduled_drain, handle_sqs_record
from tests.support.engines import local_sessions


def test_handle_scheduled_drain_relays_committed_rows() -> None:
    # A real unit of work on a server-free engine: this test's subject is the
    # HANDLER's contract with the callable it is handed, and it needs a working
    # drain rather than a faithful database.
    uow = SqlUnitOfWork(local_sessions(), owned_tables=frozenset({"outbox"}))
    outbox = OutboxEventBus(uow)
    outbox.publish(
        Event(
            event_type="widget.created",
            org_id="org_1",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    uow.commit()
    downstream = RecordingPublisher()
    # main.py builds exactly this kind of bound callable and hands it to the
    # handler -- the handler itself never imports the outbox adapter.
    drain = functools.partial(drain_outbox, uow, downstream)

    relayed = handle_scheduled_drain(drain)

    assert relayed == 1
    assert len(downstream.published) == 1


def test_handle_sqs_record_extracts_the_event() -> None:
    record = {
        "body": json.dumps(
            {
                "event_type": "widget.created",
                "org_id": "org_1",
                "payload": {"widget_id": "widget_1"},
            }
        )
    }

    result = handle_sqs_record(record)

    assert result == {
        "event_type": "widget.created",
        "org_id": "org_1",
        "payload": {"widget_id": "widget_1"},
    }
