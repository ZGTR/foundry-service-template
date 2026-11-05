"""`OutboxEventBus` -- the publisher for anything that must not be lost.

`publish()` writes one row into the `outbox` table **inside the caller's
`UnitOfWork`** -- the same session, and therefore the same transaction, as
whatever domain write triggered the event. It delivers nothing itself. A
separate `drain_outbox()` call, run on its own schedule against a *committed*
unit of work, relays every unrelayed row through a downstream `Publisher`
(typically `SqsPublisher`) and marks it relayed.

The transactional guarantee this buys: if the caller's unit of work rolls back,
the outbox row rolls back with it and `drain_outbox()` finds nothing to relay.
`tests/integration/test_outbox_transaction.py` proves that against a REAL
database -- both halves of it, because "the widget survived and the event did
not" and "the event survived and the widget did not" are different defects and
a test that only counts events cannot tell them apart.

WHY THIS FILE HAS ONE PATH NOW AND USED TO HAVE TWO. It previously branched on
`hasattr(session, "execute") and not isinstance(session, dict)`, so a dict-based
in-memory unit of work took a list-append path and a SQL one took a raw
`INSERT` path. Two implementations of one method is two sets of edge cases, and
the dict arm was the one the tests ran: a rolled-back dict is cleared by the
fake's own `rollback()`, which proves the FAKE discards rows on rollback and
says nothing about whether a database does. Both arms are gone. There is one
path, it goes through the ORM record, and the rollback claim is now measured
where rollback actually happens.

`drain_outbox()` FLUSHES AND DOES NOT COMMIT, deliberately. The relay and the
`relayed_at` stamp belong to whoever owns the transaction -- the scheduled
entrypoint that called it -- because only that caller knows whether its own
work alongside the relay also succeeded. This is boundary rule 3 applied to the
drain: a component that commits on someone else's behalf decides an outcome it
cannot see.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from foundry_contracts.ids import new_id
from foundry_contracts.unit_of_work import UnitOfWork, assert_writes_within
from sqlalchemy import select

from foundry_data_gateway.adapters.real.orm import OutboxRecord, as_utc
from foundry_data_gateway.ports import Event, Publisher

__all__ = ["OutboxEventBus", "drain_outbox"]

_OWNED_TABLES = frozenset({"outbox"})


class OutboxEventBus:
    """Implements `Publisher`. Writes only; never relays."""

    def __init__(self, uow: UnitOfWork) -> None:
        assert_writes_within(uow, _OWNED_TABLES, package="foundry-data-gateway")
        self._uow = uow

    def publish(self, event: Event) -> None:
        session = self._uow.session()
        session.add(
            OutboxRecord(
                outbox_id=new_id("obx"),
                event_type=event.event_type,
                org_id=event.org_id,
                occurred_at=event.occurred_at.astimezone(UTC),
                payload_json=json.dumps(dict(event.payload)),
                relayed_at=None,
            )
        )
        # Flushed, not committed: the INSERT joins the caller's open
        # transaction now, so a rollback discards it, and the commit decision
        # stays with the caller.
        session.flush()


def drain_outbox(uow: UnitOfWork, downstream: Publisher, *, batch_size: int = 100) -> int:
    """Relay every unrelayed outbox row through `downstream`. Returns the count relayed.

    Call this from a scheduled worker, never from the request path -- "drained
    separately" is the point: the request that wrote the row must not block on
    delivery.
    """
    session = uow.session()
    unrelayed = (
        select(OutboxRecord)
        .where(OutboxRecord.relayed_at.is_(None))
        .order_by(OutboxRecord.occurred_at.asc())
        .limit(batch_size)
    )
    relayed = 0
    for record in session.scalars(unrelayed).all():
        downstream.publish(
            Event(
                event_type=record.event_type,
                org_id=record.org_id,
                occurred_at=as_utc(record.occurred_at),
                payload=json.loads(record.payload_json),
            )
        )
        record.relayed_at = datetime.now(UTC)
        relayed += 1
    session.flush()
    return relayed
