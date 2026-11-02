"""The DI root. Explicit constructor wiring, no framework magic, no service
locator -- `build_container()` is a plain function that returns a plain
dataclass. A test overrides any single port by passing it as a keyword
argument; everything else builds from `config` alone.

Building the graph must never touch the network or the filesystem: every real
adapter here connects lazily (see `adapters.real.sqlite_unit_of_work`), so
`tests/unit/test_container.py::test_container_builds_with_zero_network_access`
can assert this by patching `socket.socket` to raise and calling
`build_container()` underneath it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_contracts.errors import ConfigurationError
from foundry_contracts.principal import CallerPrincipalV1
from sqlalchemy import Engine

from foundry_service_template.adapters.real import (
    AlembicMigrations,
    AwsSecretsManagerSecrets,
    RepositoryReadinessCheck,
)
from foundry_service_template.application import (
    CreateWidget,
    GetWidget,
    ListWidgets,
    WidgetUseCases,
)
from foundry_service_template.composition.config import ServiceConfig
from foundry_service_template.composition.wiring import (
    LOCAL_ENVS,
    InsecureDefaultAuth,
    build_persistence,
    build_principal_auth,
    build_publisher,
)
from foundry_service_template.ports import (
    Clock,
    HealthReporter,
    PrincipalAuth,
    Publisher,
    ReadinessCheck,
    SecretsPort,
    SystemClock,
    TransactionalUnitOfWork,
    TransactionBoundary,
    WidgetRepository,
)
from foundry_service_template.runtime import (
    GracefulShutdown,
    MigrationRunner,
    NamedReadinessProbe,
    RuntimeHealthReporter,
    ServiceLifecycle,
)

__all__ = [
    "Container",
    "InsecureDefaultAuth",
    "LOCAL_ENVS",
    "build_container",
    "build_migrations",
    "build_secrets",
]


@dataclass
class Container:
    config: ServiceConfig
    clock: Clock
    widget_repository: WidgetRepository
    publisher: Publisher
    readiness_check: ReadinessCheck
    principal_auth: PrincipalAuth
    widget_use_cases: WidgetUseCases
    health_reporter: HealthReporter
    shutdown: GracefulShutdown
    lifecycle: ServiceLifecycle
    unit_of_work: TransactionalUnitOfWork | None = None

    def authenticate(self, headers: Mapping[str, str]) -> CallerPrincipalV1:
        return self.principal_auth.authenticate(headers)


def _build_use_cases(
    repository: WidgetRepository,
    publisher: Publisher,
    clock: Clock,
    transaction: TransactionBoundary | None,
) -> WidgetUseCases:
    return WidgetUseCases(
        create=CreateWidget(
            repository=repository, publisher=publisher, clock=clock, transaction=transaction
        ),
        get=GetWidget(repository=repository, transaction=transaction),
        list=ListWidgets(repository=repository, transaction=transaction),
    )


def _build_runtime(
    readiness: ReadinessCheck, config: ServiceConfig
) -> tuple[RuntimeHealthReporter, GracefulShutdown, ServiceLifecycle]:
    """The §6 lifecycle trio, wired so SIGTERM reddens readiness immediately.

    `on_shutdown_signal` is the seam: without it a draining process keeps
    answering `/health/ready` with 200 and the load balancer keeps sending it
    work it is about to stop serving.
    """
    reporter = RuntimeHealthReporter(probes=(NamedReadinessProbe("dependencies", readiness),))

    def _begin_drain() -> None:
        reporter.draining = True

    shutdown = GracefulShutdown(
        on_shutdown_signal=_begin_drain, grace_seconds=config.shutdown_grace_seconds
    )
    lifecycle = ServiceLifecycle(
        reporter=reporter,
        shutdown=shutdown,
        migrations=build_migrations(config),
        log_level=config.log_level,
    )
    return reporter, shutdown, lifecycle


def build_container(
    config: ServiceConfig,
    *,
    clock: Clock | None = None,
    widget_repository: WidgetRepository | None = None,
    publisher: Publisher | None = None,
    readiness_check: ReadinessCheck | None = None,
    principal_auth: PrincipalAuth | None = None,
    secrets: SecretsPort | None = None,
    database_engine: Engine | None = None,
) -> Container:
    """Wire the whole graph from `config`. Every port can be overridden, e.g.
    `build_container(config, clock=FrozenClock(...))` in a test.

    `database_engine=` is a LIFETIME seam rather than a port override, which is why
    it is not a `ServiceConfig` field: an `Engine` owns a connection pool, and that
    dataclass is frozen and read from the environment. See `build_persistence`.
    """
    resolved_clock = clock or SystemClock()
    resolved_secrets = secrets or build_secrets(config)

    persistence = (
        None
        if widget_repository is not None
        else build_persistence(config, resolved_secrets, engine=database_engine)
    )
    if widget_repository is not None:
        resolved_repository = widget_repository
    else:
        assert persistence is not None
        resolved_repository = persistence.repository
    unit_of_work = None if persistence is None else persistence.unit_of_work
    transaction: TransactionBoundary | None = (
        None if persistence is None else persistence.transaction
    )
    resolved_publisher = (
        publisher if publisher is not None else build_publisher(config, unit_of_work)
    )

    resolved_readiness = readiness_check or RepositoryReadinessCheck(
        resolved_repository, transaction
    )
    resolved_auth = principal_auth or build_principal_auth(config)
    reporter, shutdown, lifecycle = _build_runtime(resolved_readiness, config)

    return Container(
        config=config,
        clock=resolved_clock,
        widget_repository=resolved_repository,
        publisher=resolved_publisher,
        readiness_check=resolved_readiness,
        principal_auth=resolved_auth,
        widget_use_cases=_build_use_cases(
            resolved_repository, resolved_publisher, resolved_clock, transaction
        ),
        health_reporter=reporter,
        shutdown=shutdown,
        lifecycle=lifecycle,
        unit_of_work=unit_of_work,
    )


def build_secrets(config: ServiceConfig) -> SecretsPort | None:
    """The credential seam, resolved from configuration rather than per call site.

    THE DEFECT THIS CLOSES. `AwsSecretsManagerSecrets` shipped under `adapters/real/` with
    green unit tests and no constructor anywhere in production: every entrypoint calls
    `build_container(config)` with no `secrets=`, so a service emitted from this template
    and deployed against a password-protected database could not resolve one at all. The
    class existed, its module imported cleanly, and no deployment could make the process
    use it -- which is the shape `scripts/check_unwired_real_adapter.py` exists to find,
    and this is the copy every other service inherited the defect from.

    THE TRIGGER IS `DB_PASSWORD_SECRET_ID`, not a backend name. It used to be
    `db_backend != "mysql"`, which tied the credential seam to one dialect: a Postgres
    deployment with a secret got `None` and no way to say otherwise. What decides whether
    a credential is needed is whether one was NAMED, and that is what this reads.

    `None` where nothing needs a credential is the right answer and not a degraded one: a
    local run resolves its database from the URL alone, so constructing an AWS client for
    it would put a network dependency in the one path that must not have any. It returns
    the AWS adapter and never an in-memory mapping: literal values are the right shape
    for a test (`adapters/fake/secrets.py::FakeSecrets`) and the wrong shape for a
    deployment, because those values would have to reach the process somehow and every
    honest route for that is the environment.
    """
    if not config.db_password_secret_id:
        return None
    if not config.aws_region:
        raise ConfigurationError(
            "DB_PASSWORD_SECRET_ID names a secret but AWS_REGION is unset, so no Secrets "
            "Manager client can be built. A client with no region cannot answer, and "
            "guessing one silently retargets every lookup at another account. Set "
            "AWS_REGION, or unset DB_PASSWORD_SECRET_ID and put the credential in "
            "DATABASE_URL's userinfo."
        )
    return AwsSecretsManagerSecrets(region=config.aws_region)


def build_migrations(config: ServiceConfig) -> MigrationRunner:
    """Wire the startup migration runner from configuration, onto this service's alembic tree.

    THE REFUSING PAIR IS GONE, and its removal is the point of this slice's migration half.
    It handed `MigrationRunner` a `heads`/`upgrade` pair that raised `NoMigrationsToRun`,
    with a message saying the template ships no alembic tree -- correct until `migrations/`
    existed, and a lie the moment it did: a service carrying a revision tree and setting
    `MIGRATE_ON_STARTUP=true` would have died at startup being told to supply one.

    THE DEFECT THIS CLOSES. `MIGRATE_ON_STARTUP` was declared in this template's IaC and
    parsed into `config.migrate_on_startup`, and nothing outside `composition/config.py`
    ever read it: `ServiceLifecycle` took `migrations=None` on every path, so a deployment
    setting the variable to `true` asserted that the process upgrades its schema at
    startup and the process did nothing. Nothing could go red -- the variable resolved,
    the service started, every suite passed, and the claim sat in a task definition, which
    is exactly where a reader treats it as authoritative. It is the same shape
    `scripts/check_iac_env_has_a_reader.py` was written for.

    THE RUNNER IS ALWAYS BUILT, and the flag decides what it does. With the flag false --
    the default, and correct for a service whose schema is applied by a deploy step rather
    than by the process -- `run()` returns an outcome saying so, which is a RECORD rather
    than a silence. With it true, `AlembicMigrations` reads the revision tree's heads,
    `MigrationRunner` asserts there is exactly one, and only then does it upgrade.

    BUILDING THE PAIR OPENS NOTHING. `AlembicMigrations.__init__` stores two strings; the
    alembic `Config`, the script directory and the connection are all created inside
    `heads()`/`upgrade()`, i.e. inside `ServiceLifecycle.start()`. A constructor that
    validated the tree here would run on every container build, including the one
    `tests/unit/test_container.py` performs with `socket.socket` patched to raise.

    Refusals name ENVIRONMENT VARIABLES, not call sites: a deployment is what reaches
    them, and a task definition has no call site to edit.
    """
    migrations = AlembicMigrations(script_location=config.migrations_dir)
    return MigrationRunner(
        enabled=config.migrate_on_startup,
        heads=migrations.heads,
        upgrade=migrations.upgrade,
    )
