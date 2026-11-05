"""PostgreSQL adapters for the data-tool feature.

`SqlDataSource` runs each curated tool inside its own transaction that first drops to the
`alphazed_reader` role (SELECT only, no BYPASSRLS) and sets `app.org_id`. Row-level
security on `az_learners` and `az_progress` then filters every row by that org, so the
SQL below never carries an org predicate and a bug in it cannot cross tenants. The
connection's own login never reads data directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import Engine, text

from foundry_data_gateway.ports.data_access import DataReceipt

READER_ROLE = "alphazed_reader"

_SQL: dict[str, str] = {
    "list_subjects": """
        SELECT s.slug, s.title_en, s.title_ar, s.age_group, count(b.id) AS bytes
        FROM az_subjects s LEFT JOIN az_content_bytes b ON b.subject_id = s.id
        WHERE CAST(:age_group AS text) IS NULL OR s.age_group = :age_group
        GROUP BY s.id ORDER BY s.age_group, s.slug LIMIT :limit""",
    "subject_progress": """
        SELECT l.display_name AS learner, l.age_group,
               count(p.byte_id) FILTER (WHERE p.completed) AS bytes_completed,
               round(avg(p.score)::numeric, 1)::float AS avg_score
        FROM az_learners l
        JOIN az_progress p ON p.learner_id = l.id
        JOIN az_content_bytes b ON b.id = p.byte_id
        JOIN az_subjects s ON s.id = b.subject_id
        WHERE s.slug = :subject_slug
        GROUP BY l.id ORDER BY l.display_name LIMIT :limit""",
    "struggling_learners": """
        SELECT l.display_name AS learner, s.title_en AS subject,
               round(avg(p.score)::numeric, 1)::float AS avg_score, count(*) AS attempts
        FROM az_learners l
        JOIN az_progress p ON p.learner_id = l.id
        JOIN az_content_bytes b ON b.id = p.byte_id
        JOIN az_subjects s ON s.id = b.subject_id
        GROUP BY l.id, s.id HAVING avg(p.score) < :below_score
        ORDER BY avg(p.score) ASC LIMIT :limit""",
}


class SqlDataSource:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read(
        self, tool_name: str, args: Mapping[str, object], *, org_id: str, limit: int
    ) -> Sequence[Mapping[str, object]]:
        params = {"age_group": None, **args, "limit": limit + 1}
        with self._engine.begin() as conn:
            conn.execute(text(f"SET LOCAL ROLE {READER_ROLE}"))
            conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id})
            return [dict(row) for row in conn.execute(text(_SQL[tool_name]), params).mappings()]


class SqlReceiptStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, receipt: DataReceipt) -> bool:
        with self._engine.begin() as conn:
            inserted = conn.execute(
                text(
                    """INSERT INTO data_tool_receipts
                       (call_id, channel, org_id, subject, tool, args_sha256, decision, rule,
                        row_count, columns, conversation_id)
                       VALUES (:call_id, :channel, :org_id, :subject, :tool, :args_sha256,
                               :decision, :rule, :rows, :columns, :conversation_id)
                       ON CONFLICT (call_id) DO NOTHING"""
                ),
                {
                    "call_id": receipt.call_id,
                    "channel": receipt.channel,
                    "org_id": receipt.org_id,
                    "subject": receipt.subject,
                    "tool": receipt.tool,
                    "args_sha256": receipt.args_sha256,
                    "decision": receipt.decision,
                    "rule": receipt.rule,
                    "rows": receipt.rows,
                    "columns": ",".join(receipt.columns),
                    "conversation_id": receipt.conversation_id,
                },
            )
            return inserted.rowcount == 1

    def list_for_org(self, org_id: str, limit: int = 50) -> Sequence[Mapping[str, object]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    """SELECT call_id, channel, subject, tool, decision, rule, row_count,
                              columns, conversation_id, created_at
                       FROM data_tool_receipts WHERE org_id = :org
                       ORDER BY created_at DESC LIMIT :limit"""
                ),
                {"org": org_id, "limit": limit},
            ).mappings()
            return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]
