"""End-to-end through the real ASGI app, with every port faked -- no network,
no filesystem. This is the test that proves the MVC wiring (route ->
principal -> service -> repository -> response) works as one piece.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from foundry_contracts.clock import FrozenClock

from foundry_service_template.adapters.fake import InMemoryWidgetRepository, RecordingPublisher
from foundry_service_template.composition.app_factory import create_app
from foundry_service_template.composition.config import ServiceConfig
from foundry_service_template.composition.container import build_container
from tests.support.engines import local_database_url

#: A server-free database carrying this service's schema, built once for
#: this module. The container opens its OWN engine from this URL, so an
#: in-memory one created here would be a different database.
LOCAL_DB_URL = local_database_url("foundry-app-http-test-")

AUTH_HEADERS = {
    "x-org-id": "org_1",
    "x-subject-id": "user_1",
    "x-scopes": "widgets:read,widgets:write",
}


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


@pytest.fixture
def client() -> TestClient:
    publisher = RecordingPublisher()
    container = build_container(
        _config(),
        clock=FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)),
        widget_repository=InMemoryWidgetRepository(),
        publisher=publisher,
    )
    app = create_app(container)
    app.state.test_publisher = publisher
    return TestClient(app)


def test_create_get_and_list_round_trip(client: TestClient) -> None:
    created = client.post("/widgets", json={"name": "Sprocket"}, headers=AUTH_HEADERS)
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Sprocket"
    assert body["org_id"] == "org_1"

    fetched = client.get(f"/widgets/{body['widget_id']}", headers=AUTH_HEADERS)
    assert fetched.status_code == 200
    assert fetched.json() == body

    listed = client.get("/widgets", headers=AUTH_HEADERS)
    assert listed.status_code == 200
    assert listed.json()["widgets"] == [body]


def test_create_publishes_widget_created(client: TestClient) -> None:
    client.post("/widgets", json={"name": "Sprocket"}, headers=AUTH_HEADERS)

    published = client.app.state.test_publisher.published
    assert len(published) == 1
    assert published[0].event_type == "widget.created"


def test_create_with_empty_name_is_422(client: TestClient) -> None:
    response = client.post("/widgets", json={"name": "   "}, headers=AUTH_HEADERS)
    assert response.status_code == 422


def test_get_missing_widget_is_404(client: TestClient) -> None:
    response = client.get("/widgets/does_not_exist", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_missing_auth_headers_is_401(client: TestClient) -> None:
    response = client.get("/widgets", headers={})
    assert response.status_code == 401


def test_cannot_read_another_orgs_widget(client: TestClient) -> None:
    created = client.post("/widgets", json={"name": "Sprocket"}, headers=AUTH_HEADERS)
    widget_id = created.json()["widget_id"]

    other_org_headers = {"x-org-id": "org_2", "x-subject-id": "user_2", "x-scopes": "widgets:read"}
    response = client.get(f"/widgets/{widget_id}", headers=other_org_headers)

    assert response.status_code == 404
