"""Two doors onto one governed read, plus the receipts an org can audit.

* `POST /v1/tool-webhook` -- foundry-agent's signed record-tool webhook. The caller is the
  agent runtime, proven by HMAC; the tenant is the conversation's owner it signs into
  the body (`principal`), never anything the model wrote.
* `POST /mcp` -- the same tools for any MCP client (JSON-RPC over HTTP, stateless:
  `initialize`, `tools/list`, `tools/call`). The tenant is the authenticated principal.
* `GET /v1/receipts` -- the caller's own org's receipts.

A refusal is returned to the model as data (`ok: false` with the rule), so it can say
why it cannot answer; it is still a refusal in the receipt.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from foundry_contracts.principal import CallerPrincipalV1

from foundry_data_gateway.application.run_data_tool import RunDataTool
from foundry_data_gateway.domain.data_tools import ToolRefused

Authenticate = Callable[[Mapping[str, str]], CallerPrincipalV1]
MAX_SKEW_SECONDS = 300
MCP_PROTOCOL_VERSION = "2026-07-28"


def _verify(secret_file: str | None, headers: Mapping[str, str], body: bytes) -> None:
    if not secret_file:
        raise HTTPException(503, "TOOL_WEBHOOK_SECRET_FILE is not configured")
    secret = Path(secret_file).read_text().strip().encode()
    stamp = headers.get("x-foundry-timestamp", "")
    signature = headers.get("x-foundry-signature", "")
    if not stamp.isdigit() or abs(time.time() - int(stamp)) > MAX_SKEW_SECONDS:
        raise HTTPException(401, "stale or missing timestamp")
    signed = stamp.encode() + b"." + body
    expected = "sha256=" + hmac.new(secret, signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "bad signature")


def _answer(tools: RunDataTool, **call: object) -> dict[str, object]:
    try:
        result = tools.execute(**call)  # type: ignore[arg-type]
    except ToolRefused as refused:
        return {"ok": False, "refused": refused.rule, "reason": str(refused)}
    return {"ok": True, **result.as_json()}


def webhook_answer(tools: RunDataTool, call: Mapping[str, object]) -> dict[str, object]:
    """The record-tool ack: `result` for the model, `artifact` for the chat view."""
    owner = call["principal"]
    assert isinstance(owner, Mapping)
    answer = _answer(
        tools,
        org_id=owner["org_id"],
        subject=owner["sub"],
        tool_name=call["name"],
        args=call["input"],
        call_id=call["tool_call_id"],
        channel="webhook",
        conversation_id=call["conversation_id"],
    )
    card = {"id": call["tool_call_id"], "kind": "data_table", "title": call["name"], "data": answer}
    return {"result": answer, "artifact": card}


def mcp_answer(
    tools: RunDataTool, principal: CallerPrincipalV1, msg: Mapping[str, object]
) -> dict[str, object]:
    method, rid = msg.get("method"), msg.get("id")
    params = msg.get("params") or {}
    assert isinstance(params, Mapping)
    if method == "initialize":
        result: object = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "foundry-data-gateway", "version": "0.1.0"},
        }
    elif method == "tools/list":
        listed = tools.list_tools(principal.org_id)
        result = {
            "tools": [
                {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                for t in listed
            ]
        }
    elif method == "tools/call":
        answer = _answer(
            tools,
            org_id=principal.org_id,
            subject=principal.subject_id,
            tool_name=params.get("name", ""),
            args=params.get("arguments", {}),
            call_id=f"mcp_{principal.org_id}_{rid}_{time.time_ns()}",
            channel="mcp",
        )
        text = json.dumps(answer, default=str)
        result = {
            "content": [{"type": "text", "text": text}],
            "structuredContent": answer,
            "isError": not answer["ok"],
        }
    else:
        error = {"code": -32601, "message": f"unknown method {method}"}
        return {"jsonrpc": "2.0", "id": rid, "error": error}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def build_router(
    tools: RunDataTool, authenticate: Authenticate, secret_file: str | None
) -> APIRouter:
    router = APIRouter(tags=["data-tools"])

    @router.post("/v1/tool-webhook")
    async def tool_webhook(request: Request) -> dict[str, object]:
        body = await request.body()
        _verify(secret_file, request.headers, body)
        return webhook_answer(tools, json.loads(body))

    @router.post("/mcp")
    async def mcp(request: Request) -> dict[str, object]:
        principal = authenticate(request.headers)
        return mcp_answer(tools, principal, await request.json())

    @router.get("/v1/receipts")
    def receipts(request: Request) -> dict[str, object]:
        org_id = authenticate(request.headers).org_id
        return {"org_id": org_id, "receipts": list(tools.receipts.list_for_org(org_id))}

    return router
