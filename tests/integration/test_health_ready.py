"""The three probes answer DIFFERENT questions and must be able to disagree.

`/health/live`, `/health/ready` and `/health/startup` are §6's contract, and
collapsing any two of them is the defect these tests exist to catch. `/healthz`
and `/readyz` are the legacy aliases deployed target groups already point at.

Every client here is entered as a CONTEXT MANAGER, which is what runs the ASGI
lifespan and therefore the idempotent startup. That is not ceremony: a process
whose startup has not completed is correctly NOT ready, so a bare
`TestClient(app)` would report 503 and read as a broken readiness check.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from foundry_service_template.adapters.fake import (
    FakeReadinessCheck,
    InMemoryWidgetRepository,
    RecordingPublisher,
)
from foundry_service_template.composition.app_factory import create_app
from foundry_service_template.composition.config import ServiceConfig
from foundry_service_template.composition.container import Container, build_container
from tests.support.engines import local_database_url

#: A server-free database carrying this service's schema, built once for
#: this module. The container opens its OWN engine from this URL, so an
#: in-memory one created here would be a different database.
LOCAL_DB_URL = local_database_url("foundry-health-test-")


def _config() -> ServiceConfig:
    return ServiceConfig(
        service_env="test",
        port=8080,
        principal_issuer="test-issuer",
        database_url=LOCAL_DB_URL,
        event_bus_mode="in_process",
        sqs_queue_url=None,
        aws_region=None,
    )


def _container(*, ready: bool) -> Container:
    return build_container(
        _config(),
        widget_repository=InMemoryWidgetRepository(),
        publisher=RecordingPublisher(),
        readiness_check=FakeReadinessCheck(ready=ready),
    )


@contextmanager
def _started(*, ready: bool) -> Iterator[tuple[TestClient, Container]]:
    container = _container(ready=ready)
    with TestClient(create_app(container)) as client:
        yield client, container


def test_healthz_is_always_up() -> None:
    with _started(ready=False) as (client, _container_):
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "up"}


def test_readyz_reflects_the_readiness_check() -> None:
    with _started(ready=True) as (client, _container_):
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_healthz_and_readyz_can_disagree() -> None:
    with _started(ready=False) as (client, _container_):
        healthz = client.get("/healthz")
        readyz = client.get("/readyz")

    assert healthz.status_code == 200
    assert readyz.status_code == 503
    assert readyz.json() == {"ready": False}


def test_the_three_probes_are_served_and_carry_named_results() -> None:
    with _started(ready=True) as (client, _container_):
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        startup = client.get("/health/startup")

    assert [live.status_code, ready.status_code, startup.status_code] == [200, 200, 200]
    # The NAME is what makes a red probe actionable. "not ready" tells an
    # operator nothing; "dependencies: not ready" tells them where to look.
    assert [p["name"] for p in ready.json()["probes"]] == ["dependencies"]
    assert live.json()["probes"][0]["name"] == "process"
    assert startup.json()["probes"][0]["name"] == "startup"


def test_liveness_stays_green_while_readiness_is_red() -> None:
    """The distinction that matters most in production.

    A liveness probe that consults a dependency makes an orchestrator KILL a
    healthy process during a database blip, turning a recoverable outage into a
    restart storm. Liveness must not move when a dependency does.
    """
    with _started(ready=False) as (client, _container_):
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert ready.status_code == 503


def test_startup_latches_and_readiness_goes_red_on_a_shutdown_signal() -> None:
    """SIGTERM reddens READINESS immediately and leaves STARTUP latched true.

    Those are different facts: "stop sending me work" and "my one-time startup
    completed" -- and an orchestrator reading a red startup probe KILLS the
    process instead of draining it.
    """
    container = _container(ready=True)
    with TestClient(create_app(container)) as client:
        assert client.get("/health/ready").status_code == 200

        container.shutdown.request_shutdown()

        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/startup").status_code == 200
        assert client.get("/health/live").status_code == 200
