"""A stand-in PRODUCT for the AgentChat tool-webhook template: one write, one outbox fact.

Real tables on the real PostgreSQL. `Product.write` inserts a note; `Product.stage_fact`
inserts a fact and returns its id, or returns None when told to fail, which is the
failure the template must turn into a non-2xx with nothing committed.
"""

from __future__ import annotations

import hashlib
import hmac

from agent_chat_adapter.webhook import TOOL_CALLS
from sqlalchemy import Column, Integer, MetaData, String, Table, func, insert, select

SECRET = "test-webhook-secret"
PRODUCT = MetaData()
NOTES = Table(
    "agent_chat_test_note", PRODUCT, Column("id", Integer, primary_key=True),
    Column("tool_call_id", String, nullable=False), Column("detail", String, nullable=False),
)
FACTS = Table(
    "agent_chat_test_fact", PRODUCT, Column("id", Integer, primary_key=True),
    Column("type", String, nullable=False), Column("tool_call_id", String, nullable=False),
)
TABLES = (TOOL_CALLS, NOTES, FACTS)


def sign(body: bytes, stamp: str, secret: str = SECRET) -> str:
    mac = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def empty_tables(engine) -> None:
    for table in TABLES:
        table.create(engine, checkfirst=True)
    with engine.begin() as tx:
        for table in TABLES:
            tx.execute(table.delete())


def counts(engine) -> tuple[int, int, int]:
    with engine.connect() as cx:
        return tuple(cx.execute(select(func.count()).select_from(t)).scalar_one() for t in TABLES)


class Product:
    """The two injected callables, recording how often the write ran."""

    def __init__(self, stage: bool = True) -> None:
        self.stage, self.writes = stage, 0

    def write(self, tx, tool_call):
        self.writes += 1
        tx.execute(insert(NOTES).values(tool_call_id=tool_call.tool_call_id,
                                        detail=tool_call.input["detail"]))
        topic = str(tool_call.input.get("topic", tool_call.name))
        return {"state_patch": {"notes": {topic: tool_call.input["detail"]}},
                "artifact": {"id": tool_call.tool_call_id, "kind": "note", "title": topic}}

    def stage_fact(self, tx, tool_call):
        if not self.stage:
            return None
        fact = insert(FACTS).values(type="note.recorded", tool_call_id=tool_call.tool_call_id)
        return tx.execute(fact.returning(FACTS.c.id)).scalar_one()
