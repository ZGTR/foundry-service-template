"""Ask the STORE what it holds, rather than asking the response.

A seeded test that asserts only on the HTTP body proves the handler built a
reply. It does not prove anything was written, and the two are separable in a
way that is easy to miss. A service that answers a replayed request from an
idempotency record can return a stored envelope while the row it describes was
never inserted, and every response-level assertion in the suite still passes.
The monorepo's ``.claude/CLAUDE.md`` names the general shape: a response body
that carries a persisted column reads as a read-back of the row and is not one.

WHY THIS IS IN THE KIT. The seeded tier's whole claim is that ONE suite runs
against TWO stores. Until this module existed there was nothing in the kit that
let a test assert what either store HELD, so the claim was unassertable and each
service was going to invent its own spelling of it -- which is the drift this
template exists to delete. What is service-specific is the QUESTIONS; what is
generic is everything below.

THE QUESTIONS ARE SEMANTIC, NOT COLUMNAR, and that is forced rather than
chosen. A real store may key rows by a digest of the scope and hold content as
``LargeBinary`` where the fake keys by a tuple and holds ``str``. A "give me the
raw columns" inspector therefore has a DIFFERENT SHAPE per profile, and an
assertion written against one of them is a test of the profile rather than of
the service. Define the questions in the service's own vocabulary -- "the
messages on this thread", "the records for this tenant" -- and implement them
twice.

AN UNRECOGNISED TABLE IS A REFUSAL, NEVER A ZERO. ``count("memory_mesages")``
would be ``0`` from a fake and a ``ProgrammingError`` from PostgreSQL, and ``0``
is exactly what a typo looks like on the fake side. :func:`assert_owned` makes
both answer the same way, which is the only way a cross-profile count assertion
means anything.

A service subclasses the two bases and declares its own questions::

    OWNED_TABLES = ("widgets", "widget_events")

    class SqlWidgetRows(SqlStoreRows):
        owned_tables = OWNED_TABLES

        def widgets(self, org_id):
            rows = self.select(
                "select widget_id, label from widgets where org_id = :org",
                {"org": org_id},
            )
            return [{"widget_id": r[0], "label": r[1]} for r in rows]

    class FakeWidgetRows(FakeStoreRows):
        owned_tables = OWNED_TABLES

        def widgets(self, org_id):
            return [...]                    # project the fake's own structures

        def _count(self, table):
            return len(self.inner().widgets)

``tests/support/environment.py`` picks the one that matches the store it wired
and hands it to :class:`~foundry_testkit.seeded_case.SeededEnvironment` as
``rows``; ``tests/conftest.py`` puts it on the test instance as ``self.rows``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = [
    "StoreRows",
    "SqlStoreRows",
    "FakeStoreRows",
    "assert_owned",
]


@runtime_checkable
class StoreRows(Protocol):
    """The one question every store inspector answers, whatever it holds.

    Deliberately NARROW. A wider Protocol would have to name entities, and the
    entities differ per service -- so the shared contract is the part that can
    be shared, and the service's own questions are added by its subclass and
    checked by nothing but its own tests using them.
    """

    def count(self, table: str) -> int: ...


def assert_owned(table: str, owned: tuple[str, ...]) -> None:
    """Refuse a table this service does not own, in BOTH profiles.

    The asymmetry this closes is the whole reason it exists: a misspelled table
    is an error from a real database and a plain ``0`` from a dict, and ``0`` is
    a legal answer that a test will happily assert. Raising in both makes a
    typo fail the same way whichever arm is running.
    """
    if table not in owned:
        raise AssertionError(
            f"{table!r} is not one of this service's tables {owned}. A count over "
            f"an unrecognised name would be 0 from the fake and an error from the "
            f"database, and 0 is what a typo looks like."
        )


class SqlStoreRows:
    """Read the real store back, on a connection the service is not using.

    ``self.select`` opens its OWN connection on the shared engine rather than
    borrowing the repository's session. That is the point rather than a detail:
    a read inside the writer's transaction sees uncommitted work, which is the
    one thing a "was it persisted?" assertion must not do. It would turn this
    inspector into an expensive way of agreeing with the code under test.
    """

    #: Every table this service owns, as the mapping names them. Declared by the
    #: subclass and written out rather than read from ``Base.metadata``: an
    #: inspector that derived its vocabulary from the thing it inspects would
    #: accept whatever the mapping happened to say, including a table that had
    #: been renamed out from under a test.
    owned_tables: tuple[str, ...] = ()

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def count(self, table: str) -> int:
        assert_owned(table, self.owned_tables)
        return int(self.select(f"select count(*) from {table}", {})[0][0])

    def select(self, statement: str, parameters: dict[str, Any]) -> list[tuple[Any, ...]]:
        """The one raw-SQL seam, and it is TEST support.

        ``scripts/orm_text_allowlist.json`` governs ``text()`` under ``src/``;
        ``check_orm_standard.py`` scans the source package and not ``tests/``,
        because a test that reads the database behind the adapter's back is
        exactly what a persistence assertion IS. Every value is bound, and a
        table name may only be interpolated after :func:`assert_owned`.
        """
        from sqlalchemy import text

        with self._engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(statement), parameters)]


class FakeStoreRows:
    """The same questions, projected from the in-memory store's own structures.

    IT REACHES PAST THE REPOSITORY'S READ METHODS, deliberately. A projection
    built by calling ``repository.list_widgets()`` answers with whatever the
    read path returns and therefore cannot disagree with it -- so a write that
    never happened and a read that invents its result are indistinguishable. The
    assertion is meant to be independent of the path that wrote it.
    """

    owned_tables: tuple[str, ...] = ()

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def inner(self) -> Any:
        """The real fake, through any wrapper a test put in front of it.

        An edge-case test that swaps in a store which fails on one operation
        wraps the fake rather than replacing it, so the corpus is still there.
        Without this the inspector would be asking the WRAPPER for attributes it
        does not have, and the resulting ``AttributeError`` reads as a broken
        inspector rather than as a wrapper doing its job.
        """
        inner = getattr(self._repository, "inner", None)
        return self._repository if inner is None else inner

    def count(self, table: str) -> int:
        assert_owned(table, self.owned_tables)
        return self._count(table)

    def _count(self, table: str) -> int:
        """How many rows the fake holds for an OWNED table. Service-specific."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _count(table) for "
            f"{self.owned_tables}; the base has already refused anything else."
        )
