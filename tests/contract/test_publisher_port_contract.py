"""The `Publisher` port's contract suite, run against the fake AND both real
in-process implementations.

Three adapters implement `Publisher`. Two of them can be exercised with no
external service at all (`RecordingPublisher`, `InProcessEventBus`) and one
writes a row through the ORM into a real outbox table (`OutboxEventBus`). All
three are run here through the same assertions, which is what makes "swap the
adapter, keep the behaviour" a measurement.

The outbox arm's engine is an in-memory SQLite, and its subject is the
PUBLISHER CONTRACT -- does a published event come back, in order, and does an
unused publisher hold nothing. The TRANSACTIONAL claims about that same adapter
(a rollback leaves neither the widget nor its event; a drain relays each row
once) belong to `tests/integration/test_outbox_transaction.py`, which runs them
against a real PostgreSQL as well. Splitting them that way keeps this file
runnable with no server while the claims that need one are measured where they
are true.

`SqsPublisher` is the fourth and is NOT in this suite: it needs a live AWS
surface, so its contract runs in
`tests/integration/test_sqs_publisher_real_endpoint.py`, which names its skip
when neither LocalStack nor moto-server is reachable. Splitting it out is
deliberate -- folding it in would make this whole file skip on a machine with no
AWS surface, and three arms that CAN run would stop running.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from foundry_service_template.adapters.fake import RecordingPublisher
from foundry_service_template.adapters.real import InProcessEventBus, OutboxEventBus
from foundry_service_template.adapters.real.orm import OutboxRecord
from foundry_service_template.adapters.real.sql_unit_of_work import SqlUnitOfWork
from foundry_service_template.domain import Event
from foundry_service_template.ports import Publisher
from tests.support.engines import local_sessions

OWNED = frozenset({"widgets", "outbox"})


def _event(event_type: str = "widget.created", *, org_id: str = "org_p", day: int = 1) -> Event:
    return Event(
        event_type=event_type,
        org_id=org_id,
        occurred_at=datetime(2026, 1, day, tzinfo=UTC),
        payload={"widget_id": "w1"},
    )


#: Each entry returns (publisher, a reader that yields the event types it holds).
_BUILDERS: dict[str, Callable[[], tuple[Publisher, Callable[[], list[str]]]]] = {}


def _fake() -> tuple[Publisher, Callable[[], list[str]]]:
    publisher = RecordingPublisher()
    return publisher, lambda: [e.event_type for e in publisher.published]


def _in_process() -> tuple[Publisher, Callable[[], list[str]]]:
    bus = InProcessEventBus()
    seen: list[str] = []
    for event_type in ("widget.created", "widget.archived"):
        bus.subscribe(event_type, lambda event: seen.append(event.event_type))
    return bus, lambda: list(seen)


def _outbox() -> tuple[Publisher, Callable[[], list[str]]]:
    # An in-memory SQLite, so this arm needs no server and no Docker. The reader
    # below shares the publisher's session, so it sees what `publish()` flushed.
    uow = SqlUnitOfWork(local_sessions(), owned_tables=OWNED)
    bus = OutboxEventBus(uow)
    return bus, lambda: [
        record.event_type
        for record in uow.session().scalars(
            select(OutboxRecord).order_by(OutboxRecord.occurred_at)
        )
    ]


_BUILDERS.update({"fake-recording": _fake, "real-in-process": _in_process, "real-outbox": _outbox})


@pytest.fixture(params=sorted(_BUILDERS))
def publisher_pair(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[Publisher, Callable[[], list[str]]]]:
    yield _BUILDERS[request.param]()


def test_a_published_event_is_retrievable_by_its_type(
    publisher_pair: tuple[Publisher, Callable[[], list[str]]],
) -> None:
    publisher, read = publisher_pair

    publisher.publish(_event())

    assert read() == ["widget.created"]


def test_publication_order_is_preserved(
    publisher_pair: tuple[Publisher, Callable[[], list[str]]],
) -> None:
    """The two events carry DIFFERENT instants, and that is the contract rather
    than a convenience.

    An in-memory publisher can preserve arrival order because it keeps a list. A
    durable outbox cannot: its rows are read back by a `SELECT`, and this estate's
    own rule is that a listing with no `ORDER BY` returns whatever the index walk
    produced. So the ordering a `Publisher` guarantees is by `occurred_at`, and
    two events stamped at the same instant have no defined relay order on any
    adapter that persists them. Publishing them one microsecond apart would make
    this test pass on a row store that had lost the ordering entirely, so the
    instants are a day apart and deliberately visible.
    """
    publisher, read = publisher_pair

    publisher.publish(_event("widget.created", day=1))
    publisher.publish(_event("widget.archived", day=2))

    assert read() == ["widget.created", "widget.archived"]


def test_publishing_nothing_leaves_nothing(
    publisher_pair: tuple[Publisher, Callable[[], list[str]]],
) -> None:
    _publisher, read = publisher_pair

    assert read() == []


def test_every_arm_satisfies_the_protocol_structurally(
    publisher_pair: tuple[Publisher, Callable[[], list[str]]],
) -> None:
    publisher, _read = publisher_pair

    assert isinstance(publisher, Publisher)


def test_the_suite_ran_every_declared_arm(request: pytest.FixtureRequest) -> None:
    """Positive control for the parameterisation. HAND-WRITTEN literal.

    A suite that collapsed to one arm would pass everything above and prove
    nothing about substitutability, with no assertion going red.
    """
    ids = {
        item.callspec.params["publisher_pair"]
        for item in request.session.items
        if hasattr(item, "callspec") and "publisher_pair" in item.callspec.params
    }
    assert ids == {"fake-recording", "real-in-process", "real-outbox"}, sorted(ids)
