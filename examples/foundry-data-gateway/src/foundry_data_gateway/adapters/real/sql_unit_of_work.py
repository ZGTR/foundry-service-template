"""The real `TransactionalUnitOfWork`: one SQLAlchemy `Session` per thread.

WHAT REPLACED WHAT. This module is the ORM successor to the deleted
`sqlite_unit_of_work.py` and `mysql_unit_of_work.py` pair, and the three
properties they were written for are all still here, so the replacement is a
change of mechanism rather than of contract:

* the session opens LAZILY, on first `session()`, never in `__init__` --
  `tests/unit/test_container.py` asserts the container graph builds with zero
  network access, and a constructor that connected would break it;
* it is THREAD-LOCAL, so two concurrent requests never share one transaction
  and one request's rollback cannot discard another's writes;
* `commit()` and `rollback()` both CLOSE, so a failed commit cannot leave a
  half-dead session for the next request on that thread to inherit.

WHAT IT NO LONGER DOES, deliberately: it issues NO DDL. The SQLite adapter ran
`CREATE TABLE IF NOT EXISTS` on connect because its database was a scratch file
that existed only for the process that opened it. A Postgres instance is shared
and versioned, and this estate's rule is that migrations own schema. An adapter
that created its tables would race the migration that owns them and mask a
missing deploy; `migrations/` is the authority, and `alembic upgrade head` is
what puts the schema there.

THE SESSION FACTORY IS BUILT IN THE COMPOSITION ROOT, not here. `sessionmaker`
binds an `Engine`, and an `Engine` owns a connection pool -- constructing one
per unit of work would give each request its own pool and exhaust
`max_connections` inside a single test suite. Taking the factory as a
constructor argument puts that lifetime decision in `composition/wiring.py`,
where every other lifetime in this service is decided, and lets a test hand in
a factory bound to its own per-worker database without a test-only seam in
production code.

`expire_on_commit=False` on that factory (see `composition/wiring.py`) is what
makes a committed record still readable, which matters because `commit()` here
also closes: with expiry on, any attribute access after commit raises
`DetachedInstanceError` instead of returning the value that was just stored.
"""

from __future__ import annotations

from threading import local

from sqlalchemy.orm import Session, sessionmaker

__all__ = ["SqlUnitOfWork"]


class SqlUnitOfWork:
    """A lazily-opened, thread-local `Session` with explicit commit/rollback."""

    def __init__(
        self, session_factory: sessionmaker[Session], *, owned_tables: frozenset[str]
    ) -> None:
        self._session_factory = session_factory
        self._owned_tables = owned_tables
        self._state = local()

    @property
    def _session(self) -> Session | None:
        return getattr(self._state, "session", None)

    @_session.setter
    def _session(self, value: Session | None) -> None:
        self._state.session = value

    def session(self) -> Session:
        if self._session is None:
            self._session = self._session_factory()
        return self._session

    def owned_tables(self) -> frozenset[str]:
        return self._owned_tables

    def commit(self) -> None:
        if self._session is not None:
            try:
                self._session.commit()
            finally:
                self.close()

    def rollback(self) -> None:
        if self._session is not None:
            try:
                self._session.rollback()
            finally:
                self.close()

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
