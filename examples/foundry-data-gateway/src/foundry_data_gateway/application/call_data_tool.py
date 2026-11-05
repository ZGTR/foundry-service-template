"""Call one data tool for a verified caller: the governed read, start to finish.

decide (policy) -> bind (arguments) -> read (source, tenant-scoped, row-capped) ->
shape (declared columns only) -> receipt (once per call id) -> publish `data_tool.called`.

Every step is a collaborator injected through a port, so this class only sequences
them. A refusal at any step reads nothing further, still writes its receipt and still
publishes its event: an audit that only sees successes is not an audit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from foundry_contracts.clock import Clock

from foundry_data_gateway.domain import Tool, ToolRefused, bind_arguments, data_tool_called
from foundry_data_gateway.ports import (
    CatalogueLoader,
    DataReceipt,
    Publisher,
    ReceiptStore,
    SourceRegistry,
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    org_id: str
    subject: str
    tool_name: str
    arguments: object
    call_id: str
    channel: str
    conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool: Tool
    rows: list[dict[str, object]]


@dataclass
class CallDataTool:
    catalogue: CatalogueLoader
    sources: SourceRegistry
    receipts: ReceiptStore
    publisher: Publisher
    clock: Clock

    def execute(self, call: ToolCall) -> ToolResult:
        policy = self.catalogue.load()
        digest = hashlib.sha256(
            json.dumps(call.arguments, sort_keys=True, default=str).encode()
        ).hexdigest()

        def settle(decision: str, rule: str, tool: Tool | None, rows: int = 0) -> None:
            receipt = DataReceipt(
                call.call_id,
                call.channel,
                call.org_id,
                call.subject,
                tool.pack if tool else "",
                call.tool_name,
                digest,
                decision,
                rule,
                rows,
                tool.columns if tool and decision == "allow" else (),
                policy.version,
                call.conversation_id,
            )
            if self.receipts.record(receipt):
                self.publisher.publish(
                    data_tool_called(call.org_id, receipt.as_payload(), self.clock)
                )

        tool: Tool | None = None
        try:
            tool, grant = policy.decide(call.org_id, call.tool_name)
            params = bind_arguments(tool, call.arguments)
            source = self.sources.for_source(tool.source)
            rows = [
                tool.conform(r)
                for r in source.read(tool, params, tenant=call.org_id, limit=grant.max_rows + 1)
            ]
            _shape(tool, rows, grant.max_rows)
        except ToolRefused as refused:
            settle("refuse", refused.rule, tool)
            raise
        settle("allow", "granted", tool, len(rows))
        return ToolResult(tool, rows)


def _shape(tool: Tool, rows: list[dict[str, object]], max_rows: int) -> None:
    if len(rows) > max_rows:
        raise ToolRefused("row_cap", f"more than {max_rows} rows; ask a narrower question")
    allowed = set(tool.columns)
    for row in rows:
        extra = set(row) - allowed
        if extra:
            raise ToolRefused(
                "undeclared_column", f"the source returned undeclared columns {sorted(extra)}"
            )


def result_payload(result: ToolResult) -> Mapping[str, object]:
    return {
        "ok": True,
        "tool": result.tool.name,
        "row_count": len(result.rows),
        "rows": result.rows,
    }
