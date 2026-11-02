"""The `outbox` table, mapped. One record class per file (§16).

WHY THE OUTBOX IS AN ORM RECORD AND NOT A HAND-WRITTEN INSERT. The whole
guarantee is that the outbox row and the aggregate row are written in ONE
transaction, so that a rolled-back request relays nothing. A raw `INSERT`
issued on a connection the repository does not share is a second transaction
wearing the same name: it commits or rolls back on its own schedule, and the
failure is invisible because both halves still "work". Mapping it onto the same
`Base` means one `Session` flushes both, and the proof is
`tests/integration/test_outbox_transaction.py`, which rolls back and asserts
that NEITHER row survived.

`payload_json` is `Text`, not `JSON`. The column stores the canonical string
the publisher produced, and a dialect `JSON` type re-parses and re-serialises
it, so the bytes that come back are the DATABASE's spelling of the object
rather than the publisher's. Nothing here queries into the payload; what it
needs is that the string relayed is byte-for-byte the string written.

`relayed_at` is the only nullable column, and NULL is its meaning rather than
missing data: unrelayed. The partial-order index below is what makes
`drain_outbox` a bounded scan of the unrelayed tail instead of a full table
sort once the table is a year old.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from foundry_service_template.adapters.real.orm.base import Base

__all__ = ["OutboxRecord"]


class OutboxRecord(Base):
    __tablename__ = "outbox"

    outbox_id: Mapped[str] = mapped_column(String(191), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(191), nullable=False)
    org_id: Mapped[str] = mapped_column(String(191), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    relayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_outbox_relayed_at_occurred_at", "relayed_at", "occurred_at"),)
