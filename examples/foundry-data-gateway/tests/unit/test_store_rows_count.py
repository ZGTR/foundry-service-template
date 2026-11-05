"""`StoreRows.count()` counts through SQLAlchemy Core, bare and schema-qualified.

The count used to be `select count(*) from {table}`. Built from Core instead, a
schema-qualified owned name must still split into schema and table rather than
become one quoted identifier.
"""

from __future__ import annotations

import pytest
from foundry_testkit.store_rows import SqlStoreRows
from sqlalchemy import create_engine, text


class _Rows(SqlStoreRows):
    owned_tables = ("widgets", "main.widgets")


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("create table widgets (id integer primary key)"))
        connection.execute(text("insert into widgets (id) values (1), (2)"))
    yield engine
    engine.dispose()


def test_a_bare_owned_table_is_counted(engine) -> None:
    assert _Rows(engine).count("widgets") == 2


def test_a_schema_qualified_owned_table_is_counted(engine) -> None:
    assert _Rows(engine).count("main.widgets") == 2


def test_an_unowned_table_is_refused_before_any_sql(engine) -> None:
    with pytest.raises(AssertionError, match="not one of this service's tables"):
        _Rows(engine).count("gadgets")
