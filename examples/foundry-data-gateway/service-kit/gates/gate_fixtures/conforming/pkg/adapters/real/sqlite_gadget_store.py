import sqlite3

from pkg.domain import Gadget


class SqliteGadgetStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def put(self, gadget: Gadget) -> None:
        self._connection.execute(
            "INSERT INTO gadgets VALUES (?, ?, ?)",
            (gadget.gadget_id, gadget.tenant_id, gadget.label),
        )

    def fetch(self, tenant_id: str, gadget_id: str) -> Gadget | None:
        row = self._connection.execute(
            "SELECT gadget_id, tenant_id, label FROM gadgets WHERE tenant_id=? AND gadget_id=?",
            (tenant_id, gadget_id),
        ).fetchone()
        return None if row is None else Gadget(*row)
