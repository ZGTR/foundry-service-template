"""The AgentChat tool-webhook template against a REAL PostgreSQL.

Every refusal the runtime's signed webhook contract names, and the atomicity rule:
the product write and its outbox fact commit in ONE transaction, and a fact that
could not be staged (`stage_fact` returns None) leaves no row anywhere and no
display data in the answer.
"""

from __future__ import annotations

import json

import pytest
from agent_chat_adapter.webhook import tool_webhook_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.agent_chat_product import SECRET, Product, counts, sign

NOW = 1_800_000_000


def call(tool_call_id: str = "call-1", epoch: int = 1, conversation_id: str = "conv-1") -> bytes:
    return json.dumps({
        "conversation_id": conversation_id, "turn_id": "turn-1", "tool_call_id": tool_call_id,
        "epoch": epoch, "name": "record_note", "input": {"topic": "t", "detail": "d"},
    }).encode()


def client(engine, product: Product) -> TestClient:
    app = FastAPI()
    app.include_router(tool_webhook_router(
        secret=lambda: SECRET, begin=engine.begin, write=product.write,
        stage_fact=product.stage_fact, now=lambda: NOW,
    ))
    return TestClient(app)


def post(c: TestClient, body: bytes, stamp: str = str(NOW), signature: str | None = None,
         key: str = "call-1"):
    return c.post("/agent-chat/tools", content=body, headers={
        "content-type": "application/json", "x-foundry-timestamp": stamp,
        "x-foundry-signature": signature if signature is not None else sign(body, stamp),
        "idempotency-key": key,
    })


def test_a_signed_call_commits_the_write_and_its_fact_and_answers_display_data(engine):
    product = Product()
    response = post(client(engine, product), call())
    assert response.status_code == 200
    assert response.json() == {"state_patch": {"notes": {"t": "d"}},
                               "artifact": {"id": "call-1", "kind": "note", "title": "t"}}
    assert counts(engine) == (1, 1, 1)


def test_the_conversation_owner_reaches_the_write_and_an_older_runtime_sends_none(engine):
    product = Product()
    body = json.loads(call())
    body["principal"] = {"org_id": "org-1", "sub": "user-1"}
    raw = json.dumps(body).encode()
    assert post(client(engine, product), raw).status_code == 200
    assert post(client(engine, product), call("call-2"), key="call-2").status_code == 200
    assert product.principals == [{"org_id": "org-1", "sub": "user-1"}, None]


def test_fact_staging_returning_none_is_non_2xx_with_zero_rows_and_no_artifact(engine):
    product = Product(stage=False)
    response = post(client(engine, product), call())
    assert response.status_code == 503
    assert "artifact" not in response.json() and "state_patch" not in response.json()
    assert product.writes == 1, "the write ran, so zero rows proves it was rolled back"
    assert counts(engine) == (0, 0, 0)


def test_a_duplicate_tool_call_id_commits_once_and_answers_the_same_display(engine):
    product = Product()
    c = client(engine, product)
    first, second = post(c, call()), post(c, call())
    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()
    assert product.writes == 1
    assert counts(engine) == (1, 1, 1)


def test_an_epoch_lower_than_one_seen_for_the_conversation_is_refused(engine):
    product = Product()
    c = client(engine, product)
    assert post(c, call("call-new", epoch=2), key="call-new").status_code == 200
    response = post(c, call("call-old", epoch=1), key="call-old")
    assert response.status_code == 409
    assert product.writes == 1
    assert counts(engine) == (1, 1, 1)


def test_the_same_epoch_on_another_conversation_is_not_stale(engine):
    c = client(engine, Product())
    assert post(c, call("a", epoch=5, conversation_id="c-a"), key="a").status_code == 200
    assert post(c, call("b", epoch=1, conversation_id="c-b"), key="b").status_code == 200


@pytest.mark.parametrize("signature", ["", "sha256=" + "0" * 64, sign(call(), str(NOW), "other")])
def test_a_bad_or_missing_signature_is_refused_before_any_write(engine, signature):
    product = Product()
    assert post(client(engine, product), call(), signature=signature).status_code == 401
    assert product.writes == 0 and counts(engine) == (0, 0, 0)


def test_a_signature_over_a_different_body_is_refused(engine):
    product = Product()
    response = post(client(engine, product), call(epoch=9), signature=sign(call(), str(NOW)))
    assert response.status_code == 401 and product.writes == 0


@pytest.mark.parametrize("stamp", [str(NOW - 301), str(NOW + 301), "not-a-number", ""])
def test_a_stale_future_or_unreadable_timestamp_is_refused(engine, stamp):
    product = Product()
    assert post(client(engine, product), call(), stamp=stamp).status_code == 401
    assert product.writes == 0 and counts(engine) == (0, 0, 0)


def test_a_timestamp_inside_the_window_is_accepted(engine):
    assert post(client(engine, Product()), call(), stamp=str(NOW - 299)).status_code == 200


@pytest.mark.parametrize("body", [b"not json", b"[]", json.dumps({"tool_call_id": "x"}).encode()])
def test_a_signed_but_malformed_call_is_refused(engine, body):
    product = Product()
    assert post(client(engine, product), body).status_code == 400
    assert product.writes == 0


def test_an_idempotency_key_that_is_not_the_tool_call_id_is_refused(engine):
    product = Product()
    assert post(client(engine, product), call(), key="other").status_code == 400
    assert product.writes == 0


def test_a_write_that_raises_rolls_back_and_is_non_2xx(engine):
    class Failing(Product):
        def stage_fact(self, tx, tool_call):
            super().stage_fact(tx, tool_call)
            raise RuntimeError("outbox down")

    c = TestClient(client(engine, Failing()).app, raise_server_exceptions=False)
    assert post(c, call()).status_code == 500
    assert counts(engine) == (0, 0, 0)
