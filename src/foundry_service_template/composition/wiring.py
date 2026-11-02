"""Concrete adapter selection for the composition root.

Each function represents a configuration axis that exists in a generated
service.  Keeping the axes separate makes unsupported combinations fail where
they are selected and leaves ``build_container`` as graph assembly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from foundry_contracts.errors import ConfigurationError
from foundry_contracts.principal import PrincipalMinter
from foundry_contracts.unit_of_work import UnitOfWork
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from foundry_service_template.adapters.fake import HeaderPrincipalAuth
from foundry_service_template.adapters.real import (
    InProcessEventBus,
    OutboxEventBus,
    RequestTransaction,
    SqlUnitOfWork,
    SqlWidgetRepository,
    build_engine,
)
from foundry_service_template.composition.config import ServiceConfig
from foundry_service_template.ports import (
    PrincipalAuth,
    Publisher,
    SecretsPort,
    TransactionalUnitOfWork,
    WidgetRepository,
)

#: The environments where unverified header auth is acceptable. NOT `dev`:
#: `deploy/iac/serverless.yml` defaults the stage to `dev` and sets
#: `SERVICE_ENV` from it, so `dev` names a DEPLOYED environment, and counting it
#: here handed every service emitted from this template header-trusting auth in
#: its first deployment, by omission.
LOCAL_ENVS = frozenset({"local", "test"})


class InsecureDefaultAuth(RuntimeError):
    """Raised when a non-local service would fall back to unverified header auth."""


@dataclass(frozen=True, slots=True)
class Persistence:
    repository: WidgetRepository
    unit_of_work: TransactionalUnitOfWork
    transaction: RequestTransaction


#: The tables this service owns and the unit of work therefore grants writes to.
#: HAND-WRITTEN rather than read from `Base.metadata`: the grant is what the
#: service CLAIMS, and deriving it from the mapping it is supposed to constrain
#: would make `assert_writes_within` true of any mapping, including one that
#: grew a table nobody reviewed.
OWNED_TABLES = frozenset({"widgets", "outbox"})


def build_persistence(
    config: ServiceConfig, secrets: SecretsPort | None, *, engine: Engine | None = None
) -> Persistence:
    """The ONE persistence axis: a SQLAlchemy engine built from `database_url`.

    THIS FUNCTION USED TO BE A DIALECT SWITCH over two hand-written adapters,
    and the switch is gone rather than extended. `db_backend='sqlite'` and
    `db_backend='mysql'` selected `SqliteWidgetRepository`/`MySqlWidgetRepository`,
    two classes whose only difference was driver mechanics -- placeholder
    syntax, cursor versus connection, whether a `datetime` came back aware. The
    ORM absorbs all three, so there is one repository and the engine is chosen
    by the URL's dialect. An unrecognised dialect is refused by SQLAlchemy when
    the URL is parsed, at startup, which is where the old `unknown db_backend`
    refusal was and is the property that mattered: there is still no
    fall-through to a local file.

    THE SESSION FACTORY IS BUILT HERE, once per container, and that is a
    lifetime decision rather than a detail. A `sessionmaker` binds an `Engine`,
    and an `Engine` owns a connection pool; building one per unit of work or
    per request would give every request its own pool and exhaust the server's
    `max_connections` inside a single suite.

    `engine=` IS THAT SAME LIFETIME DECISION, MOVED ONE LEVEL OUT, and it is
    configuration rather than a test hook. A caller that already owns an `Engine`
    for this database hands it in instead of having a second one built: the kit's
    real test profile owns ONE engine per xdist worker and builds a container per
    test, so without this seam a suite of a hundred tests would open a hundred
    pools against one server and exhaust `max_connections` partway through --
    reported as a connection error attributed to whichever test was unlucky. The
    same seam serves a runtime that pools engines across handlers. When it is
    None, which is every deployment today, the engine is built from `config`
    exactly as before.
    """
    engine = engine if engine is not None else build_database_engine(config, secrets)
    # `expire_on_commit=False`: `SqlUnitOfWork.commit()` also closes the
    # session, so with expiry on, reading any attribute of a record that was
    # just stored raises `DetachedInstanceError` instead of returning it.
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    uow = SqlUnitOfWork(session_factory, owned_tables=OWNED_TABLES)
    return Persistence(SqlWidgetRepository(uow), uow, RequestTransaction(uow))


def build_database_engine(config: ServiceConfig, secrets: SecretsPort | None) -> Engine:
    """The ONE expression that turns configuration into an `Engine`.

    Public because `migrations/env.py` calls it too. An alembic `env.py` that built its
    own engine would be a second answer to "which database, with which credential" -- and
    the failure mode of a second answer here is the worst one available: a deployment that
    migrates one database and serves another, with both halves reporting success.
    """
    return build_engine(config.database_url, password_provider=_password_provider(config, secrets))


def _password_provider(
    config: ServiceConfig, secrets: SecretsPort | None
) -> Callable[[], str] | None:
    """The credential seam: a callable resolved at CONNECT time, or None.

    None where the URL needs no password -- a local run, or a Postgres reached
    over a socket. A NAMED secret with no `SecretsPort` to read it is refused,
    because the alternative is a service that starts, cannot authenticate, and
    reports the failure as an unreachable database.
    """
    secret_id = config.db_password_secret_id
    if secret_id is None:
        return None
    if secrets is None:
        raise ConfigurationError(
            "DB_PASSWORD_SECRET_ID names a secret but this deployment has no SecretsPort "
            "to read it with. Set AWS_REGION so `build_secrets()` can build the Secrets "
            "Manager adapter, or unset DB_PASSWORD_SECRET_ID and put the credential in "
            "DATABASE_URL's userinfo. There is deliberately no DB_PASSWORD environment "
            "fallback: a password in the environment is readable by every child process "
            "and printed by every crash dump that dumps the environment. "
            "(A test may also pass secrets=<adapter> to build_container().)"
        )
    return lambda: secrets.get(secret_id)


def build_publisher(config: ServiceConfig, unit_of_work: UnitOfWork | None) -> Publisher:
    if config.event_bus_mode == "in_process":
        return InProcessEventBus()
    # COMMS-FLAVOUR-STRIP:BEGIN: the SQS point-to-point publish
    # path -- comms services hold only events:PutEvents on their own bus
    if config.event_bus_mode == "sqs":
        from foundry_service_template.adapters.real import SqsPublisher

        return SqsPublisher(
            queue_url=config.sqs_queue_url or "",
            region_name=config.aws_region,
            endpoint_url=config.aws_endpoint_url,
        )
    # COMMS-FLAVOUR-STRIP:END
    if config.event_bus_mode == "outbox":
        # There is no dialect exclusion here any more. `event_bus_mode='outbox'`
        # used to be refused for `db_backend='mysql'`, because the raw-SQL
        # publisher wrote `?` placeholders that only SQLite understood -- a
        # driver detail leaking all the way up into a supported-configuration
        # matrix. The ORM writes the row, so every dialect `DATABASE_URL` can
        # name publishes the same way.
        if unit_of_work is None:
            # TWO READERS, TWO REMEDIES. `check_refusal_remedy_is_actionable.py`
            # classes this refusal as deployment-reachable because its enclosing
            # function reads configuration, and the old message named only
            # `publisher=<adapter>` -- an instruction an ECS task definition cannot
            # follow, since a deployment has no call site to edit. Whether a
            # deployment can actually reach it is a narrower question than that gate
            # asks, and the answer does not change what the message owes an operator
            # who is looking at it: the setting that selected this path, by name.
            raise ConfigurationError(
                "event_bus_mode='outbox' needs the persistence UnitOfWork, and this "
                "container was built without one. A deployment reaches this by setting "
                "EVENT_BUS_MODE=outbox with no working persistence: set "
                "EVENT_BUS_MODE=in_process if this deployment publishes in process, or "
                "point DATABASE_URL at a database the outbox can write to. A caller that "
                "overrode only widget_repository must pass publisher=<adapter> as well."
            )
        return OutboxEventBus(unit_of_work)
    raise ConfigurationError(
        f"unknown event_bus_mode={config.event_bus_mode!r}; supported modes are "
        "'in_process'"
        ", 'sqs'"  # COMMS-FLAVOUR-STRIP-LINE: not a mode comms services have
        ", and 'outbox'"
    )


def build_principal_auth(config: ServiceConfig) -> PrincipalAuth:
    if bool(config.auth_jwks_uri) != bool(config.auth_audience):
        raise ConfigurationError("AUTH_JWKS_URI and AUTH_AUDIENCE must be configured together")
    if config.auth_jwks_uri and config.auth_audience:
        from foundry_service_template.adapters.real import JwksKeyResolver, JwtPrincipalAuth

        return JwtPrincipalAuth(
            PrincipalMinter(issuer=config.principal_issuer),
            JwksKeyResolver(config.auth_jwks_uri),
            issuer=config.principal_issuer,
            audience=config.auth_audience,
        )
    if config.service_env in LOCAL_ENVS:
        return HeaderPrincipalAuth(PrincipalMinter(issuer=config.principal_issuer))
    raise InsecureDefaultAuth(
        f"env={config.service_env!r} has neither principal_auth wired nor "
        "AUTH_JWKS_URI/AUTH_AUDIENCE set, and HeaderPrincipalAuth trusts "
        "unverified request headers. Set AUTH_JWKS_URI and "
        f"AUTH_AUDIENCE, pass principal_auth=<adapter>, or use {sorted(LOCAL_ENVS)} locally."
    )
