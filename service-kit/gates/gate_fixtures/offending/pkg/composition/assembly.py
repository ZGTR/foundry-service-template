import sqlite3

from pkg.adapters.real import SqliteGadgetStore
from pkg.application import RegisterGadget
from pkg.composition.config import Settings
from pkg.transport.http.controllers import gadgets


def assemble(settings: Settings) -> dict[str, object]:
    store = SqliteGadgetStore(sqlite3.connect(settings.database_path))
    return {"router": gadgets.build_router(RegisterGadget()), "store": store}
