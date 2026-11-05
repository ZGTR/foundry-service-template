"""A server-free SQLAlchemy engine, for the tests whose subject is not the engine.

WHEN THIS IS THE RIGHT INSTRUMENT AND WHEN IT IS NOT. A test asking whether the
composition root builds the graph it claims, whether a scheduled handler calls
the drain callable it was handed, or whether a JSON fixture reaches the
repository, is not asking anything about PostgreSQL. Making those tests wait on
a container would make them slow enough to be turned off, and turning them off
is how a suite stops being run.

A test asking what a `timestamptz` column hands back, what a rolled-back flush
leaves behind, or how a unique violation surfaces IS asking about the engine,
and SQLite answers for SQLite. Those arms use the kit's real profile
(`foundry_testkit.real_profile`) and a real server; see
`tests/contract/test_widget_repository_port_contract.py` and
`tests/integration/test_outbox_transaction.py`, both of which run BOTH.

`create_all` RATHER THAN `alembic upgrade head`, and only here. The migrations
are what a deployment applies, so they are exercised against the real server,
where the schema they produce is the schema the service shares. Building this
one from the models is a statement that these tests are about mechanism rather
than about schema fidelity -- and it keeps a server-free arm from silently
becoming a second, divergent schema authority that nothing compares.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from foundry_data_gateway.adapters.real.orm import Base

__all__ = ["local_database_url", "local_engine", "local_sessions"]


def local_engine(path: Path | None = None) -> Engine:
    """A migrated-equivalent SQLite engine. In memory unless given a path.

    An in-memory database gets a per-thread singleton connection from
    SQLAlchemy, so everything on the calling thread sees the same database and
    nothing survives the process. Pass a path where a test needs a SECOND
    connection to read what the first wrote -- which is how a test distinguishes
    "committed" from "visible to the session that wrote it".
    """
    engine = create_engine("sqlite+pysqlite://" if path is None else f"sqlite+pysqlite:///{path}")
    Base.metadata.create_all(engine)
    return engine


def local_database_url(prefix: str) -> str:
    """A fresh SQLite FILE carrying this service's schema, as a `DATABASE_URL`.

    For a test that builds a CONTAINER rather than a session: `build_container`
    opens its own engine from the URL, so an in-memory database created here
    would be a different database from the one the container reaches, and every
    readiness probe would report "not ready" for a reason that has nothing to do
    with the wiring under test.

    Called once per module, at import. A per-test database would be cleaner and
    would also make every one of these modules pay a file create for tests that
    never touch a row; the tests that DO care about isolation between rows are
    the ones in `tests/integration/test_outbox_transaction.py`, which take a
    fresh `tmp_path` each time.
    """
    path = Path(tempfile.mkdtemp(prefix=prefix)) / "service.db"
    local_engine(path).dispose()
    return f"sqlite+pysqlite:///{path}"


def local_sessions(path: Path | None = None) -> sessionmaker[Session]:
    """A session factory over `local_engine`, configured as the service configures its own.

    `expire_on_commit=False` matches `composition/wiring.py`. A test harness that
    configured its sessions differently from the composition root would be
    measuring a different object than the one that ships.
    """
    return sessionmaker(bind=local_engine(path), expire_on_commit=False)
