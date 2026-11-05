"""The transactional outbox: one transaction, both rows, or neither.

THE GUARANTEE, stated as the thing that can go wrong. `CreateWidget` writes a
widget and publishes an event inside one `RequestTransaction`. If those two
writes land in different transactions, the service has two failure modes and
neither is visible from a passing test: an event relayed for a widget that was
rolled away, and a widget created whose event nobody will ever receive. Counting
outbox rows cannot tell them apart, so every assertion below checks BOTH tables.

THIS FILE REPLACES `test_outbox_event_bus.py`, AND THE REPLACEMENT IS THE POINT.
That file ran the same claims against `InMemoryUnitOfWork`, a fake whose
"session" was a `dict` and whose `rollback()` was `for rows in ...: rows.clear()`.
So "a rolled-back unit of work publishes nothing" was measured against a list
comprehension that clears a list. It could not have failed, and it said nothing
about a database. A transaction has no honest substitute: the behaviour under
test IS the engine's.

TWO ARMS, and they are not redundant. The `sqlite` arm needs no server, so the
mechanism -- flush inside the caller's transaction, rollback discards, drain
relays each row once -- is proved on every checkout and every CI run. The
`real-postgres` arm is the engine this estate deploys, reached through
`deploy/compose.yml` and migrated by `alembic upgrade head`; it is where the
claim is true of the thing that actually runs. A skip on the second is named and
`REQUIRE_REAL_RESOURCES=1` turns it into a failure.

The sqlite arm builds its schema with `create_all`, deliberately and only here:
its subject is transaction MECHANICS, not schema fidelity, and the migrations
are exercised by the postgres arm, which is the arm whose schema a deployment
shares.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from foundry_contracts.principal import PrincipalMinter
from foundry_contracts.unit_of_work import TableOwnershipViolation
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from foundry_data_gateway.adapters.fake import RecordingPublisher
from foundry_data_gateway.adapters.real.orm import OutboxRecord, WidgetRecord
from foundry_data_gateway.adapters.real.outbox_event_bus import OutboxEventBus, drain_outbox
from foundry_data_gateway.adapters.real.serialized_transaction import RequestTransaction
from foundry_data_gateway.adapters.real.sql_unit_of_work import SqlUnitOfWork
from foundry_data_gateway.adapters.real.sql_widget_repository import SqlWidgetRepository
from foundry_data_gateway.application.create_widget import CreateWidget
from foundry_data_gateway.ports import Event
from tests.support.engines import local_sessions

OWNED = frozenset({"widgets", "outbox"})
EVENT = Event(
    event_type="widget.created", org_id="org_1", occurred_at=datetime(2026, 1, 1, tzinfo=UTC)
)

#: HAND-WRITTEN, and asserted below against the arms that actually ran.
EXPECTED_ARMS = frozenset({"sqlite", "real-postgres"})


class _FrozenClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


class _FailingPublisher:
    """Publishes nothing and raises, so the request's transaction must roll back."""

    def publish(self, event: Event) -> None:
        raise RuntimeError("downstream refused")


@pytest.fixture(params=sorted(EXPECTED_ARMS))
def sessions(request: pytest.FixtureRequest, tmp_path) -> Iterator[sessionmaker[Session]]:
    if request.param == "real-postgres":
        yield request.getfixturevalue("postgres_sessions")
        return
    # A FILE, not `:memory:`. `_rows()` reads on a second session, which is the
    # only way to tell "committed" from "visible to the session that wrote it" --
    # and that distinction is what every assertion in this file rests on.
    yield local_sessions(tmp_path / "outbox.db")


@pytest.fixture
def uow(sessions: sessionmaker[Session]) -> Iterator[SqlUnitOfWork]:
    """A unit of work that is ALWAYS closed, however the test ended.

    A session left open holds its transaction, and on PostgreSQL an open
    transaction that touched a table blocks the next test's `TRUNCATE` on an
    ACCESS EXCLUSIVE lock. Untimed, that is a suite that stops dead with no
    failing test and no output -- the least debuggable outcome available, and
    measured on this file before this fixture existed. `conftest.py` sets a
    `lock_timeout` as the second half of the same guard.
    """
    unit = SqlUnitOfWork(sessions, owned_tables=OWNED)
    try:
        yield unit
    finally:
        unit.rollback()
        unit.close()


def _rows(sessions: sessionmaker[Session]) -> tuple[list[WidgetRecord], list[OutboxRecord]]:
    """Read BOTH tables on a SEPARATE session, so nothing pending is counted.

    Reading through the unit of work under test would see its own unflushed or
    uncommitted state, which is exactly the state these tests are asking about.
    """
    with sessions() as session:
        return (
            list(session.scalars(select(WidgetRecord))),
            list(session.scalars(select(OutboxRecord))),
        )


def test_a_committed_request_leaves_the_widget_AND_its_event(
    uow: SqlUnitOfWork, sessions: sessionmaker[Session]
) -> None:
    use_case = CreateWidget(
        repository=SqlWidgetRepository(uow),
        publisher=OutboxEventBus(uow),
        clock=_FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)),
        transaction=RequestTransaction(uow),
    )
    principal = PrincipalMinter(issuer="t").mint(
        org_id="org_1", subject_id="u", scopes={"widgets:write"}
    )

    created = use_case.execute(principal, name="Sprocket")

    widgets, outbox = _rows(sessions)
    assert [w.widget_id for w in widgets] == [created.widget_id]
    assert [o.event_type for o in outbox] == ["widget.created"]


def test_a_failed_publish_leaves_NEITHER_the_widget_NOR_the_event(
    uow: SqlUnitOfWork, sessions: sessionmaker[Session]
) -> None:
    """The half a row count cannot see.

    Asserting only that no event was relayed would pass on a service that wrote
    the widget and lost the event -- which is the more expensive of the two
    defects, because the data is there and nothing downstream will ever hear
    about it.
    """
    use_case = CreateWidget(
        repository=SqlWidgetRepository(uow),
        publisher=_FailingPublisher(),
        clock=_FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)),
        transaction=RequestTransaction(uow),
    )
    principal = PrincipalMinter(issuer="t").mint(
        org_id="org_1", subject_id="u", scopes={"widgets:write"}
    )

    with pytest.raises(RuntimeError, match="downstream refused"):
        use_case.execute(principal, name="never-committed")

    widgets, outbox = _rows(sessions)
    assert widgets == []
    assert outbox == []


def test_a_committed_outbox_row_is_relayed_once(uow: SqlUnitOfWork) -> None:
    downstream = RecordingPublisher()
    OutboxEventBus(uow).publish(EVENT)
    uow.commit()

    first_pass = drain_outbox(uow, downstream)
    second_pass = drain_outbox(uow, downstream)
    uow.commit()

    assert (first_pass, second_pass) == (1, 0)
    assert [e.event_type for e in downstream.published] == ["widget.created"]


def test_a_rolled_back_publish_relays_nothing(
    uow: SqlUnitOfWork, sessions: sessionmaker[Session]
) -> None:
    downstream = RecordingPublisher()
    OutboxEventBus(uow).publish(EVENT)
    uow.rollback()

    relayed = drain_outbox(uow, downstream)

    assert relayed == 0
    assert downstream.published == []
    assert _rows(sessions)[1] == []


def test_a_relayed_row_carries_the_payload_the_publisher_wrote(uow: SqlUnitOfWork) -> None:
    """Round-trip the payload, not just the count.

    A drain that relayed the right NUMBER of events with an empty payload would
    satisfy every assertion above, and the downstream consumer is the one that
    finds out.
    """
    downstream = RecordingPublisher()
    OutboxEventBus(uow).publish(
        Event(
            event_type="widget.created",
            org_id="org_1",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            payload={"widget_id": "w1", "name": "Sprocket"},
        )
    )
    uow.commit()

    drain_outbox(uow, downstream)

    assert downstream.published[0].payload == {"widget_id": "w1", "name": "Sprocket"}
    assert downstream.published[0].occurred_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_publish_is_refused_when_the_unit_of_work_does_not_own_the_outbox(
    sessions: sessionmaker[Session],
) -> None:
    """Boundary rule 3, asserted against the REAL unit of work rather than a double.

    Constructing one opens no connection, so a test of the grant costs nothing;
    a fake here would only prove that a fake carries the frozenset it was given.
    """
    uow = SqlUnitOfWork(sessions, owned_tables=frozenset({"widgets"}))

    with pytest.raises(TableOwnershipViolation):
        OutboxEventBus(uow)


def test_this_file_ran_against_every_declared_arm(request: pytest.FixtureRequest) -> None:
    """The positive control for the parameterisation, same shape as the contract suite."""
    ids = {
        item.callspec.params["sessions"]
        for item in request.session.items
        if hasattr(item, "callspec") and "sessions" in item.callspec.params
    }

    assert EXPECTED_ARMS <= ids, f"the outbox suite ran only: {sorted(ids)}"
