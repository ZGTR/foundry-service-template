"""The two SQL source classes against REAL servers: tenant binding, row cap, read-only, RLS.

Needs `FDG_TEST_POSTGRES_URL` (a superuser URL to a throwaway database) and/or
`FDG_TEST_MYSQL_URL` (root URL to a server, no database); each arm skips BY NAME without
its URL, and a skip is not a pass. `e2e/run.sh` exercises the same classes end to end.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from foundry_data_gateway.adapters.real import SqlSourceRegistry
from foundry_data_gateway.domain import parse_pack

PG_URL = os.environ.get("FDG_TEST_POSTGRES_URL")
MYSQL_URL = os.environ.get("FDG_TEST_MYSQL_URL")


def pack(kind: str, strategy: str, statement: str, role: str | None = None):
    source = {"kind": kind, "connection": "db", "scope": {"strategy": strategy, "param": "org_id"}}
    if role:
        source["reader_role"] = role
    return parse_pack(
        {
            "apiVersion": "foundry.dev/tool-pack/v1",
            "pack": "it",
            "sources": {"db": source},
            "tools": {
                "notes": {
                    "source": "db",
                    "description": "Notes.",
                    "statement": statement,
                    "returns": {"org_id": "string", "body": "string"},
                }
            },
        }
    ).tools["notes"]


@pytest.mark.skipif(not PG_URL, reason="FDG_TEST_POSTGRES_URL unset: Postgres RLS arm not run")
def test_postgres_rls_filters_rows_even_when_the_statement_forgets_to() -> None:
    role = f"fdg_reader_{uuid.uuid4().hex[:8]}"
    admin = create_engine(PG_URL)
    with admin.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS it_notes"))
        conn.execute(text("CREATE TABLE it_notes (org_id text, body text)"))
        conn.execute(text("INSERT INTO it_notes VALUES ('a','a1'),('a','a2'),('b','b1')"))
        conn.execute(text(f"CREATE ROLE {role} NOLOGIN NOBYPASSRLS"))
        conn.execute(text(f"GRANT SELECT ON it_notes TO {role}"))
        conn.execute(text("ALTER TABLE it_notes ENABLE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                "CREATE POLICY it_by_org ON it_notes "
                "USING (org_id = current_setting('app.org_id', true))"
            )
        )
    try:
        # References :org_id (the loader requires it) but does not filter by it: a statement bug.
        careless = pack(
            "postgres",
            "rls",
            "SELECT org_id, body FROM it_notes WHERE CAST(:org_id AS text) IS NOT NULL",
            role,
        )
        source = SqlSourceRegistry({"db": PG_URL}).for_source(careless.source)
        assert sorted(r["body"] for r in source.read(careless, {}, tenant="a", limit=10)) == [
            "a1",
            "a2",
        ]
        assert [r["body"] for r in source.read(careless, {}, tenant="b", limit=10)] == ["b1"]
        assert (
            len(source.read(careless, {}, tenant="a", limit=1)) == 1
        )  # the row cap wraps the statement
        # Negative control: the same statement without RLS returns every tenant's rows.
        plain = pack("postgres", "bound_param", careless.statement)
        leaked = (
            SqlSourceRegistry({"db": PG_URL})
            .for_source(plain.source)
            .read(plain, {}, tenant="a", limit=10)
        )
        assert {r["org_id"] for r in leaked} == {"a", "b"}
    finally:
        with admin.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS it_notes"))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))


@pytest.mark.skipif(not MYSQL_URL, reason="FDG_TEST_MYSQL_URL unset: MySQL arm not run")
def test_mysql_binds_the_tenant_and_runs_read_only() -> None:
    db = f"fdg_it_{uuid.uuid4().hex[:8]}"
    admin = create_engine(MYSQL_URL.rstrip("/") + "/mysql")
    with admin.begin() as conn:
        conn.execute(text(f"CREATE DATABASE {db}"))
    url = f"{MYSQL_URL.rstrip('/')}/{db}"
    try:
        with create_engine(url).begin() as conn:
            conn.execute(text("CREATE TABLE notes (org_id VARCHAR(8), body VARCHAR(8))"))
            conn.execute(text("INSERT INTO notes VALUES ('a','a1'),('b','b1')"))
        tool = pack("mysql", "bound_param", "SELECT org_id, body FROM notes WHERE org_id = :org_id")
        source = SqlSourceRegistry({"db": url}).for_source(tool.source)
        assert source.read(tool, {}, tenant="a", limit=10) == [{"org_id": "a", "body": "a1"}]
        # The session is READ ONLY: even a statement that slipped past the loader cannot write.
        writer = pack(
            "mysql", "bound_param", "SELECT org_id, body FROM notes WHERE org_id = :org_id"
        )
        object.__setattr__(
            writer,
            "statement",
            "SELECT 1 AS org_id, 2 AS body FROM notes WHERE org_id = :org_id FOR UPDATE",
        )
        with pytest.raises(DBAPIError):
            source.read(writer, {}, tenant="a", limit=10)
    finally:
        with admin.begin() as conn:
            conn.execute(text(f"DROP DATABASE {db}"))
