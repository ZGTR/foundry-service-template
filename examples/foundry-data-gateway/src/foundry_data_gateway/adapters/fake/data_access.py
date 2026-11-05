"""In-memory `DataSource` and `ReceiptStore`: fixed rows, receipts kept in a list."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_data_gateway.ports.data_access import DataReceipt


class FixedRowsDataSource:
    """Returns the same rows for every read and counts reads, so a test can assert none."""

    def __init__(self, rows: Sequence[Mapping[str, object]] = ()) -> None:
        self.rows = list(rows)
        self.reads = 0

    def read(
        self, tool_name: str, args: Mapping[str, object], *, org_id: str, limit: int
    ) -> Sequence[Mapping[str, object]]:
        self.reads += 1
        return self.rows


class InMemoryReceiptStore:
    def __init__(self) -> None:
        self.receipts: list[DataReceipt] = []

    def record(self, receipt: DataReceipt) -> bool:
        if any(r.call_id == receipt.call_id for r in self.receipts):
            return False
        self.receipts.append(receipt)
        return True

    def list_for_org(self, org_id: str, limit: int = 50) -> Sequence[Mapping[str, object]]:
        mine = [r for r in self.receipts if r.org_id == org_id]
        return [
            {"call_id": r.call_id, "tool": r.tool, "decision": r.decision} for r in mine[:limit]
        ]
