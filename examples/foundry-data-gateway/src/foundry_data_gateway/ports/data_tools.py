"""Ports for the data-tool feature. The application depends on these, never on a driver.

* `CatalogueLoader` -- where tool packs and the policy come from (files today; an S3
  object or an admin API later, without the use cases noticing).
* `DataSource` / `SourceRegistry` -- one executor per declared source; a new dialect is a
  new adapter registered by kind, not a change to the use case (open/closed).
* `ReceiptStore` -- one immutable receipt per call id.
* `UsageStore` -- the read model the `data_tool.called` subscriber maintains.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from foundry_data_gateway.domain import Source, Tool, ToolPolicy


class CatalogueLoader(Protocol):
    def load(self) -> ToolPolicy:
        """Every pack plus the policy over them, validated; raises InvalidToolPack."""
        ...


class DataSource(Protocol):
    def read(
        self, tool: Tool, params: Mapping[str, object], *, tenant: str, limit: int
    ) -> Sequence[Mapping[str, object]]:
        """At most `limit` rows of `tool`'s statement, with the scope param bound to `tenant`."""
        ...


class SourceRegistry(Protocol):
    def for_source(self, source: Source) -> DataSource: ...


@dataclass(frozen=True, slots=True)
class DataReceipt:
    call_id: str
    channel: str  # "webhook" (foundry-agent) | "mcp" | "rest"
    org_id: str
    subject: str
    pack: str
    tool: str
    args_sha256: str
    decision: str  # "allow" | "refuse"
    rule: str
    rows: int
    columns: tuple[str, ...]
    policy_version: str
    conversation_id: str | None = None

    def as_payload(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "channel": self.channel,
            "subject": self.subject,
            "pack": self.pack,
            "tool": self.tool,
            "decision": self.decision,
            "rule": self.rule,
            "rows": self.rows,
            "policy_version": self.policy_version,
        }


class ReceiptStore(Protocol):
    def record(self, receipt: DataReceipt) -> bool:
        """Append once per `call_id`; False when that id was already recorded."""
        ...

    def list_for_org(self, org_id: str, limit: int = 50) -> Sequence[Mapping[str, object]]: ...


class UsageStore(Protocol):
    def add(self, org_id: str, tool: str, *, allowed: bool, rows: int) -> None: ...

    def for_org(self, org_id: str) -> Sequence[Mapping[str, object]]: ...
