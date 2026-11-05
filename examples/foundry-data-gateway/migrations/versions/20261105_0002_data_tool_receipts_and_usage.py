"""The gateway's own records: one receipt per data-tool call, and the usage read model.

Company data is NOT here. It stays in each business's own database (a `source` in its
tool pack); the gateway stores only what it decided and how many rows it returned, never
the rows themselves.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data_tool_receipts (
            call_id text PRIMARY KEY, channel text NOT NULL, org_id text NOT NULL,
            subject text NOT NULL, pack text NOT NULL, tool text NOT NULL,
            args_sha256 text NOT NULL, decision text NOT NULL, rule text NOT NULL,
            row_count int NOT NULL, columns text NOT NULL, policy_version text NOT NULL,
            conversation_id text, created_at timestamptz NOT NULL DEFAULT now());
        CREATE INDEX data_tool_receipts_org ON data_tool_receipts (org_id, created_at);

        CREATE TABLE data_tool_usage (
            org_id text NOT NULL, tool text NOT NULL, calls bigint NOT NULL,
            refusals bigint NOT NULL, rows_returned bigint NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (org_id, tool));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE data_tool_usage; DROP TABLE data_tool_receipts;")
