"""The one place a SQLAlchemy `Engine` is constructed.

WHY A FACTORY AND NOT `create_engine(config.database_url)` AT THE CALL SITE.
Two properties have to hold together, and neither is visible from a call site
that just builds an engine.

*Building the graph does no I/O.* `create_engine` is documented as lazy -- it
parses the URL, picks a dialect and creates a pool, and connects on first
checkout. `tests/unit/test_container.py::test_container_builds_with_zero_network_access`
patches `socket.socket` to raise and calls `build_container()` underneath it,
so a factory that connected eagerly would turn that test red rather than
surfacing as a slow start in production. That test is the reason this stays a
factory rather than becoming an engine handed in already open.

*The password never sits on a config object.* A credential on a
`ServiceConfig` field is one `repr()` away from a log line and one
`subprocess` away from a process listing. So `database_url` carries the user,
host, port and database and NO password, and the password arrives through a
callable resolved at CONNECT time by the `do_connect` event -- after the graph
is built, after the process has started, on the first query. This is the same
seam the deleted `MySqlUnitOfWork` expressed as a `password_provider`
argument; moving it into the engine keeps it while removing the hand-written
driver code that carried it.

A URL THAT CARRIES A PASSWORD **AND** A SECRET ID IS REFUSED rather than
resolved by precedence. Two credential authorities for one connection is a
configuration a reader cannot evaluate: whichever wins, the other is a live
credential in a place somebody believed was being used. The refusal names both
environment variables, because a deployment is what reaches it and a task
definition has no call site to edit.

THIS MODULE IS `check_orm_standard.py` CLAUSE 1'S ONLY DECLARED EXCEPTION -- the
one file under `src/` permitted to import a DBAPI driver. It imports none: the
driver is selected by the URL's dialect (`postgresql+psycopg`), loaded by
SQLAlchemy, and never named here. The exception exists for the service that
genuinely needs a driver-specific connect argument, and the template
deliberately does not use it, which is why the gate carries a fixture for that
clause instead of relying on this file to exercise it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from foundry_contracts.errors import ConfigurationError
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

__all__ = ["build_engine", "parse_database_url"]


def parse_database_url(database_url: str) -> URL:
    """Parse and validate `database_url`, refusing anything unusable.

    Parsed HERE rather than at first connect: a malformed URL should kill the
    process at startup, not on the first request that reaches persistence.
    """
    if not database_url:
        raise ConfigurationError(
            "DATABASE_URL is empty. This service persists through SQLAlchemy and has no "
            "local fallback: set DATABASE_URL to a SQLAlchemy URL, e.g. "
            "postgresql+psycopg://user@host:5432/dbname. There is deliberately no default "
            "-- a service silently handed a local file would look green while writing to "
            "storage nobody backs up."
        )
    try:
        return make_url(database_url)
    except ArgumentError as exc:
        raise ConfigurationError(
            f"DATABASE_URL is not a SQLAlchemy URL ({exc}). Expected "
            "<dialect>+<driver>://<user>@<host>:<port>/<database>."
        ) from exc


def build_engine(
    database_url: str,
    *,
    password_provider: Callable[[], str] | None = None,
) -> Engine:
    """A lazily-connecting `Engine`. Opens no socket and resolves no secret here."""
    url = parse_database_url(database_url)
    if password_provider is not None and url.password:
        raise ConfigurationError(
            "DATABASE_URL carries a password AND DB_PASSWORD_SECRET_ID names a secret. "
            "Two credential authorities for one connection means one of them is a live "
            "credential somebody believes is in use. Remove the password from "
            "DATABASE_URL, or unset DB_PASSWORD_SECRET_ID."
        )
    # `pool_pre_ping` costs one round trip per checkout and buys the case a
    # long-lived service always eventually hits: a connection the database or a
    # proxy closed while the pool believed it was open. Without it the first
    # request after an idle period fails, once, with a driver-level error.
    engine = create_engine(url, pool_pre_ping=True)
    if password_provider is not None:
        _inject_password_at_connect_time(engine, password_provider)
    return engine


def _inject_password_at_connect_time(
    engine: Engine, password_provider: Callable[[], str]
) -> None:
    """Resolve the credential on the first real connection, never before."""

    @event.listens_for(engine, "do_connect")
    def _supply_password(
        _dialect: Any, _record: Any, _args: list[Any], params: dict[str, Any]
    ) -> None:
        params["password"] = password_provider()
        # Returning None lets SQLAlchemy perform the connect itself with the
        # parameters this handler just amended. Returning a connection here
        # would make this function the connector, which is a different and much
        # larger responsibility than supplying one field.
        return None
