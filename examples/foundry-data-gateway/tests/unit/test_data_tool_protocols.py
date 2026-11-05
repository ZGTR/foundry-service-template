"""The MCP and signed-webhook doors, over the same use cases."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from foundry_contracts.errors import Unauthorized
from foundry_contracts.principal import PrincipalMinter

from foundry_data_gateway.adapters.fake import InMemoryUsageStore, StaticCatalogueLoader
from foundry_data_gateway.application import (
    AgentDefinition,
    DataToolUseCases,
    ListDataTools,
    ReadAudit,
)
from foundry_data_gateway.transport.http.data_tool_protocols import McpEndpoint, SignedToolWebhook
from tests.unit.test_call_data_tool import wire
from tests.unit.test_tool_policy_and_arguments import GRANTED, policy

PRINCIPAL = PrincipalMinter(issuer="test").mint(org_id="org_a", subject_id="u1", scopes=set())


@pytest.fixture
def use_cases() -> DataToolUseCases:
    call, _, receipts, _, _ = wire()
    loader = StaticCatalogueLoader(policy(GRANTED))
    return DataToolUseCases(
        call,
        ListDataTools(loader),
        AgentDefinition(loader, "m"),
        ReadAudit(receipts, InMemoryUsageStore()),
    )


def rpc(endpoint: McpEndpoint, method: str, params: dict | None = None) -> dict:
    return endpoint.handle(
        PRINCIPAL, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    )


def test_mcp_lists_and_calls_with_schemas_and_structured_content(
    use_cases: DataToolUseCases,
) -> None:
    mcp = McpEndpoint(use_cases)
    assert rpc(mcp, "initialize")["result"]["serverInfo"]["name"] == "foundry-data-gateway"
    tool = rpc(mcp, "tools/list")["result"]["tools"][0]
    assert set(tool) == {"name", "description", "inputSchema", "outputSchema", "annotations"}
    called = rpc(mcp, "tools/call", {"name": "orders", "arguments": {}})["result"]
    assert called["isError"] is False and called["structuredContent"]["row_count"] == 1
    refused = rpc(mcp, "tools/call", {"name": "orders", "arguments": {"days": 99}})["result"]
    assert (
        refused["isError"] is True
        and refused["structuredContent"]["refused"] == "invalid_arguments"
    )
    assert rpc(mcp, "resources/list")["error"]["code"] == -32601


def sign(secret: bytes, body: bytes, stamp: int | None = None) -> dict[str, str]:
    ts = str(stamp or int(time.time()))
    return {
        "x-foundry-timestamp": ts,
        "x-foundry-signature": "sha256="
        + hmac.new(secret, ts.encode() + b"." + body, hashlib.sha256).hexdigest(),
    }


def test_the_webhook_verifies_its_signature_and_answers_for_the_signed_owner(
    use_cases,
) -> None:
    hook = SignedToolWebhook(use_cases, lambda: b"s3cret")
    with pytest.raises(Unauthorized, match="no webhook key"):
        SignedToolWebhook(use_cases, None).verify({}, b"")
    body = json.dumps(
        {
            "conversation_id": "c",
            "turn_id": "t",
            "tool_call_id": "tc_1",
            "epoch": 1,
            "name": "orders",
            "input": {},
            "principal": {"org_id": "org_a", "sub": "u1"},
        }
    ).encode()
    hook.verify(sign(b"s3cret", body), body)
    for headers in (sign(b"wrong", body), sign(b"s3cret", body, stamp=int(time.time()) - 3600), {}):
        with pytest.raises(Unauthorized):
            hook.verify(headers, body)
    answer = hook.handle(json.loads(body))
    assert answer["result"]["row_count"] == 1 and answer["artifact"]["kind"] == "data_table"
    assert use_cases.audit.receipts_for("org_a")[0]["channel"] == "webhook"
