from __future__ import annotations

import socket
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from foundry_contracts.clock import FrozenClock
from foundry_contracts.errors import ConfigurationError
from foundry_contracts.principal import PrincipalMinter

from foundry_data_gateway.adapters.real.in_process_event_bus import InProcessEventBus
from foundry_data_gateway.adapters.real.outbox_event_bus import OutboxEventBus
from foundry_data_gateway.composition.config import ServiceConfig
from foundry_data_gateway.composition.container import build_container
from tests.support.engines import local_engine

#: A server-free database with this service's schema in it, built once for the
#: whole module. A FILE rather than `:memory:`, because the container builds its
#: OWN engine from the URL: an in-memory database created here would be a
#: different database from the one the container opens, and every readiness
#: probe below would report "not ready" for a reason that has nothing to do with
#: the wiring these tests are about.
_LOCAL_DB = Path(tempfile.mkdtemp(prefix="foundry-container-test-")) / "container.db"
local_engine(_LOCAL_DB).dispose()
LOCAL_DB_URL = f"sqlite+pysqlite:///{_LOCAL_DB}"


def _config(**overrides: object) -> ServiceConfig:
    fields = dict(
        service_env="test",
        port=8080,
        principal_issuer="test-issuer",
        database_url=LOCAL_DB_URL,
        event_bus_mode="in_process",
        sqs_queue_url=None,
        aws_region=None,
    )
    fields.update(overrides)
    return ServiceConfig(**fields)


def test_container_builds_from_config_alone() -> None:
    container = build_container(_config())

    assert container.config.service_env == "test"
    assert container.widget_use_cases is not None
    assert container.readiness_check.check() is True
    # No session yet, and the readiness probe above has already run a query --
    # which is the point: `SqlUnitOfWork` opens one per call and closes it, so a
    # leaked session would show up here as a non-None attribute rather than as a
    # connection-pool exhaustion several hundred requests later.
    assert container.unit_of_work._session is None


def test_container_builds_with_zero_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_sockets(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("build_container() must not open a network socket")

    monkeypatch.setattr(socket, "socket", _no_sockets)

    container = build_container(_config())

    assert container is not None


def test_a_fake_clock_reaches_the_service() -> None:
    frozen = FrozenClock(datetime(2020, 1, 1, tzinfo=UTC))

    container = build_container(_config(), clock=frozen)
    principal = PrincipalMinter(issuer="t").mint(
        org_id="org_x", subject_id="user_x", scopes={"widgets:write"}
    )
    widget = container.widget_use_cases.create.execute(principal, name="Cog")

    assert widget.created_at == frozen.now()


def test_every_port_can_be_overridden_independently() -> None:
    from foundry_data_gateway.adapters.fake import (
        FakeReadinessCheck,
        InMemoryWidgetRepository,
        RecordingPublisher,
    )

    publisher = RecordingPublisher()
    repository = InMemoryWidgetRepository()
    readiness = FakeReadinessCheck(ready=False)

    container = build_container(
        _config(),
        widget_repository=repository,
        publisher=publisher,
        readiness_check=readiness,
    )

    assert container.widget_repository is repository
    assert container.publisher is publisher
    assert container.readiness_check is readiness
    assert container.readiness_check.check() is False


def test_in_process_event_bus_mode_builds_the_in_process_publisher() -> None:
    container = build_container(_config(event_bus_mode="in_process"))

    assert isinstance(container.publisher, InProcessEventBus)


def test_an_unknown_event_bus_mode_never_reaches_the_container() -> None:
    """S18 moved this refusal EARLIER, from the container to the config boundary.

    `event_bus_mode` was annotated `Literal` and fed through a
    `# type: ignore[arg-type]`, so `ServiceConfig(event_bus_mode="kafka")` built
    happily and `build_publisher()` was the first thing to object. Now the
    `Literal` is enforced where the value enters, which is the right place: a
    deployment learns at startup rather than at the first publish.
    """
    with pytest.raises(ValueError, match="event_bus_mode"):
        _config(event_bus_mode="kafka")


def test_the_containers_own_unknown_mode_guard_still_fires() -> None:
    """The half the test above no longer covers, and it is not redundant.

    Moving a refusal earlier is how a LATER guard quietly stops being reachable
    and then stops being maintained. `model_construct` builds a `ServiceConfig`
    WITHOUT validation -- which is what a future caller assembling a config from
    a cached dict would do -- so this drives `build_publisher()`'s own branch
    directly and keeps `ConfigurationError` a live path rather than dead code
    behind a validator.
    """
    unvalidated = ServiceConfig.model_construct(
        service_env="test",
        port=8080,
        principal_issuer="test-issuer",
        database_url=LOCAL_DB_URL,
        event_bus_mode="kafka",
        sqs_queue_url=None,
        aws_region=None,
    )

    with pytest.raises(ConfigurationError, match="unknown event_bus_mode"):
        build_container(unvalidated)


# COMMS-FLAVOUR-STRIP:BEGIN: the SQS wiring branch,
# no point-to-point publish path for comms services
def test_sqs_event_bus_mode_builds_a_publisher_that_opens_no_client_yet() -> None:
    """The WIRING property, with no vendor handle patched.

    This test used to `monkeypatch.setattr(..., "_boto3", FakeBoto3())` to watch
    a fake client be constructed. That asserts something about the patch: a
    vendor SDK handle is not a collaborator with a port, so §7 gives it neither
    a fake nor an exemption -- it gives it a REAL endpoint. The publish path is
    now proved against one in
    `tests/integration/test_sqs_publisher_real_endpoint.py`, and what is left
    here is the part that needs no AWS at all: the graph builds, selects the SQS
    adapter, and has NOT initialised an SDK client.
    """
    from foundry_data_gateway.adapters.real.sqs_event_bus import SqsPublisher

    container = build_container(
        _config(
            event_bus_mode="sqs",
            sqs_queue_url="https://sqs.example/widgets",
            aws_region="eu-west-1",
        )
    )

    assert isinstance(container.publisher, SqsPublisher)
    assert container.publisher._client is None, (
        "constructing the graph must not initialize an SDK client"
    )
# COMMS-FLAVOUR-STRIP:END


def test_outbox_event_bus_mode_builds_a_publisher_on_the_repository_unit_of_work() -> None:
    container = build_container(_config(event_bus_mode="outbox"))

    assert isinstance(container.publisher, OutboxEventBus)
    assert container.publisher._uow is container.unit_of_work


# --- the credential seam: who builds the SecretsPort, and when ----------------
#
# `AwsSecretsManagerSecrets` was constructed by NOTHING in production. It sat under
# `adapters/real/` wearing a port's name, its own unit tests green, while every entrypoint
# called `build_container(config)` with no `secrets=` -- so a service emitted from this
# template and deployed against a password-protected database could not resolve one at
# all. This is the copy the other services inherited that defect from, so the arms belong
# here.
#
# THE TRIGGER MOVED, AND THE ARMS MOVED WITH IT. It used to be `db_backend == "mysql"`,
# which tied the credential seam to one dialect; it is now whether DB_PASSWORD_SECRET_ID
# NAMES a secret, which is the question that was always being asked.


def _recording_aws(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Record every region an AWS secrets client is built for, and build none."""
    import foundry_data_gateway.composition.container as container_module

    built: list[str | None] = []

    class _Recorded:
        def __init__(self, region: str | None) -> None:
            built.append(region)

        def get(self, name: str) -> str:
            return "stub-secret-value"

    monkeypatch.setattr(container_module, "AwsSecretsManagerSecrets", _Recorded)
    return built


def test_a_service_with_NO_named_secret_builds_NO_client__positive_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL THE ARMS BELOW NEED, and it CARRIES A REGION deliberately.

    With `aws_region=None` a `build_secrets` mutated to build unconditionally would still
    return None one line later, so this arm would stay green and prove the wrong clause.
    With a region set, the absence of a NAMED SECRET is the only remaining reason not to
    build a client.
    """
    built = _recording_aws(monkeypatch)

    container = build_container(_config(aws_region="eu-west-1"))

    assert built == []
    assert container.widget_repository is not None


def test_a_named_secret_builds_the_AWS_client_FROM_THE_ROOT(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entered through `build_container`, deliberately. Calling `build_secrets` directly
    would prove that a function returns what it returns; the question is whether the
    composition root reaches it, which is the half a unit test of the helper cannot see."""
    built = _recording_aws(monkeypatch)

    build_container(_config(aws_region="eu-west-1", db_password_secret_id="svc/db"))

    assert built == ["eu-west-1"]


def test_a_named_secret_with_NO_REGION_is_refused_naming_an_ENV_VAR(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment reaches this and a task definition has no call site to edit, so the
    remedy has to be something an operator can perform. A client with no region cannot
    answer, and guessing one silently retargets every lookup at another account."""
    built = _recording_aws(monkeypatch)

    with pytest.raises(ConfigurationError) as refusal:
        build_container(_config(db_password_secret_id="svc/db"))

    assert built == []
    assert "AWS_REGION" in str(refusal.value)
    assert "DB_PASSWORD_SECRET_ID" in str(refusal.value)


def test_an_injected_SecretsPort_short_circuits_the_DEFAULT_and_never_builds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The precedence arm, entered through the root. `secrets or build_secrets(config)`
    must short-circuit, because `build_secrets` CONSTRUCTS a network-adjacent AWS client.

    Two clauses, two instruments: the explode-patch catches an EAGER default, and the
    recorded-empty assertion catches an operand SWAP (`build_secrets(config) or secrets`),
    which reaches the patch too and would otherwise red for the wrong reason."""
    import foundry_data_gateway.composition.container as container_module

    class _Secrets:
        def get(self, name: str) -> str:
            return "resolved-by-the-injected-port"

    def _no_secrets(config):  # pragma: no cover - reaching it IS the failure
        raise AssertionError("build_secrets() ran even though secrets= was injected")

    monkeypatch.setattr(container_module, "build_secrets", _no_secrets)

    container = build_container(
        _config(aws_region="eu-west-1", db_password_secret_id="svc/db"),
        secrets=_Secrets(),
    )

    assert container.config.db_password_secret_id == "svc/db"
