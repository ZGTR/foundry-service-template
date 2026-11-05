"""The template as the REAL runtime's BFF and tool webhook, end to end.

Starts foundry-agent's step API (conversation routes, on PostgreSQL) with its FAKE
streaming model, configured to trust the template BFF's JWKS and to deliver record
tools to the template webhook. A `note:` turn through the BFF must commit exactly one
row and emit `state.patch` and `artifact`; a turn whose fact cannot be staged must
commit nothing and emit no artifact.

Opt-in: set FOUNDRY_AGENT to a foundry-agent checkout (repository root) with its `.venv`;
the step API runs from it with `SERVICE_MODE=steps`.
Without it this module skips by name; it is a cross-repository proof, not a unit test.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn
from agent_chat_adapter.bff import ProductUser, bff_router
from agent_chat_adapter.webhook import tool_webhook_router
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from sqlalchemy import create_engine, select, text

from tests.support.agent_chat_product import NOTES, SECRET, Product, counts

AGENT = os.environ.get("FOUNDRY_AGENT")
pytestmark = pytest.mark.skipif(
    not AGENT, reason="real-runtime proof skipped: FOUNDRY_AGENT is not set")
DEFINITION = "tests/steps/fixtures/conversation/intake-assistant.conversation.json"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, proc: subprocess.Popen | None = None) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"{url}: process exited {proc.returncode}")
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError(url)


@pytest.fixture
def runtime_database(real_profile):
    name = f"agent_chat_rt_{uuid.uuid4().hex[:12]}"
    admin = create_engine(real_profile.admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as cx:
        cx.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin.url.set(database=name, drivername="postgresql")
    yield url.render_as_string(hide_password=False)
    with admin.connect() as cx:
        cx.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def stack(engine, runtime_database, tmp_path):
    root = Path(AGENT)
    python = str(root / ".venv" / "bin" / "python")
    product = Product()
    app_port, model_port, runtime_port = free_port(), free_port(), free_port()
    issuer = f"http://127.0.0.1:{app_port}"
    definition = json.loads((root / DEFINITION).read_text())
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()

    async def verify_session(request):
        return ProductUser("user-1", "org-1") if request.cookies.get("s") else None

    app = FastAPI()
    app.include_router(bff_router(
        runtime_url=f"http://127.0.0.1:{runtime_port}",
        verify_session=verify_session,
        signing_key_pem=pem, key_id="k1", issuer=issuer, audience="agent-chat",
        definition=definition), prefix="/api/agent-chat")
    app.include_router(tool_webhook_router(
        secret=lambda: SECRET, begin=engine.begin,
        write=lambda tx, c: product.write(tx, c),
        stage_fact=lambda tx, c: product.stage_fact(tx, c)))
    config = uvicorn.Config(app, host="127.0.0.1", port=app_port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    (tmp_path / "secret").write_text(SECRET)
    env = {**os.environ, "PYTHONPATH": f"{root / 'src'}:{root}", "SERVICE_MODE": "steps",
           "SERVICE_ENV": "local",
           "RECEIPT_STORE_BACKEND": "postgres", "RECEIPT_STORE_DATABASE_URL": runtime_database,
           "AUTH_JWKS_URI": f"{issuer}/api/agent-chat/jwks.json", "AUTH_AUDIENCE": "agent-chat",
           "PRINCIPAL_ISSUER": issuer, "MODEL_API_BASE_URL": f"http://127.0.0.1:{model_port}/v1",
           "MODEL_PRICES_JSON": json.dumps({"example-chat-model": {
               "input_micros_per_1k_tokens": 1000, "output_micros_per_1k_tokens": 5000}}),
           "CONVERSATION_TOOL_WEBHOOK_URL": f"http://127.0.0.1:{app_port}/agent-chat/tools",
           "CONVERSATION_TOOL_WEBHOOK_SECRET_FILE": str(tmp_path / "secret")}
    logs = (tmp_path / "model.log").open("w"), (tmp_path / "runtime.log").open("w")
    subprocess.run([python, "-m", "foundry_agent_runtime.composition.migrate_receipt_store"],
                   cwd=root, env=env, check=True, capture_output=True)
    commands = (
        ["-m", "tests.steps.support.fake_chat_server", "--port", str(model_port),
         "--chunk-delay", "0.04"],
        ["-m", "uvicorn", "foundry_agent_runtime.composition.main:app", "--host", "127.0.0.1",
         "--port", str(runtime_port), "--log-level", "warning"],
    )
    procs = [subprocess.Popen([python, *argv], cwd=root, env=env, stdout=log, stderr=log)
             for argv, log in zip(commands, logs, strict=True)]
    try:
        wait_for(f"{issuer}/api/agent-chat/jwks.json")
        wait_for(f"http://127.0.0.1:{model_port}/calls", procs[0])
        wait_for(f"http://127.0.0.1:{runtime_port}/health/live", procs[1])
        yield issuer, product, root
    finally:
        for proc in procs:
            proc.terminate()
            proc.wait(timeout=10)
        server.should_exit = True
        for log in logs:
            log.close()


def turn(base: str, cid: str, said: str) -> list[tuple[float, dict]]:
    """POST a turn through the BFF; (arrival time, event) for every SSE frame."""
    arrived: list[tuple[float, dict]] = []
    url = f"{base}/api/agent-chat/conversations/{cid}/turns:stream"
    with httpx.stream("POST", url, json={"text": said}, cookies={"s": "1"}, timeout=30,
                      headers={"idempotency-key": uuid.uuid4().hex}) as response:
        assert response.status_code == 200, response.read()
        for line in response.iter_lines():
            if line.startswith("data: "):
                arrived.append((time.monotonic(), json.loads(line[len("data: "):])))
    return arrived


def test_a_note_turn_commits_one_row_and_streams_the_display_events(stack, engine):
    base, _, root = stack
    import jsonschema

    schema = json.loads((root / "contracts" / "agent-chat-stream.v1.json").read_text())
    arrived = turn(base, "conv-note", "note: water the plants on Friday")
    events = [event for _, event in arrived]
    for event in events:
        jsonschema.validate(event, schema)
    kinds = [event["type"] for event in events]
    assert kinds[0] == "turn.started" and kinds[-1] == "turn.done", kinds
    assert kinds.count("state.patch") == 1 and kinds.count("artifact") == 1, kinds
    assert events[-1]["stop_reason"] == "end_turn"
    (patch,) = [e["patch"] for e in events if e["type"] == "state.patch"]
    (card,) = [e["artifact"] for e in events if e["type"] == "artifact"]
    assert patch == {"notes": {"note": "water the plants on Friday"}}
    assert card["kind"] == "note" and card["title"] == "note"
    assert counts(engine) == (1, 1, 1)
    with engine.connect() as cx:
        assert cx.execute(select(NOTES.c.tool_call_id, NOTES.c.detail)).one() == (
            card["id"], "water the plants on Friday")
    assert arrived[-1][0] - arrived[0][0] > 0.1, "the stream arrived in one piece: buffered"


def test_a_fact_that_cannot_be_staged_commits_nothing_and_emits_no_artifact(stack, engine):
    base, product, _ = stack
    product.stage = False
    events = [event for _, event in turn(base, "conv-fail", "note: this must not be kept")]
    kinds = [event["type"] for event in events]
    assert "artifact" not in kinds and "state.patch" not in kinds, kinds
    assert kinds[-1] == "turn.done", kinds
    assert product.writes >= 1, "the webhook ran the write, so zero rows proves the rollback"
    assert counts(engine) == (0, 0, 0)
