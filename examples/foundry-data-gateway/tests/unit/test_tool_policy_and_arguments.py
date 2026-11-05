"""Tenant grants (default deny) and argument binding (hostile model input)."""

from __future__ import annotations

import pytest

from foundry_data_gateway.domain import (
    InvalidToolPack,
    ToolPolicy,
    ToolRefused,
    bind_arguments,
    parse_pack,
)
from tests.unit.test_tool_pack import BASE


def policy(tenants: dict) -> ToolPolicy:
    pack = parse_pack(BASE)
    return ToolPolicy.parse(
        {"apiVersion": "foundry.dev/tool-policy/v1", "tenants": tenants}, {pack.name: pack}
    )


GRANTED = {
    "org_a": {"pack": "demo", "toolsets": ["all"], "max_rows": 5},
    "org_b": {"pack": "demo", "toolsets": []},
}


def test_default_deny_and_named_rules() -> None:
    p = policy(GRANTED)
    assert [t.name for t in p.granted_tools("org_a")] == ["orders"]
    assert p.granted_tools("org_z") == []
    for tenant, tool, rule in (
        ("org_z", "orders", "unknown_tenant"),
        ("org_b", "orders", "tool_not_granted"),
        ("org_a", "ghost", "unknown_tool"),
    ):
        with pytest.raises(ToolRefused) as refused:
            p.decide(tenant, tool)
        assert refused.value.rule == rule
    tool, grant = p.decide("org_a", "orders")
    assert grant.max_rows == 5


def test_a_policy_naming_an_unknown_pack_or_toolset_does_not_load() -> None:
    with pytest.raises(InvalidToolPack, match="unknown pack"):
        policy({"org_a": {"pack": "nope"}})
    with pytest.raises(InvalidToolPack, match="unknown toolsets"):
        policy({"org_a": {"pack": "demo", "toolsets": ["admin"]}})


def test_the_policy_version_follows_its_content() -> None:
    assert policy(GRANTED).version != policy({"org_a": GRANTED["org_a"]}).version
    assert policy(GRANTED).version == policy(dict(GRANTED)).version


@pytest.mark.parametrize(
    "args, message",
    [
        ([], "one JSON object"),
        ({"days": "7"}, "must be integer"),
        ({"days": True}, "must be integer"),
        ({"days": 0}, "below"),
        ({"days": 31}, "above"),
        ({"org_id": "org_b"}, "unexpected"),  # the tenant can never be chosen by the model
        ({"days": 3, "sql": "DROP"}, "unexpected"),
    ],
)
def test_arguments_outside_the_declaration_are_refused(args: object, message: str) -> None:
    tool = parse_pack(BASE).tools["orders"]
    with pytest.raises(ToolRefused, match=message) as refused:
        bind_arguments(tool, args)
    assert refused.value.rule == "invalid_arguments"


def test_defaults_fill_omitted_optional_arguments() -> None:
    assert bind_arguments(parse_pack(BASE).tools["orders"], {}) == {"days": 7}
