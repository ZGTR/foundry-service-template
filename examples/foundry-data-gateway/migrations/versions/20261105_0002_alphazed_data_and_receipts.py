"""Alphazed-shaped learning data behind row-level security, and the data-tool receipts.

The `az_*` tables mirror the shape of Alphazed's content and learner data (subjects,
content bytes, learners, progress), keyed by school (`org_id`). They hold SYNTHETIC rows
only (`scripts/seed_alphazed.py`); no production data is copied.

`alphazed_reader` is the only role the data tools read as: SELECT on the `az_*` tables,
nothing else, and no BYPASSRLS. Learners and progress carry an RLS policy on
`current_setting('app.org_id')`, which the gateway sets per transaction. `parent_email`
exists to prove a column the role can never select: the reader has a column grant list
on `az_learners` that omits it.

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
        CREATE TABLE az_subjects (
            id serial PRIMARY KEY, slug text UNIQUE NOT NULL, title_en text NOT NULL,
            title_ar text NOT NULL, age_group text NOT NULL);
        CREATE TABLE az_content_bytes (
            id serial PRIMARY KEY, subject_id int NOT NULL REFERENCES az_subjects(id),
            title text NOT NULL, bit_count int NOT NULL);
        CREATE TABLE az_learners (
            id serial PRIMARY KEY, org_id text NOT NULL, display_name text NOT NULL,
            age_group text NOT NULL, parent_email text NOT NULL);
        CREATE TABLE az_progress (
            learner_id int NOT NULL REFERENCES az_learners(id),
            byte_id int NOT NULL REFERENCES az_content_bytes(id),
            org_id text NOT NULL, score int NOT NULL, completed boolean NOT NULL,
            PRIMARY KEY (learner_id, byte_id));

        CREATE TABLE data_tool_receipts (
            call_id text PRIMARY KEY, channel text NOT NULL, org_id text NOT NULL,
            subject text NOT NULL, tool text NOT NULL, args_sha256 text NOT NULL,
            decision text NOT NULL, rule text NOT NULL, row_count int NOT NULL,
            columns text NOT NULL, conversation_id text,
            created_at timestamptz NOT NULL DEFAULT now());
        CREATE INDEX data_tool_receipts_org ON data_tool_receipts (org_id, created_at);

        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'alphazed_reader') THEN
                CREATE ROLE alphazed_reader NOLOGIN NOBYPASSRLS;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO alphazed_reader;
        GRANT SELECT ON az_subjects, az_content_bytes, az_progress TO alphazed_reader;
        GRANT SELECT (id, org_id, display_name, age_group) ON az_learners TO alphazed_reader;

        ALTER TABLE az_learners ENABLE ROW LEVEL SECURITY;
        ALTER TABLE az_progress ENABLE ROW LEVEL SECURITY;
        CREATE POLICY az_learners_by_org ON az_learners
            USING (org_id = current_setting('app.org_id', true));
        CREATE POLICY az_progress_by_org ON az_progress
            USING (org_id = current_setting('app.org_id', true));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE data_tool_receipts;
        DROP TABLE az_progress; DROP TABLE az_learners;
        DROP TABLE az_content_bytes; DROP TABLE az_subjects;
        REVOKE USAGE ON SCHEMA public FROM alphazed_reader;
        """
    )
