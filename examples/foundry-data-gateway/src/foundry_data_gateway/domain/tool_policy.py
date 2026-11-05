"""Who may call which tools: the policy file, `foundry.dev/tool-policy/v1`.

A tenant is the verified caller's `org_id` (a Shepherd organisation, an Alphazed family
or school). It is granted toolsets of exactly one pack and a row cap. A tenant absent
from the file may call nothing: default deny. The policy carries a version (its content
digest) and every receipt records it, so "which rule allowed this read" stays answerable
after the file changes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from foundry_data_gateway.domain.errors import InvalidToolPack, ToolRefused
from foundry_data_gateway.domain.tool_pack import Tool, ToolPack

API_VERSION = "foundry.dev/tool-policy/v1"


@dataclass(frozen=True, slots=True)
class Grant:
    tenant: str
    pack: str
    toolsets: tuple[str, ...]
    max_rows: int


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    grants: Mapping[str, Grant]
    packs: Mapping[str, ToolPack]
    version: str

    @classmethod
    def parse(cls, document: Mapping[str, object], packs: Mapping[str, ToolPack]) -> ToolPolicy:
        if document.get("apiVersion") != API_VERSION:
            raise InvalidToolPack(f"policy apiVersion must be {API_VERSION}")
        tenants = document.get("tenants") or {}
        assert isinstance(tenants, Mapping)
        grants = {}
        for tenant, raw in tenants.items():
            pack = packs.get(raw.get("pack"))
            if pack is None:
                raise InvalidToolPack(f"tenant {tenant}: unknown pack {raw.get('pack')!r}")
            sets = tuple(raw.get("toolsets") or ())
            if set(sets) - set(pack.toolsets):
                raise InvalidToolPack(
                    f"tenant {tenant}: unknown toolsets {sorted(set(sets) - set(pack.toolsets))}"
                )
            grants[str(tenant)] = Grant(str(tenant), pack.name, sets, int(raw.get("max_rows", 50)))
        digest = hashlib.sha256(
            json.dumps(document, sort_keys=True, default=str).encode()
        ).hexdigest()
        return cls(grants, packs, "sha256:" + digest[:16])

    def granted_tools(self, tenant: str) -> list[Tool]:
        grant = self.grants.get(tenant)
        if grant is None:
            return []
        pack = self.packs[grant.pack]
        names = dict.fromkeys(t for s in grant.toolsets for t in pack.toolsets[s])
        return [pack.tools[n] for n in names]

    def decide(self, tenant: str, tool_name: str) -> tuple[Tool, Grant]:
        grant = self.grants.get(tenant)
        if grant is None:
            raise ToolRefused("unknown_tenant", "this organisation has no data policy")
        for tool in self.granted_tools(tenant):
            if tool.name == tool_name:
                return tool, grant
        pack_tools = self.packs[grant.pack].tools
        rule = "tool_not_granted" if tool_name in pack_tools else "unknown_tool"
        raise ToolRefused(rule, f"{tool_name} is not available to this organisation")
