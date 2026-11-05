"""Ports for the data-tool feature: where rows come from, and where receipts go."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


class DataSource(Protocol):
    def read(
        self, tool_name: str, args: Mapping[str, object], *, org_id: str, limit: int
    ) -> Sequence[Mapping[str, object]]:
        """Rows for one curated tool, read under `org_id`'s row scope, at most `limit` + 1."""
        ...


@dataclass(frozen=True, slots=True)
class DataReceipt:
    call_id: str
    channel: str  # "webhook" (foundry-agent) or "mcp"
    org_id: str
    subject: str
    tool: str
    args_sha256: str
    decision: str  # "allow" or "refuse"
    rule: str
    rows: int
    columns: tuple[str, ...]
    conversation_id: str | None = None


class ReceiptStore(Protocol):
    def record(self, receipt: DataReceipt) -> bool:
        """Append once per `call_id`; False when that id was already recorded."""
        ...

    def list_for_org(self, org_id: str, limit: int = 50) -> Sequence[Mapping[str, object]]: ...
