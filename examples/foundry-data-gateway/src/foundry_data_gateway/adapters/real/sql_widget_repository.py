"""The real `WidgetRepository`, mapped through SQLAlchemy 2's typed ORM.

Boundary rule 3 (`foundry_contracts.unit_of_work`) applies exactly as it did to
the two hand-written SQL adapters this replaces: this module opens no session of
its own and writes only to the tables `assert_writes_within` grants -- `widgets`
here, `outbox` in `OutboxEventBus`.

ONE ADAPTER, NOT ONE PER DIALECT, and that is the change worth stating. The
deleted pair needed two classes because the drivers disagreed about everything:
SQLite's connection executes statements itself and PyMySQL's goes through a
cursor; SQLite round-tripped an ISO string and MySQL handed back a naive
`datetime`; the placeholder was `?` in one and `%s` in the other. All three of
those were driver knowledge leaking into a repository, and they are exactly
what an ORM absorbs. What is left here is the record <-> domain mapping, which
is genuine and belongs to this layer.

`add()` FLUSHES, and the difference between flush and commit is the whole
transactional contract. `session.add()` alone only makes the record pending:
the INSERT would not reach the database until something else flushed, so a
duplicate id would surface at commit time, attributed to whatever the request
was doing by then, and `get()` in the same request could read from the identity
map without the database ever having seen the row. Flushing sends the INSERT
inside the caller's open transaction -- the row is visible to this transaction,
an `IntegrityError` for a duplicate is raised HERE, and the commit decision
still belongs entirely to `RequestTransaction`.

`created_at` is normalised to UTC on the way in and re-stamped UTC on the way
out. The write is a conversion (an aware value in any zone is stored as the
same instant); the read is a STATEMENT OF THE COLUMN'S CONTRACT -- the column
holds UTC by construction of this method, so a dialect that dropped the offset
(SQLite has no timezone-aware type) yields the same instant rather than a naive
value the frozen domain type would refuse outright.
"""

from __future__ import annotations

from datetime import UTC

from foundry_contracts.unit_of_work import UnitOfWork, assert_writes_within
from sqlalchemy import select

from foundry_data_gateway.adapters.real.orm import WidgetRecord, as_utc
from foundry_data_gateway.domain import Widget

__all__ = ["SqlWidgetRepository"]

_OWNED_TABLES = frozenset({"widgets"})


class SqlWidgetRepository:
    def __init__(self, uow: UnitOfWork) -> None:
        assert_writes_within(uow, _OWNED_TABLES, package="foundry-data-gateway")
        self._uow = uow

    def add(self, widget: Widget) -> None:
        session = self._uow.session()
        session.add(_to_record(widget))
        session.flush()

    def get(self, org_id: str, widget_id: str) -> Widget | None:
        session = self._uow.session()
        record = session.get(WidgetRecord, {"org_id": org_id, "widget_id": widget_id})
        return None if record is None else _to_widget(record)

    def list_for_org(self, org_id: str) -> list[Widget]:
        session = self._uow.session()
        statement = (
            select(WidgetRecord)
            .where(WidgetRecord.org_id == org_id)
            # `widget_id` breaks ties: two widgets created in the same instant
            # would otherwise come back in whatever order the index walk
            # produced, so two deployments could disagree while both "pass".
            .order_by(WidgetRecord.created_at.asc(), WidgetRecord.widget_id.asc())
        )
        return [_to_widget(record) for record in session.scalars(statement)]


def _to_record(widget: Widget) -> WidgetRecord:
    return WidgetRecord(
        org_id=widget.org_id,
        widget_id=widget.widget_id,
        name=widget.name,
        created_at=widget.created_at.astimezone(UTC),
    )


def _to_widget(record: WidgetRecord) -> Widget:
    return Widget(
        widget_id=record.widget_id,
        org_id=record.org_id,
        name=record.name,
        created_at=as_utc(record.created_at),
    )
