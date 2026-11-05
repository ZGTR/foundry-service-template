"""Load-time rules of `foundry.dev/tool-pack/v1`: a pack that could leak does not start."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from foundry_data_gateway.domain import InvalidToolPack, merge_packs, parse_pack

PACKS = Path(__file__).resolve().parents[2] / "packs"

BASE = {
    "apiVersion": "foundry.dev/tool-pack/v1",
    "pack": "demo",
    "sources": {
        "db": {
            "kind": "mysql",
            "connection": "demo",
            "scope": {"strategy": "bound_param", "param": "org_id"},
        }
    },
    "tools": {
        "orders": {
            "source": "db",
            "description": "Orders since a date.",
            "parameters": {"days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7}},
            "statement": "SELECT id, total FROM orders WHERE org_id = :org_id AND age < :days",
            "returns": {"id": "string", "total": "number"},
            "examples": [{"question": "Orders this week?", "arguments": {"days": 7}}],
        }
    },
}


def mutate(path: tuple[str, ...], value: object) -> dict:
    doc = copy.deepcopy(BASE)
    node = doc
    for key in path[:-1]:
        node = node[key]
    if value is None:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return doc


@pytest.mark.parametrize("pack_file", sorted(PACKS.glob("*.tools.yaml")), ids=lambda p: p.name)
def test_the_shipped_packs_load(pack_file: Path) -> None:
    pack = parse_pack(yaml.safe_load(pack_file.read_text()))
    assert pack.tools and all(t.examples for t in pack.tools.values())


def test_a_valid_pack_maps_onto_mcp_fields() -> None:
    tool = parse_pack(BASE).tools["orders"]
    assert tool.input_schema() == {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7}},
    }
    row = tool.output_schema()["properties"]["rows"]["items"]
    assert (
        row["properties"]["total"] == {"type": ["number", "null"]}
        and row["additionalProperties"] is False
    )
    assert tool.annotations["readOnlyHint"] is True


@pytest.mark.parametrize(
    "path, value, message",
    [
        (
            ("tools", "orders", "statement"),
            "SELECT id, total FROM orders WHERE age < :days",
            "cross tenants",
        ),
        (("tools", "orders", "parameters", "org_id"), {"type": "string"}, "bound from the caller"),
        (
            ("tools", "orders", "statement"),
            "SELECT id, total FROM orders WHERE org_id = :org_id AND x = :other",
            "undeclared",
        ),
        (
            ("tools", "orders", "statement"),
            "SELECT id, total FROM orders WHERE org_id = :org_id",
            "never used",
        ),
        (
            ("tools", "orders", "statement"),
            "DELETE FROM orders WHERE org_id = :org_id AND age < :days",
            "read-only",
        ),
        (
            ("tools", "orders", "statement"),
            "SELECT 1 WHERE org_id = :org_id AND age < :days; DROP TABLE x",
            "read-only",
        ),
        (("tools", "orders", "returns"), None, "returns"),
        (("tools", "orders", "returns"), {"id": "uuid"}, "return types"),
        (("tools", "orders", "annotations"), {"readOnlyHint": False}, "read-only tools only"),
        (("tools", "orders", "source"), "missing", "unknown source"),
        (("tools", "orders", "description"), None, "description"),
        (("sources", "db", "scope"), {"strategy": "rls", "param": "org_id"}, "postgres"),
        (("sources", "db", "kind"), "oracle", "kind"),
        (("apiVersion",), "v0", "apiVersion"),
        (("toolsets",), {"all": ["orders", "ghost"]}, "unknown tools"),
    ],
)
def test_each_load_time_rule_refuses(path: tuple[str, ...], value: object, message: str) -> None:
    with pytest.raises(InvalidToolPack, match=message):
        parse_pack(mutate(path, value))


def test_two_packs_may_not_define_the_same_tool() -> None:
    other = {**copy.deepcopy(BASE), "pack": "other"}
    with pytest.raises(InvalidToolPack, match="defined by both"):
        merge_packs([parse_pack(BASE), parse_pack(other)])


def test_conform_returns_declared_json_types() -> None:
    tool = parse_pack(
        mutate(("tools", "orders", "returns"), {"id": "integer", "total": "number"})
    ).tools["orders"]
    assert tool.conform({"id": 3.0, "total": 2, "x": None}) == {"id": 3, "total": 2.0, "x": None}
