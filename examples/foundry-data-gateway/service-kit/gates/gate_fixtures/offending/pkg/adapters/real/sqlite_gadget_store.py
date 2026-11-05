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
