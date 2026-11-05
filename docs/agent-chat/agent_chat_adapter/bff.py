"""AgentChat BFF: verify the product session, mint a short-lived token, proxy four routes.

The browser never holds a runtime token or the definition. Each request is checked by
the product's own `verify_session`; the BFF then signs an ES256 JWT (`sub` = product
user, `custom:org_id` = product tenant, scopes conversation:read/write) with a key the
installation trusts through `GET {prefix}/jwks.json` (its `AUTH_JWKS_URI`), adds the
compiled definition and its digest to a turn, and pipes the event stream unbuffered.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
import rfc8785
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

SCOPES = "conversation:read conversation:write"


@dataclass(frozen=True)
class ProductUser:
    user_id: str
    org_id: str


def bff_router(  # `async def verify_session(request) -> ProductUser | None`, the product's
    *, runtime_url: str, verify_session: Callable[[Request], Any], signing_key_pem: str,
    key_id: str, issuer: str, audience: str, definition: dict,
    client: httpx.AsyncClient | None = None, token_ttl_s: int = 300,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    key = load_pem_private_key(signing_key_pem.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("signing_key_pem must be a P-256 (ES256) private key")
    jwks = {"keys": [{**json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key())),
                      "kid": key_id, "alg": "ES256", "use": "sig"}]}
    digest = hashlib.sha256(rfc8785.dumps(definition)).hexdigest()
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
    router = APIRouter()

    def mint(user: ProductUser) -> str:
        issued = int(now())
        claims = {"iss": issuer, "aud": audience, "sub": user.user_id, "custom:org_id": user.org_id,
                  "scope": SCOPES, "iat": issued, "exp": issued + token_ttl_s}
        return jwt.encode(claims, key, algorithm="ES256", headers={"kid": key_id})

    async def proxy(request: Request, path: str, body: bytes | None = None) -> Response:
        user = await verify_session(request)
        if not isinstance(user, ProductUser):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        headers = {"authorization": f"Bearer {mint(user)}", "content-type": "application/json",
                   "accept": request.headers.get("accept", "*/*")}
        if "idempotency-key" in request.headers:
            headers["idempotency-key"] = request.headers["idempotency-key"]
        upstream = await http.send(http.build_request(
            request.method, f"{runtime_url}/v1{path}", params=request.query_params,
            headers=headers, content=body), stream=True)
        return StreamingResponse(
            upstream.aiter_raw(), status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
            headers={"cache-control": "no-store", "x-accel-buffering": "no"},
            background=BackgroundTask(upstream.aclose))

    router.add_api_route("/jwks.json", lambda: jwks, methods=["GET"])  # the AUTH_JWKS_URI

    @router.post("/conversations/{cid}/turns:stream")
    async def turn(cid: str, request: Request) -> Response:
        try:  # only the user's text is taken from the browser
            text = str(json.loads(await request.body()).get("text", ""))
        except (ValueError, AttributeError):
            text = ""
        body = json.dumps({"text": text, "definition": definition, "definition_digest": digest})
        return await proxy(request, f"/conversations/{cid}/turns:stream", body.encode())

    @router.get("/conversations/{cid}/turns/{tid}/events")
    async def events(cid: str, tid: str, request: Request) -> Response:
        return await proxy(request, f"/conversations/{cid}/turns/{tid}/events")

    @router.api_route("/conversations/{cid}", methods=["GET", "DELETE"])
    async def conversation(cid: str, request: Request) -> Response:
        return await proxy(request, f"/conversations/{cid}")

    return router
