"""§6 through a REAL ASGI request: correlation id and in-flight drain tracking.

These properties are only true if the middleware is actually ATTACHED. A unit
test of `CorrelationIdMiddleware.dispatch` would pass while `create_app` had
stopped adding it -- a unit test cannot see its own caller
(`knowledge/wiki/a-unit-test-cannot-see-its-own-caller.md`), so every assertion
here goes through the app the composition root builds.
"""

from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

from foundry_data_gateway.adapters.fake import (
    InMemoryWidgetRepository,
    RecordingPublisher,
)
from foundry_data_gateway.composition.app_factory import create_app
from foundry_data_gateway.composition.config import ServiceConfig
from foundry_data_gateway.composition.container import build_container
from foundry_data_gateway.runtime import CORRELATION_ID_HEADER, configure_json_logging
from tests.support.engines import local_database_url

#: A server-free database carrying this service's schema, built once for
#: this module. The container opens its OWN engine from this URL, so an
#: in-memory one created here would be a different database.
LOCAL_DB_URL = local_database_url("foundry-middleware-test-")

HEADERS = {
    "x-org-id": "org_mw",
    "x-subject-id": "user_mw",
    "x-scopes": "widgets:read,widgets:write",
}


def _client() -> TestClient:
    container = build_container(
        ServiceConfig(
            service_env="test",
            port=8080,
            principal_issuer="mw-test",
            database_url=LOCAL_DB_URL,
            event_bus_mode="in_process",
            sqs_queue_url=None,
            aws_region=None,
        ),
        widget_repository=InMemoryWidgetRepository(),
        publisher=RecordingPublisher(),
    )
    return TestClient(create_app(container))


def test_a_caller_supplied_correlation_id_is_echoed_back() -> None:
    with _client() as client:
        response = client.get("/widgets", headers={**HEADERS, CORRELATION_ID_HEADER: "trace-42"})

    assert response.status_code == 200
    assert response.headers[CORRELATION_ID_HEADER] == "trace-42"


def test_a_correlation_id_is_minted_when_the_caller_supplies_none() -> None:
    with _client() as client:
        response = client.get("/widgets", headers=HEADERS)

    minted = response.headers[CORRELATION_ID_HEADER]
    assert minted, "every response must carry a correlation id"
    assert len(minted) >= 16


def test_two_requests_get_two_different_ids() -> None:
    with _client() as client:
        first = client.get("/widgets", headers=HEADERS)
        second = client.get("/widgets", headers=HEADERS)

    assert first.headers[CORRELATION_ID_HEADER] != second.headers[CORRELATION_ID_HEADER]


def test_a_log_line_emitted_during_the_request_carries_that_id() -> None:
    """The property the header alone does not prove.

    Echoing an id back is easy; binding it so that application logging PICKS IT
    UP is the part that makes a trace usable, and it is the part a header
    assertion cannot see.
    """
    stream = io.StringIO()
    with _client() as client:
        configure_json_logging("INFO", stream=stream)
        client.post(
            "/widgets",
            json={"name": "Traced"},
            headers={**HEADERS, CORRELATION_ID_HEADER: "trace-logged"},
        )
        logging.getLogger("probe").info("after the request")

    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines() if line]
    during = [line for line in lines if line["correlation_id"] == "trace-logged"]
    assert during, f"no log line carried the request's correlation id: {lines}"


def test_the_id_does_not_leak_into_the_next_request() -> None:
    with _client() as client:
        client.get("/widgets", headers={**HEADERS, CORRELATION_ID_HEADER: "trace-first"})
        second = client.get("/widgets", headers=HEADERS)

    assert second.headers[CORRELATION_ID_HEADER] != "trace-first"


def test_in_flight_returns_to_zero_after_every_request() -> None:
    """The drain counter must not leak, or the drain never completes and every
    deploy waits out the full grace period."""
    container = build_container(
        ServiceConfig(
            service_env="test",
            port=8080,
            principal_issuer="mw-test",
            database_url=LOCAL_DB_URL,
            event_bus_mode="in_process",
            sqs_queue_url=None,
            aws_region=None,
        ),
        widget_repository=InMemoryWidgetRepository(),
        publisher=RecordingPublisher(),
    )
    with TestClient(create_app(container)) as client:
        client.get("/widgets", headers=HEADERS)
        client.get("/widgets", headers=HEADERS)

        assert container.shutdown.in_flight == 0


def test_a_failing_request_still_releases_its_in_flight_slot() -> None:
    container = build_container(
        ServiceConfig(
            service_env="test",
            port=8080,
            principal_issuer="mw-test",
            database_url=LOCAL_DB_URL,
            event_bus_mode="in_process",
            sqs_queue_url=None,
            aws_region=None,
        ),
        widget_repository=InMemoryWidgetRepository(),
        publisher=RecordingPublisher(),
    )
    with TestClient(create_app(container)) as client:
        missing = client.get("/widgets/does-not-exist", headers=HEADERS)

        assert missing.status_code == 404
        assert container.shutdown.in_flight == 0
