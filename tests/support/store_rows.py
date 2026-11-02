"""This service's store questions, answered twice.

The bases, the owned-table refusal and the "own connection" rule live in
``foundry_testkit.store_rows`` and are identical in every service. What lives
HERE is the only part that cannot be shared: which questions this service's
seeded tests need to ask, and how each store answers them.

A new service rewrites this file and nothing else to get row assertions.
"""

from __future__ import annotations

from typing import Any

from foundry_testkit.store_rows import FakeStoreRows, SqlStoreRows

#: Every table this service owns, as ``Base.metadata`` names them. Written out
#: rather than read from the mapping, per the base class's own note: an
#: inspector that derives its vocabulary from the thing it inspects accepts
#: whatever the mapping happens to say.
OWNED_TABLES = ("widgets", "outbox")


class SqlWidgetRows(SqlStoreRows):
    """PostgreSQL's answer, read on a connection the service is not using."""

    owned_tables = OWNED_TABLES

    def widgets(self, org_id: str) -> list[dict[str, Any]]:
        rows = self.select(
            "select widget_id, name, created_at from widgets "
            "where org_id = :org_id order by created_at, widget_id",
            {"org_id": org_id},
        )
        return [
            {"widget_id": row[0], "name": row[1], "created_at": row[2]} for row in rows
        ]


class FakeWidgetRows(FakeStoreRows):
    """The in-memory store's answer to the same questions.

    It reads ``_by_key`` rather than calling ``list_for_org``, which is the
    base class's rule and worth restating at the one place it bites: asking the
    repository's own read method would make this inspector agree with the read
    path by construction, so a write that never landed and a read that invented
    its result would be indistinguishable.
    """

    owned_tables = OWNED_TABLES

    def widgets(self, org_id: str) -> list[dict[str, Any]]:
        store = self.inner()
        found = [
            {"widget_id": widget.widget_id, "name": widget.name, "created_at": widget.created_at}
            for (owner, _), widget in store._by_key.items()
            if owner == org_id
        ]
        return sorted(found, key=lambda row: (row["created_at"], row["widget_id"]))

    def _count(self, table: str) -> int:
        store = self.inner()
        if table == "widgets":
            return len(store._by_key)
        # The fake publisher records events in memory and never writes `outbox`,
        # so 0 here is the TRUTH about the fake arm rather than a gap. A seeded
        # test that asserts a non-zero outbox count is therefore a real-arm
        # assertion and belongs in `tests/integration`, not in the shared tier.
        return 0
