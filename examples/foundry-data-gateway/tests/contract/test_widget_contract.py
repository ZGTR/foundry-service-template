"""Every wire shape round-trips against its versioned JSON Schema. This is
what "contracts/ ... CI-validated" (plan section 5) means concretely: a
schema that nothing checks is documentation, not a contract.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest

from foundry_data_gateway.domain import Widget
from foundry_data_gateway.transport.http.schemas import WidgetResponse
from foundry_data_gateway.transport.http.views import WidgetView

SCHEMA_PATH = Path(__file__).parent.parent.parent / "contracts/widget.v1.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())


def test_schema_file_is_valid_json_schema_2020_12() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_a_real_widget_response_conforms_to_the_schema() -> None:
    widget = Widget(
        widget_id="widget_1",
        org_id="org_1",
        name="Sprocket",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # THROUGH THE VIEW, not a schema-side constructor. §2 puts response shaping
    # in the presenter, so the thing this contract test must validate is what the
    # VIEW emits -- a schema that can build itself from a domain object would let
    # the view drift from the wire contract with nothing going red.
    payload = json.loads(WidgetResponse(**WidgetView.one(widget)).model_dump_json())

    jsonschema.validate(instance=payload, schema=SCHEMA)


def test_a_payload_missing_a_required_field_is_rejected() -> None:
    payload = {"widget_id": "widget_1", "org_id": "org_1", "name": "Sprocket"}  # no created_at

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=SCHEMA)


def test_an_unknown_field_is_rejected() -> None:
    payload = {
        "widget_id": "widget_1",
        "org_id": "org_1",
        "name": "Sprocket",
        "created_at": "2026-01-01T00:00:00+00:00",
        "unexpected_field": "should not be here",
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=SCHEMA)
