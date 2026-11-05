"""Protocol adapters for the two machine doors: MCP JSON-RPC and the signed webhook.

Both turn a wire message into one `ToolCall` and an answer back into the wire shape;
neither decides anything. Kept out of the controllers so each route handler stays a
parse/delegate/present step, and out of `application` because JSON-RPC framing and
HMAC headers are transport concerns.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections.abc import Callable, Mapping

from foundry_contracts.errors import Unauthorized
from foundry_contracts.principal import CallerPrincipalV1

from foundry_data_gateway.application import DataToolUseCases, ToolCall
from foundry_data_gateway.domain import ToolRefused
from foundry_data_gateway.transport.http.views import DataToolView

MCP_PROTOCOL_VERSION = "2026-07-28"
MAX_SKEW_SECONDS = 300


def answer_call(use_cases: DataToolUseCases, call: ToolCall) -> dict[str, object]:
    """Run one call; a refusal is an answer (`ok: false` + rule), never an exception."""
    try:
        return DataToolView.answer(use_cases.call.execute(call))
    except ToolRefused as refused:
        return DataToolView.refusal(refused)


class McpEndpoint:
    """Stateless MCP over HTTP: `initialize`, `tools/list`, `tools/call`."""

    def __init__(self, use_cases: DataToolUseCases) -> None:
        self._use_cases = use_cases

    def handle(self, principal: CallerPrincipalV1, msg: Mapping[str, object]) -> dict[str, object]:
        method, rid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        assert isinstance(params, Mapping)
        if method == "initialize":
            result: object = {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "foundry-data-gateway", "version": "1.0.0"},
            }
        elif method == "tools/list":
            tools = self._use_cases.list.execute(principal.org_id)
            result = {"tools": [DataToolView.mcp_tool(t) for t in tools]}
        elif method == "tools/call":
            call = ToolCall(
                principal.org_id,
                principal.subject_id,
                str(params.get("name", "")),
                params.get("arguments", {}),
                f"mcp_{uuid.uuid4().hex}",
                "mcp",
            )
            result = DataToolView.mcp_call(answer_call(self._use_cases, call))
        else:
            error = {"code": -32601, "message": f"method not found: {method}"}
            return {"jsonrpc": "2.0", "id": rid, "error": error}
        return {"jsonrpc": "2.0", "id": rid, "result": result}


class SignedToolWebhook:
    """foundry-agent's record-tool webhook, verified by HMAC over `<timestamp>.<body>`."""

    def __init__(self, use_cases: DataToolUseCases, secret: Callable[[], bytes] | None) -> None:
        self._use_cases = use_cases
        self._secret = secret

    def verify(self, headers: Mapping[str, str], body: bytes) -> None:
        if self._secret is None:
            raise Unauthorized(
                "no webhook key: set TOOL_WEBHOOK_SECRET or TOOL_WEBHOOK_SECRET_FILE"
            )
        secret = self._secret()
        stamp = headers.get("x-foundry-timestamp", "")
        if not stamp.isdigit() or abs(time.time() - int(stamp)) > MAX_SKEW_SECONDS:
            raise Unauthorized("stale or missing webhook timestamp")
        expected = (
            "sha256=" + hmac.new(secret, stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected, headers.get("x-foundry-signature", "")):
            raise Unauthorized("bad webhook signature")

    def handle(self, call: Mapping[str, object]) -> dict[str, object]:
        owner = call["principal"]
        assert isinstance(owner, Mapping)
        tool_call = ToolCall(
            str(owner["org_id"]),
            str(owner["sub"]),
            str(call["name"]),
            call["input"],
            str(call["tool_call_id"]),
            "webhook",
            str(call["conversation_id"]),
        )
        answer = answer_call(self._use_cases, tool_call)
        card = DataToolView.chat_card(tool_call.call_id, tool_call.tool_name, answer)
        return {"result": answer, "artifact": card}
