"""Receipts and the usage read model, on the service's own database."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict

from sqlalchemy import Engine, text

from foundry_data_gateway.ports import DataReceipt


class SqlReceiptStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, receipt: DataReceipt) -> bool:
        with self._engine.begin() as conn:
            inserted = conn.execute(
                text(
                    """INSERT INTO data_tool_receipts
                       (call_id, channel, org_id, subject, pack, tool, args_sha256, decision,
                        rule, row_count, columns, policy_version, conversation_id)
                       VALUES (:call_id, :channel, :org_id, :subject, :pack, :tool, :args_sha256,
                               :decision, :rule, :rows, :columns, :policy_version,
                               :conversation_id)
                       ON CONFLICT (call_id) DO NOTHING"""
                ),
                {**asdict(receipt), "columns": ",".join(receipt.columns)},
            )
            return inserted.rowcount == 1

    def list_for_org(self, org_id: str, limit: int = 50) -> Sequence[Mapping[str, object]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    """SELECT call_id, channel, subject, pack, tool, decision, rule, row_count,
                              columns, policy_version, conversation_id, created_at
                       FROM data_tool_receipts WHERE org_id = :org
                       ORDER BY created_at DESC LIMIT :limit"""
                ),
                {"org": org_id, "limit": limit},
            ).mappings()
            return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]


class SqlUsageStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, org_id: str, tool: str, *, allowed: bool, rows: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO data_tool_usage (org_id, tool, calls, refusals, rows_returned)
                       VALUES (:org, :tool, 1, :refused, :rows)
                       ON CONFLICT (org_id, tool) DO UPDATE SET
                         calls = data_tool_usage.calls + 1,
                         refusals = data_tool_usage.refusals + EXCLUDED.refusals,
                         rows_returned = data_tool_usage.rows_returned + EXCLUDED.rows_returned,
                         updated_at = now()"""
                ),
                {"org": org_id, "tool": tool, "refused": 0 if allowed else 1, "rows": rows},
            )

    def for_org(self, org_id: str) -> Sequence[Mapping[str, object]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    """SELECT tool, calls, refusals, rows_returned FROM data_tool_usage
                       WHERE org_id = :org ORDER BY calls DESC, tool"""
                ),
                {"org": org_id},
            ).mappings()
            return [dict(r) for r in rows]
