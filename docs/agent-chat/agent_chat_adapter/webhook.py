"""AgentChat record-tool webhook: the product side of the runtime's signed tool contract.

The runtime POSTs `{conversation_id, turn_id, tool_call_id, epoch, name, input, principal}`
(`principal` = the conversation's owner `{org_id, sub}`, None from an older runtime) with
`X-Foundry-Timestamp`, `X-Foundry-Signature: sha256=<HMAC(secret, "<ts>.<body>")>` and
`Idempotency-Key: <tool_call_id>`. This route verifies the signature and the timestamp's
age, refuses an epoch lower than one already committed for the conversation, commits each
`tool_call_id` once, and runs the product write and the staging of its outbox fact in ONE
transaction. A fact that could not be staged (`stage_fact` returns None) rolls everything
back and answers 503: no row, no display data. 2xx carries `{state_patch?, artifact?}`.
"""

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import JSON, BigInteger, Column, MetaData, String, Table, func, select, update
from sqlalchemy.dialects.postgresql import insert

#: The dedupe table. A product adds it to its own migrations.
TOOL_CALLS = Table(
    "agent_chat_tool_call", MetaData(), Column("tool_call_id", String(128), primary_key=True),
    Column("conversation_id", String(128), nullable=False, index=True),
    Column("epoch", BigInteger, nullable=False), Column("display", JSON))


@dataclass(frozen=True)
class ToolCall:
    conversation_id: str
    turn_id: str
    tool_call_id: str
    epoch: int
    name: str
    input: dict
    principal: dict | None = None  # {org_id, sub}: the conversation's owner (newer runtimes)


class _Refused(Exception):
    """args = (status, error)."""


def _parse(raw: bytes, key: str | None) -> ToolCall:
    try:
        body = {"principal": None, **json.loads(raw)}  # an older runtime sends no principal
        call = ToolCall(**{name: body[name] for name in ToolCall.__dataclass_fields__})
    except (ValueError, TypeError, KeyError):
        call = None
    if call is None or type(call.epoch) is not int or not isinstance(call.input, dict):
        raise _Refused(400, "malformed tool call")
    if key != call.tool_call_id:
        raise _Refused(400, "Idempotency-Key must equal tool_call_id")
    return call


def tool_webhook_router(
    *, secret: Callable[[], str],
    begin: Callable[[], Any],  # e.g. engine.begin: a context manager yielding a Connection
    write: Callable[[Any, ToolCall], dict],  # (tx, call) -> display {state_patch?, artifact?}
    stage_fact: Callable[[Any, ToolCall], object | None],  # (tx, call) -> fact id; None = failed
    now: Callable[[], float] = time.time, max_age_s: int = 300, path: str = "/agent-chat/tools",
) -> APIRouter:
    router = APIRouter()

    def commit(call: ToolCall) -> dict:
        this = TOOL_CALLS.c.tool_call_id == call.tool_call_id
        with begin() as tx:  # a raise anywhere below rolls back the claim, the write and the fact
            claimed = tx.execute(insert(TOOL_CALLS).values(
                tool_call_id=call.tool_call_id, conversation_id=call.conversation_id,
                epoch=call.epoch).on_conflict_do_nothing().returning(TOOL_CALLS.c.epoch)).first()
            if claimed is None:  # already committed: acknowledge again, change nothing
                return tx.execute(select(TOOL_CALLS.c.display).where(this)).scalar_one() or {}
            highest = tx.execute(select(func.max(TOOL_CALLS.c.epoch)).where(
                TOOL_CALLS.c.conversation_id == call.conversation_id)).scalar_one()
            if highest > call.epoch:
                raise _Refused(409, "stale epoch")
            display = write(tx, call) or {}
            if stage_fact(tx, call) is None:
                raise _Refused(503, "fact not staged")
            tx.execute(update(TOOL_CALLS).values(display=display).where(this))
            return display

    @router.post(path)
    async def deliver(request: Request) -> JSONResponse:
        raw = await request.body()
        stamp = request.headers.get("x-foundry-timestamp", "")
        expected = "sha256=" + hmac.new(
            secret().encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        try:
            if not hmac.compare_digest(expected, request.headers.get("x-foundry-signature", "")):
                raise _Refused(401, "bad signature")
            if not stamp.isdigit() or abs(now() - int(stamp)) > max_age_s:
                raise _Refused(401, "stale timestamp")
            display = await run_in_threadpool(
                commit, _parse(raw, request.headers.get("idempotency-key")))
        except _Refused as refused:
            status, error = refused.args
            return JSONResponse({"error": error}, status_code=status)
        return JSONResponse({k: display[k] for k in ("state_patch", "artifact") if k in display})

    return router
