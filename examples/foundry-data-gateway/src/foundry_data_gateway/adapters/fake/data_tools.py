"""In-memory data-tool adapters: fixed rows, receipts and usage kept in lists/dicts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_data_gateway.domain import Source, Tool, ToolPolicy
from foundry_data_gateway.ports import DataReceipt


class StaticCatalogueLoader:
    def __init__(self, policy: ToolPolicy) -> None:
        self._policy = policy

    def load(self) -> ToolPolicy:
        return self._policy


class FixedRowsDataSource:
    """Returns the same rows for every read and records what it was asked."""

    def __init__(self, rows: Sequence[Mapping[str, object]] = ()) -> None:
        self.rows = list(rows)
        self.reads: list[dict[str, object]] = []

    def read(
        self, tool: Tool, params: Mapping[str, object], *, tenant: str, limit: int
    ) -> Sequence[Mapping[str, object]]:
        self.reads.append(
            {"tool": tool.name, "params": dict(params), "tenant": tenant, "limit": limit}
        )
        return self.rows[:limit]


class SingleSourceRegistry:
    def __init__(self, source: FixedRowsDataSource) -> None:
        self.source = source

    def for_source(self, source: Source) -> FixedRowsDataSource:
        return self.source


class InMemoryReceiptStore:
    def __init__(self) -> None:
        self.receipts: list[DataReceipt] = []

    def record(self, receipt: DataReceipt) -> bool:
        if any(r.call_id == receipt.call_id for r in self.receipts):
            return False
        self.receipts.append(receipt)
        return True

    def list_for_org(self, org_id: str, limit: int = 50) -> Sequence[Mapping[str, object]]:
        return [r.as_payload() for r in self.receipts if r.org_id == org_id][:limit]


class InMemoryUsageStore:
    def __init__(self) -> None:
        self.counts: dict[tuple[str, str], dict[str, int]] = {}

    def add(self, org_id: str, tool: str, *, allowed: bool, rows: int) -> None:
        row = self.counts.setdefault(
            (org_id, tool), {"calls": 0, "refusals": 0, "rows_returned": 0}
        )
        row["calls"] += 1
        row["refusals"] += 0 if allowed else 1
        row["rows_returned"] += rows

    def for_org(self, org_id: str) -> Sequence[Mapping[str, object]]:
        return [{"tool": t, **c} for (o, t), c in self.counts.items() if o == org_id]
