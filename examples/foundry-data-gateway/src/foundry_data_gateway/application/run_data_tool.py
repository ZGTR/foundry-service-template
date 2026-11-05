"""One governed read: decide, validate, read under row scope, shape, receipt.

Every call writes exactly one receipt per `call_id`, allow or refuse, so "what did an
agent read, for whom, under which rule" is a query, not a log search. A refused call
reads nothing. A result is checked against the tool's declared columns before it
leaves, and a result larger than the org's row cap is refused rather than truncated.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from foundry_data_gateway.domain.data_tools import (
    DataPolicy,
    DataTool,
    ToolRefused,
    check_arguments,
)
from foundry_data_gateway.ports.data_access import DataReceipt, DataSource, ReceiptStore


@dataclass(frozen=True, slots=True)
class DataToolResult:
    tool: str
    rows: list[dict[str, object]]
    columns: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "row_count": len(self.rows),
            "columns": list(self.columns),
            "rows": self.rows,
        }


@dataclass
class RunDataTool:
    policy: DataPolicy
    source: DataSource
    receipts: ReceiptStore

    def list_tools(self, org_id: str) -> list[DataTool]:
        return self.policy.allowed(org_id)

    def execute(
        self,
        *,
        org_id: str,
        subject: str,
        tool_name: str,
        args: object,
        call_id: str,
        channel: str,
        conversation_id: str | None = None,
    ) -> DataToolResult:
        digest = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()

        def receipt(decision: str, rule: str, rows: int = 0, columns: tuple[str, ...] = ()) -> None:
            self.receipts.record(
                DataReceipt(
                    call_id,
                    channel,
                    org_id,
                    subject,
                    tool_name,
                    digest,
                    decision,
                    rule,
                    rows,
                    columns,
                    conversation_id,
                )
            )

        try:
            tool, max_rows = self.policy.decide(org_id, tool_name)
            clean = check_arguments(tool, args)
            rows = [
                dict(r) for r in self.source.read(tool.name, clean, org_id=org_id, limit=max_rows)
            ]
            if len(rows) > max_rows:
                raise ToolRefused("row_cap", f"more than {max_rows} rows; narrow the question")
            for row in rows:
                if set(row) - set(tool.columns):
                    raise ToolRefused(
                        "undeclared_column", "a row carried a column the tool does not declare"
                    )
        except ToolRefused as refused:
            receipt("refuse", refused.rule)
            raise
        receipt("allow", "granted", len(rows), tool.columns)
        return DataToolResult(tool.name, rows, tool.columns)
