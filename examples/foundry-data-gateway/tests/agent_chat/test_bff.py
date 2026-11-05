"""The AgentChat BFF template: product session -> short-lived ES256 JWT -> the four routes.

The runtime here is an in-process FAKE that records what reached it and verifies the
minted token against the JWKS the BFF publishes, exactly as an installation configured
with `AUTH_JWKS_URI` would. The real runtime is driven in `test_against_real_runtime.py`.
"""

from __future__ import annotations

import hashlib
import json

import httpx
import jwt
import pytest
import rfc8785
from agent_chat_adapter.bff import ProductUser, bff_router
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

NOW = 1_700_000_000
DEFINITION = {"schema_version": "example/1", "agent": {"agent_id": "helper"}, "tools": []}
DIGEST = hashlib.sha256(rfc8785.dumps(DEFINITION)).hexdigest()
SSE = b'id: 1\nevent: turn.started\ndata: {"type":"turn.started"}\n\n'


def private_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


class FakeRuntime:
    def __init__(self) -> None:
        self.seen: list[dict] = []
        self.app = FastAPI()
        self.app.add_api_route("/{path:path}", self.handle, methods=["GET", "POST", "DELETE"])

    async def handle(self, path: str, request: Request) -> Response:
        self.seen.append({"method": request.method, "path": "/" + path, "query": request.url.query,
                          "headers": dict(request.headers), "body": await request.body()})
        if path.endswith("turns:stream") or path.endswith("/events"):
            return StreamingResponse(iter([SSE]), media_type="text/event-stream")
        if request.method == "DELETE":
            return Response(status_code=204)
        if path == "v1/conversations/busy":
            return JSONResponse({"error": "busy", "turn_id": "t9"}, status_code=409)
        return JSONResponse({"conversation_id": path.rsplit("/", 1)[1], "messages": []})


@pytest.fixture
def stack():
    runtime = FakeRuntime()
    users = {"good": ProductUser(user_id="user-1", org_id="org-1")}

    async def verify_session(request: Request) -> ProductUser | None:
        return users.get(request.cookies.get("session", ""))

    app = FastAPI()
    app.include_router(bff_router(
        runtime_url="http://runtime.invalid", verify_session=verify_session,
        signing_key_pem=private_pem(), key_id="k1", issuer="https://product.example.com",
        audience="agent-chat", definition=DEFINITION, now=lambda: NOW,
        client=httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app)),
    ), prefix="/api/agent-chat")
    return TestClient(app), runtime


def claims(client: TestClient, header: str) -> dict:
    scheme, token = header.split(" ", 1)
    assert scheme == "Bearer"
    key = jwt.PyJWKSet.from_dict(client.get("/api/agent-chat/jwks.json").json())["k1"].key
    return jwt.decode(token, key, algorithms=["ES256"], audience="agent-chat",
                      issuer="https://product.example.com", options={"verify_exp": False})


ROUTES = [
    ("POST", "/conversations/c1/turns:stream"),
    ("GET", "/conversations/c1/turns/t1/events?after=3"),
    ("GET", "/conversations/c1"),
    ("DELETE", "/conversations/c1"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("cookie", [None, "forged"])
def test_no_verified_session_is_401_and_nothing_reaches_the_runtime(stack, method, path, cookie):
    client, runtime = stack
    client.cookies.clear()
    if cookie:
        client.cookies.set("session", cookie)
    response = client.request(method, "/api/agent-chat" + path, json={"text": "hi"})
    assert response.status_code == 401
    assert runtime.seen == []


def test_a_turn_carries_a_minted_token_the_definition_and_its_digest(stack):
    client, runtime = stack
    client.cookies.set("session", "good")
    response = client.post(
        "/api/agent-chat/conversations/c1/turns:stream",
        headers={"idempotency-key": "k-1", "authorization": "Bearer product-cookie-token"},
        json={"text": "hello", "definition": {"evil": True}, "definition_digest": "0" * 64},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.content == SSE
    (seen,) = runtime.seen
    assert (seen["method"], seen["path"]) == ("POST", "/v1/conversations/c1/turns:stream")
    assert seen["headers"]["idempotency-key"] == "k-1"
    assert "cookie" not in seen["headers"]
    assert json.loads(seen["body"]) == {"text": "hello", "definition": DEFINITION,
                                        "definition_digest": DIGEST}
    token = claims(client, seen["headers"]["authorization"])
    assert token["sub"] == "user-1" and token["custom:org_id"] == "org-1"
    assert token["scope"].split() == ["conversation:read", "conversation:write"]
    assert token["iat"] == NOW and token["exp"] == NOW + 300


def test_the_token_header_names_es256_and_the_published_key(stack):
    client, runtime = stack
    client.cookies.set("session", "good")
    client.get("/api/agent-chat/conversations/c1")
    header = jwt.get_unverified_header(runtime.seen[0]["headers"]["authorization"].split(" ", 1)[1])
    assert (header["alg"], header["kid"]) == ("ES256", "k1")
    (jwk,) = client.get("/api/agent-chat/jwks.json").json()["keys"]
    assert "d" not in jwk, "the private half must never be published"


@pytest.mark.parametrize(("method", "path"), ROUTES[1:])
def test_the_other_three_routes_are_proxied_unchanged(stack, method, path):
    client, runtime = stack
    client.cookies.set("session", "good")
    response = client.request(method, "/api/agent-chat" + path)
    assert response.status_code == (204 if method == "DELETE" else 200)
    (seen,) = runtime.seen
    target, _, query = ("/v1" + path).partition("?")
    assert (seen["method"], seen["path"], seen["query"]) == (method, target, query)
    assert claims(client, seen["headers"]["authorization"])["sub"] == "user-1"


def test_a_runtime_refusal_keeps_its_status_and_body(stack):
    client, _ = stack
    client.cookies.set("session", "good")
    response = client.get("/api/agent-chat/conversations/busy")
    assert response.status_code == 409
    assert response.json() == {"error": "busy", "turn_id": "t9"}


def test_a_non_ec_signing_key_is_refused_at_construction():
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    with pytest.raises(ValueError, match="P-256"):
        bff_router(runtime_url="http://x.invalid", verify_session=lambda r: None,
                   signing_key_pem=pem, key_id="k", issuer="i", audience="a", definition={})
