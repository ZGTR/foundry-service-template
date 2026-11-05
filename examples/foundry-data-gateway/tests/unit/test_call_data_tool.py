"""The governed read with in-memory adapters: receipts, events, the usage projection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from foundry_contracts.clock import FrozenClock

from foundry_data_gateway.adapters.fake import (
    FixedRowsDataSource,
    InMemoryReceiptStore,
    InMemoryUsageStore,
    SingleSourceRegistry,
    StaticCatalogueLoader,
)
from foundry_data_gateway.adapters.real.in_process_event_bus import InProcessEventBus
from foundry_data_gateway.application import (
    AgentDefinition,
    CallDataTool,
    ToolCall,
    UsageProjection,
)
from foundry_data_gateway.domain import DATA_TOOL_CALLED, ToolRefused
from tests.unit.test_tool_policy_and_arguments import GRANTED, policy

ROW = {"id": "o1", "total": 12}


def wire(rows=(ROW,)):
    bus, usage, receipts = InProcessEventBus(), InMemoryUsageStore(), InMemoryReceiptStore()
    events: list = []
    bus.subscribe(DATA_TOOL_CALLED, events.append)
    UsageProjection(usage).subscribe_to(bus)
    source = FixedRowsDataSource(rows)
    use_case = CallDataTool(
        StaticCatalogueLoader(policy(GRANTED)),
        SingleSourceRegistry(source),
        receipts,
        bus,
        FrozenClock(datetime(2026, 11, 5, tzinfo=UTC)),
    )
    return use_case, source, receipts, usage, events


def call(tool="orders", args=None, tenant="org_a", call_id="c1") -> ToolCall:
    return ToolCall(tenant, "u1", tool, {} if args is None else args, call_id, "rest")


def test_an_allowed_read_binds_the_tenant_receipts_publishes_and_projects() -> None:
    use_case, source, receipts, usage, events = wire()
    result = use_case.execute(call())
    assert result.rows == [{"id": "o1", "total": 12.0}]  # conformed to the declared number type
    assert source.reads == [
        {"tool": "orders", "params": {"days": 7}, "tenant": "org_a", "limit": 6}
    ]
    assert [(r.decision, r.rule, r.rows) for r in receipts.receipts] == [("allow", "granted", 1)]
    assert receipts.receipts[0].policy_version.startswith("sha256:")
    assert [e.payload["decision"] for e in events] == ["allow"]
    assert usage.for_org("org_a") == [
        {"tool": "orders", "calls": 1, "refusals": 0, "rows_returned": 1}
    ]


@pytest.mark.parametrize(
    "tenant, args, rule",
    [("org_b", {}, "tool_not_granted"), ("org_a", {"days": 99}, "invalid_arguments")],
)
def test_a_refusal_reads_nothing_but_is_receipted_and_published(
    tenant: str, args: dict, rule: str
) -> None:
    use_case, source, receipts, usage, events = wire()
    with pytest.raises(ToolRefused) as refused:
        use_case.execute(call(args=args, tenant=tenant))
    assert refused.value.rule == rule and source.reads == []
    assert [(r.decision, r.rule) for r in receipts.receipts] == [("refuse", rule)]
    assert usage.for_org(tenant)[0]["refusals"] == 1 and len(events) == 1


def test_more_rows_than_the_cap_is_refused_not_truncated() -> None:
    use_case, *_ = wire(rows=[ROW] * 6)
    with pytest.raises(ToolRefused, match="more than 5"):
        use_case.execute(call())


def test_an_undeclared_column_never_leaves() -> None:
    use_case, *_ = wire(rows=[{**ROW, "email": "x@y"}])
    with pytest.raises(ToolRefused) as refused:
        use_case.execute(call())
    assert refused.value.rule == "undeclared_column"


def test_a_resent_call_id_is_receipted_and_published_once() -> None:
    use_case, _, receipts, usage, events = wire()
    use_case.execute(call(call_id="same"))
    use_case.execute(call(call_id="same"))
    assert len(receipts.receipts) == 1 and len(events) == 1
    assert usage.for_org("org_a")[0]["calls"] == 1


def test_the_agent_definition_offers_exactly_the_granted_tools() -> None:
    agent = AgentDefinition(StaticCatalogueLoader(policy(GRANTED)), "m1")
    bundle, digest = agent.execute("org_a")
    assert [t["name"] for t in bundle["tools"]] == bundle["agent"]["tool_catalog"] == ["orders"]
    assert "Example questions" in bundle["tools"][0]["description"] and len(digest) == 64
    assert agent.execute("org_b")[0]["tools"] == []
