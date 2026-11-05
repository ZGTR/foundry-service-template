"""`composition/lambda_dispatch.py` (the Lambda dispatch change).

WHY THIS IS TESTED IN ISOLATION FROM `main.py`. `test_main_entrypoint.py`
imports the real `main` module, which never sets `foundry_function` -- the
template emits only an HTTP function -- so that test can never exercise this
module's mount path. A `<name>_main.py` sibling module is a per-service
artifact this template does not itself emit (comms-core/comms-email each
author their own), so the dispatched module is FAKED via `sys.modules`
injection rather than by adding a scaffolding file the emission contract
does not produce.

WHY THIS PASSES VALUES AS ARGUMENTS, NEVER `monkeypatch.setenv`. This module
takes `foundry_function`/`pass_through_path` as keyword arguments rather than
reading `os.environ` itself -- `config.py` is the one module in this package
that reads the process environment (S18; `test_layering.py`), and a version
of this file that read `os.environ` directly failed that gate on its first
run. Testing with env vars would exercise a contract this module no longer has.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from foundry_data_gateway.composition.lambda_dispatch import (
    DISPATCH_PATH,
    mount_non_http_dispatch,
)

PACKAGE = "foundry_data_gateway"
FAKE_MODULE_NAME = f"{PACKAGE}.composition.probe_main"


@pytest.fixture
def fake_main_module(monkeypatch):
    """A minimal stand-in for a real `<name>_main.py`: a module-level
    `handler(event, context) -> dict`, exactly the shape every real one in
    this estate already exports."""
    calls = []
    module = ModuleType(FAKE_MODULE_NAME)

    def handler(event, context):
        calls.append((event, context))
        return {"applied": True, "seen": event}

    module.handler = handler
    monkeypatch.setitem(sys.modules, FAKE_MODULE_NAME, module)
    return calls


def test_no_op_when_foundry_function_is_none() -> None:
    """`ServiceConfig.foundry_function` is `None` for the HTTP function and
    for every non-Lambda deploy target, so this call must be safe to make
    unconditionally from every service's main.py."""
    app = FastAPI()
    mount_non_http_dispatch(app, PACKAGE, foundry_function=None, pass_through_path=None)
    assert all(route.path != DISPATCH_PATH for route in app.routes)


def test_refuses_at_boot_when_pass_through_path_is_missing() -> None:
    """A container that boots successfully but can never receive an invocation
    is a worse failure than one that refuses to boot: this must raise before
    the process ever answers a health check, not at the first real invoke."""
    app = FastAPI()
    with pytest.raises(RuntimeError, match="AWS_LWA_PASS_THROUGH_PATH"):
        mount_non_http_dispatch(app, PACKAGE, foundry_function="probe", pass_through_path=None)


def test_refuses_at_boot_when_pass_through_path_does_not_match() -> None:
    """A mismatched path is the same failure as an absent one: the adapter
    forwards to a path nothing answers."""
    app = FastAPI()
    with pytest.raises(RuntimeError, match="only serves"):
        mount_non_http_dispatch(
            app, PACKAGE, foundry_function="probe", pass_through_path="/wrong/path"
        )


def test_dispatches_the_raw_event_to_the_named_functions_handler(fake_main_module) -> None:
    """The end-to-end shape an earlier change's RIE proof measured: a POST to the
    pass-through path reaches the named function's unmodified `handler`, which
    is the real, testable substitute for "does not answer with a null body"."""
    app = FastAPI()
    mount_non_http_dispatch(
        app, PACKAGE, foundry_function="probe", pass_through_path=DISPATCH_PATH
    )

    client = TestClient(app)
    response = client.post(DISPATCH_PATH, json={"hello": "event"})

    assert response.status_code == 200
    assert response.json() == {"applied": True, "seen": {"hello": "event"}}
    assert fake_main_module == [({"hello": "event"}, None)]


def test_dispatches_an_empty_event_when_the_body_is_empty(fake_main_module) -> None:
    """LWA forwards a cold-start / scheduled invoke's event verbatim, and an
    empty body is a real shape it can carry -- this must not raise on it."""
    app = FastAPI()
    mount_non_http_dispatch(
        app, PACKAGE, foundry_function="probe", pass_through_path=DISPATCH_PATH
    )

    client = TestClient(app)
    response = client.post(DISPATCH_PATH, content=b"")

    assert response.status_code == 200
    assert fake_main_module == [({}, None)]
