"""§6: structured JSON logging with a correlation id."""

from __future__ import annotations

import io
import json
import logging

from foundry_data_gateway.runtime import (
    JsonLogFormatter,
    configure_json_logging,
    correlation_id,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)


def _emit(message: str, **extra: object) -> dict[str, object]:
    stream = io.StringIO()
    configure_json_logging("INFO", stream=stream)
    logging.getLogger("probe").info(message, extra=extra)
    return json.loads(stream.getvalue().strip())


def test_every_record_is_one_line_of_json() -> None:
    stream = io.StringIO()
    configure_json_logging("INFO", stream=stream)

    logging.getLogger("probe").info("first")
    logging.getLogger("probe").info("second")

    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["message"] for line in lines] == ["first", "second"]


def test_a_record_carries_the_bound_correlation_id() -> None:
    token = set_correlation_id("corr-abc")
    try:
        record = _emit("with id")
    finally:
        reset_correlation_id(token)

    assert record["correlation_id"] == "corr-abc"


def test_the_id_is_unbound_again_afterwards() -> None:
    token = set_correlation_id("corr-xyz")
    reset_correlation_id(token)

    assert correlation_id() == ""
    assert _emit("after reset")["correlation_id"] == ""


def test_structured_extras_reach_the_payload_as_fields_not_text() -> None:
    record = _emit("staffed", event="widget.created", org_id="org_1")

    assert record["event"] == "widget.created"
    assert record["org_id"] == "org_1"


def test_an_exception_is_carried_as_a_field_rather_than_a_second_line() -> None:
    stream = io.StringIO()
    configure_json_logging("INFO", stream=stream)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("probe").exception("failed")

    lines = stream.getvalue().strip().splitlines()
    # ONE line, not a traceback spread over many: a shipper that guesses at the
    # boundary between records indexes half a traceback as its own event.
    assert len(lines) == 1
    assert "ValueError: boom" in json.loads(lines[0])["exception"]


def test_configuring_twice_does_not_double_emit() -> None:
    """Replacing handlers, not appending.

    A leftover plain-text handler emits every record a second time, unparseable,
    and a log platform that indexes both reports double the error rate.
    """
    first = io.StringIO()
    second = io.StringIO()
    configure_json_logging("INFO", stream=first)
    configure_json_logging("INFO", stream=second)

    logging.getLogger("probe").info("once")

    assert first.getvalue() == ""
    assert len(second.getvalue().strip().splitlines()) == 1


def test_minted_ids_are_distinct() -> None:
    assert new_correlation_id() != new_correlation_id()


def test_the_formatter_is_installed_by_configure() -> None:
    configure_json_logging("INFO", stream=io.StringIO())

    assert isinstance(logging.getLogger().handlers[0].formatter, JsonLogFormatter)
