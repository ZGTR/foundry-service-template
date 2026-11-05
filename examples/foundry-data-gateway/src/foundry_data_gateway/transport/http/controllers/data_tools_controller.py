"""MVC controllers for data tools: route, authenticate, delegate, present. Nothing else.

| Route | Caller | Tenant comes from |
|---|---|---|
| `POST /mcp` | any MCP client | the authenticated principal |
| `POST /v1/tool-webhook` | foundry-agent (HMAC) | the conversation owner foundry-agent signs |
| `GET /v1/tools`, `POST /v1/tools/{name}:call` | a product backend (REST) | the principal |
| `GET /v1/agent-definition` | a product backend building a chat turn | the principal |
| `GET /v1/receipts`, `GET /v1/usage` | the tenant's auditor | the principal |
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping

from fastapi import APIRouter, Request
from foundry_contracts.principal import CallerPrincipalV1

from foundry_data_gateway.application import DataToolUseCases, ToolCall
from foundry_data_gateway.transport.http.data_tool_protocols import (
    McpEndpoint,
    SignedToolWebhook,
    answer_call,
)
from foundry_data_gateway.transport.http.views import DataToolView

Authenticate = Callable[[Mapping[str, str]], CallerPrincipalV1]


def build_router(
    use_cases: DataToolUseCases,
    authenticate: Authenticate,
    webhook_secret: Callable[[], bytes] | None,
) -> APIRouter:
    router = APIRouter(tags=["data-tools"])
    mcp, webhook = McpEndpoint(use_cases), SignedToolWebhook(use_cases, webhook_secret)

    @router.post("/mcp")
    async def mcp_rpc(request: Request) -> dict[str, object]:
        principal = authenticate(request.headers)
        return mcp.handle(principal, await request.json())

    @router.post("/v1/tool-webhook")
    async def tool_webhook(request: Request) -> dict[str, object]:
        body = await request.body()
        webhook.verify(request.headers, body)
        return webhook.handle(json.loads(body))

    @router.get("/v1/tools")
    def list_tools(request: Request) -> dict[str, object]:
        tools = use_cases.list.execute(authenticate(request.headers).org_id)
        return {"tools": [DataToolView.catalogue_entry(t) for t in tools]}

    @router.post("/v1/tools/{name}:call")
    async def call_tool(name: str, request: Request) -> dict[str, object]:
        who = authenticate(request.headers)
        call = ToolCall(
            who.org_id,
            who.subject_id,
            name,
            await request.json(),
            f"rest_{uuid.uuid4().hex}",
            "rest",
        )
        return answer_call(use_cases, call)

    @router.get("/v1/agent-definition")
    def agent_definition(request: Request) -> dict[str, object]:
        bundle, digest = use_cases.agent.execute(authenticate(request.headers).org_id)
        return {"definition": bundle, "definition_digest": digest}

    @router.get("/v1/receipts")
    def receipts(request: Request) -> dict[str, object]:
        org_id = authenticate(request.headers).org_id
        return {"org_id": org_id, "receipts": use_cases.audit.receipts_for(org_id)}

    @router.get("/v1/usage")
    def usage(request: Request) -> dict[str, object]:
        org_id = authenticate(request.headers).org_id
        return {"org_id": org_id, "usage": use_cases.audit.usage_for(org_id)}

    return router
