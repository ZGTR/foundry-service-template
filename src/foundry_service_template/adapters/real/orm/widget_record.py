"""The `widgets` table, mapped. One record class per file (§16).

A RECORD IS NOT THE DOMAIN TYPE, and keeping them apart is the whole reason
this file exists rather than an `@dataclass`-with-`Mapped`-annotations on
`domain.Widget`. Imperative mapping onto the domain class was considered and
rejected: it hands the ORM identity and mutation semantics over a type the
domain declares `frozen=True`, so `session.merge()` and the identity map would
be able to change a value object behind its owner's back. The domain stays a
frozen dataclass; `SqlWidgetRepository` maps record <-> domain in one direction
each, and that mapping is the only place either shape is named twice.

EVERY COLUMN DECLARES `nullable` EXPLICITLY, including where the default would
agree. A guarantee that holds only because of a library default is one
unwritten keyword away from becoming false, and nothing goes red when it does.
`primary_key=True` implies `nullable=False`, so the two composite-key columns
say it by implication rather than twice.

`DateTime(timezone=True)` is `timestamptz` on Postgres and carries the offset
through unchanged. On other dialects it does not: SQLite has no timezone-aware
type, so a value written aware comes back NAIVE. The repository re-stamps UTC
on read, which is a statement of this column's contract -- it holds a UTC
instant, by construction on the write path -- rather than a conversion. The
template's contract suite asserts the aware round trip on a REAL PostgreSQL
because that is the arm where the column type is what answers.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from foundry_service_template.adapters.real.orm.base import Base

__all__ = ["WidgetRecord"]


class WidgetRecord(Base):
    __tablename__ = "widgets"

    #: `(org_id, widget_id)` is the key, org first. A key on `widget_id` alone
    #: would let one tenant's id shadow another's; org first also makes the
    #: primary-key index answer `list_for_org` without a second structure.
    org_id: Mapped[str] = mapped_column(String(191), primary_key=True)
    widget_id: Mapped[str] = mapped_column(String(191), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # The listing order, indexed. `list_for_org` sorts by `created_at` and
        # breaks ties on `widget_id`; without this the query is a sort over
        # every row the tenant owns.
        Index("ix_widgets_org_id_created_at", "org_id", "created_at"),
    )
