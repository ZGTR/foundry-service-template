"""`build_container()` reaches its database through `config.database_url`, lazily.

THIS FILE REPLACES `test_container_db_backend.py`, and the three properties it
guarded are all still here because all three still matter -- the mechanism
changed, the risks did not:

* the container builds WITHOUT connecting, so the graph stays free of I/O and of
  secret material;
* a NAMED credential is resolved at connect time, never at build time;
* a configuration that cannot be honoured is REFUSED rather than quietly
  downgraded. There is no `else: sqlite` fall-through, because a service that
  asked for a database it did not get and was handed a local file would look
  green while writing to storage nobody backs up
  (`knowledge/wiki/a-fall-through-inherits-the-branchs-severity.md`).

WHAT IS NO LONGER TESTED, AND WHY THAT IS NOT A GAP. The old file's
`test_unknown_backend_is_refused_rather_than_falling_back` asserted on a
`db_backend` string this service no longer has. The equivalent question --
"what happens to a database this service cannot reach" -- is now answered one
layer down, by SQLAlchemy refusing an unknown dialect when the URL is parsed,
and `test_an_unparseable_url_is_refused_at_build_time` is that assertion.
"""

from __future__ import annotations

import socket

import pytest
from foundry_contracts.errors import ConfigurationError

from foundry_service_template.adapters.real.sql_widget_repository import SqlWidgetRepository
from foundry_service_template.composition.config import ServiceConfig
from foundry_service_template.composition.container import build_container

#: A URL that is well-formed, names a host that will never resolve, and is never
#: connected to by anything in this file. That combination is the point: if any
#: assertion here started opening a connection, it would hang or fail rather
#: than pass, so "the graph does no I/O" cannot quietly stop being true.
UNREACHABLE_URL = "postgresql+psycopg://svc@db.invalid:5432/widgets"


def _config(**overrides: object) -> ServiceConfig:
    fields: dict[str, object] = dict(
        service_env="test",
        port=8080,
        principal_issuer="test-issuer",
        database_url=UNREACHABLE_URL,
        event_bus_mode="in_process",
        sqs_queue_url=None,
        aws_region=None,
    )
    fields.update(overrides)
    return ServiceConfig(**fields)  # type: ignore[arg-type]


def test_the_container_wires_the_sql_repository() -> None:
    container = build_container(_config())

    assert isinstance(container.widget_repository, SqlWidgetRepository)


def test_building_the_graph_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """`create_engine` is documented as lazy; this is the assertion that keeps it so.

    A factory that connected eagerly would not fail loudly -- it would make
    every process start slower and every container build depend on the database
    being up, which surfaces as a deployment that times out rather than as a
    test that goes red.
    """

    def _no_sockets(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("build_container() must not open a network socket")

    monkeypatch.setattr(socket, "socket", _no_sockets)

    assert build_container(_config()).widget_repository is not None


def test_a_named_credential_is_not_resolved_while_the_graph_is_built() -> None:
    """The secret is read on the first CONNECT, not on the way to one.

    Resolving it here would put a Secrets Manager round trip inside every
    container build -- including the ones a test performs -- and would place the
    credential on an object that a traceback can print.
    """

    class ExplodingSecrets:
        def get(self, name: str) -> str:
            raise AssertionError(f"the credential {name!r} was resolved at build time")

    container = build_container(
        _config(db_password_secret_id="foundry/widgets/db-password", aws_region="eu-west-1"),
        secrets=ExplodingSecrets(),
    )

    assert container.widget_repository is not None


def test_a_named_credential_with_no_secrets_port_is_refused() -> None:
    """A service that cannot read the secret it was told to use must not start.

    The alternative is a process that starts, fails to authenticate, and reports
    it as an unreachable database -- a diagnosis that sends the reader to the
    network.
    """
    with pytest.raises(ConfigurationError, match="DB_PASSWORD_SECRET_ID"):
        build_container(_config(db_password_secret_id="foundry/widgets/db-password"))


def test_a_url_carrying_a_password_AND_a_named_secret_is_refused() -> None:
    """Two credential authorities for one connection is not a precedence question.

    Whichever one won, the other would be a live credential sitting somewhere
    a reader believes is in use.
    """
    with pytest.raises(ConfigurationError, match="Two credential authorities"):
        build_container(
            _config(
                database_url="postgresql+psycopg://svc:hunter2@db.invalid:5432/widgets",
                db_password_secret_id="foundry/widgets/db-password",
                aws_region="eu-west-1",
            ),
            secrets=_StubSecrets(),
        )


def test_an_unparseable_url_is_refused_at_build_time() -> None:
    with pytest.raises(ConfigurationError, match="DATABASE_URL is not a SQLAlchemy URL"):
        build_container(_config(database_url="not a url at all"))


def test_an_empty_url_is_refused_by_the_config_itself_naming_the_variable() -> None:
    """Refused at CONFIG construction, which is earlier than the container.

    A deployment that forgot the variable should die while reading its
    configuration, not on the first request that reaches persistence -- and the
    message has to name something a task definition can set.
    """
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _config(database_url="")


class _StubSecrets:
    def get(self, name: str) -> str:  # pragma: no cover - refused before it is called
        return "never-read"
