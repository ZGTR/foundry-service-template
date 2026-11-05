"""Alembic's runtime environment for this service.

THREE THINGS ARE DECIDED HERE, and each is here because the default is wrong.

*Which database.* Through `ServiceConfig.from_environment()` and the service's
own `build_database_engine`, never `create_engine` on a URL read out of
`alembic.ini`. The service and its migrations must be incapable of disagreeing
about which database they mean, and the only way to guarantee that is one
expression that answers it. The credential comes the same way it does at
runtime -- from the `SecretsPort` when `DB_PASSWORD_SECRET_ID` names one, never
from a password in the URL.

*Which metadata.* `Base.metadata`, imported through `adapters.real.orm`, whose
`__init__` imports every record module. A record class alembic never imports is
a table the metadata never learns about, and autogenerate then reports the
schema as up to date while the table is missing -- silently, at exit 0.

*Which version table.* `foundry_alembic_version`, DECLARED. Alembic's default
is the bare name `alembic_version`, and this estate has already paid for that
once: two products sharing one database each took the default, so one chain's
tracking row was the other's, and `upgrade head` on either could report itself
up to date having applied nothing. A declared name costs one line and makes the
collision impossible.

`compare_type=True` because the default is False: without it, changing a column
from `String(191)` to `String(255)` -- or from `DateTime` to
`DateTime(timezone=True)`, which is this service's most load-bearing column
property -- autogenerates an EMPTY revision, and the reader concludes the
change needs no migration.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from foundry_data_gateway.adapters.real.orm import Base
from foundry_data_gateway.composition.config import ServiceConfig
from foundry_data_gateway.composition.container import build_secrets
from foundry_data_gateway.composition.wiring import build_database_engine

#: Declared, never defaulted. See the module docstring.
VERSION_TABLE = "foundry_alembic_version"

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """`--sql` mode: emit DDL to stdout without connecting.

    It still resolves the URL through `ServiceConfig`, so the dialect the DDL is
    generated for is the dialect the service actually runs. Generating Postgres
    DDL for a deployment on anything else is a review that passes and a script
    that fails.
    """
    config = ServiceConfig.from_environment()
    context.configure(
        url=config.database_url,
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config = ServiceConfig.from_environment()
    engine = build_database_engine(config, build_secrets(config))
    # `dispose()` in a `finally`, and it is not tidiness. The kit's real test
    # profile runs `alembic upgrade head` into a TEMPLATE database and then
    # clones it, and Postgres refuses `CREATE DATABASE ... TEMPLATE` while any
    # session is still attached. A connection left open here makes that clone
    # fail with an error about the template, several steps away from the cause.
    try:
        with engine.connect() as connection:
            _run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
