"""The reference console: a backend-for-frontend and one page, for operators and demos.

What every product's own backend does to put a governed data agent in front of a user,
in ~100 lines, so Shepherd's console and Alphazed's app can copy the pattern:

1. ask the gateway which tools this tenant may use      GET  /v1/tools
2. ask the gateway for the agent built on those tools   GET  /v1/agent-definition
3. send the turn to foundry-agent with that definition   POST /v1/conversations/{id}/turns:stream
4. show the receipts and usage the gateway recorded      GET  /v1/receipts, /v1/usage

Tokens: each call carries the persona's own JWT (audience `data-gateway` or `agent-chat`),
read from the local workload IdP's token directory. A production BFF holds the signed-in
user's token instead; nothing else changes.

    GATEWAY_URL=... AGENT_URL=... TOKENS_DIR=... PERSONAS_FILE=... \
      uvicorn console.bff:app --port 18300
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:18110")
AGENT = os.environ.get("AGENT_URL", "http://127.0.0.1:18183")
TOKENS = Path(os.environ.get("TOKENS_DIR", ".local/tokens"))
PERSONAS = json.loads(
    Path(os.environ.get("PERSONAS_FILE", "e2e/fixtures/personas.json")).read_text()
)["personas"]
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="foundry-data-gateway console")
http = httpx.Client(timeout=60)


class ChatTurn(BaseModel):
    text: str
    conversation_id: str | None = None


def persona(pid: str) -> dict:
    for p in PERSONAS:
        if p["id"] == pid:
            return p
    raise HTTPException(404, f"unknown persona {pid}")


def bearer(p: dict, audience: str) -> dict[str, str]:
    token = (TOKENS / p["tenant"] / p["sub"] / f"{audience}.jwt").read_text().strip()
    return {"Authorization": f"Bearer {token}"}


def gateway(p: dict, path: str) -> dict:
    response = http.get(f"{GATEWAY}{path}", headers=bearer(p, "data-gateway"))
    response.raise_for_status()
    return response.json()


def sse_events(raw: str) -> list[dict]:
    events = []
    for block in raw.split("\n\n"):
        data = [line[6:] for line in block.split("\n") if line.startswith("data: ")]
        if data:
            events.append(json.loads(data[0]))
    return events


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/personas")
def personas() -> dict:
    return {"personas": PERSONAS}


@app.get("/api/{pid}/tools")
def tools(pid: str) -> dict:
    return gateway(persona(pid), "/v1/tools")


@app.get("/api/{pid}/audit")
def audit(pid: str) -> dict:
    p = persona(pid)
    return {
        "receipts": gateway(p, "/v1/receipts")["receipts"],
        "usage": gateway(p, "/v1/usage")["usage"],
    }


@app.post("/api/{pid}/chat")
def chat(pid: str, turn: ChatTurn) -> dict:
    p = persona(pid)
    agent = gateway(p, "/v1/agent-definition")
    cid = turn.conversation_id or f"c-{uuid.uuid4().hex[:20]}"
    response = http.post(
        f"{AGENT}/v1/conversations/{cid}/turns:stream",
        headers={**bearer(p, "agent-chat"), "Idempotency-Key": uuid.uuid4().hex},
        json={"text": turn.text, **agent},
    )
    if response.status_code != 200:
        raise HTTPException(response.status_code, response.text[:500])
    events = sse_events(response.text)
    return {
        "conversation_id": cid,
        "reply": "".join(e.get("text", "") for e in events if e["type"] == "text.delta"),
        "cards": [e["artifact"] for e in events if e["type"] == "artifact"],
        "errors": [e for e in events if e["type"] == "error"],
        "event_types": [e["type"] for e in events],
        "definition_digest": agent["definition_digest"],
    }
