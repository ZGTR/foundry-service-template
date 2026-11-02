"""`main.py`, the one module that runs at import time in the container.

WHY THIS IS THE MODULE MOST WORTH A TEST AND HAD NONE. `tests/shape/test_entrypoint.py`
drives `docker/entrypoint.sh` with a STUB python, so it proves the shell picks the right
runtime and proves nothing about this file. Everything here executes at import: a
misconfigured environment, a container that cannot build, or a `run()` that binds the wrong
port all surface as a container that exits during startup, which is the most expensive
moment to discover any of them.

Imported with a controlled environment, because importing it with the ambient one would
make this test depend on whatever the developer happens to have exported.
"""

from __future__ import annotations

import importlib
import sys

import pytest

MODULE = "foundry_service_template.composition.main"


@pytest.fixture
def entrypoint(monkeypatch):
    for name in ("SERVICE_ENV", "PORT", "AWS_LAMBDA_FUNCTION_NAME", "RUNTIME_MODE"):
        monkeypatch.delenv(name, raising=False)
    # One `setenv` CALL SITE for three variables, not three. §7's mock budget
    # counts call sites, deliberately -- including the legitimate environment
    # ones -- so a variable added here must not cost a ratchet raise.
    #
    # DATABASE_URL is among them because it has no default: importing this
    # module builds the container, and a container needs to know which database
    # it is for. The URL is SQLite so nothing has to be running, and nothing in
    # this file connects -- `create_engine` is lazy, which is the property
    # `tests/unit/test_container_database.py` asserts.
    for name, value in {
        "SERVICE_ENV": "test",
        "PORT": "9137",
        "DATABASE_URL": "sqlite+pysqlite:///widgets.db",
        # Fractional on purpose: see the grace-ceiling test below.
        "SHUTDOWN_GRACE_SECONDS": "0.5",
    }.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop(MODULE, None)
    module = importlib.import_module(MODULE)
    yield module
    sys.modules.pop(MODULE, None)


def test_importing_it_builds_a_real_app_from_the_real_environment(entrypoint) -> None:
    assert entrypoint.config.port == 9137
    assert entrypoint.config.service_env == "test"
    assert entrypoint.app is not None


@pytest.fixture
def served(entrypoint, monkeypatch) -> tuple[object, dict]:
    """Call `run()` once with `uvicorn.run` recorded, and hand back its one call.

    ONE `setattr` call site for the three arms below, not one each. §7's mock budget
    counts call sites, and three copies of the same interception were three ratchet
    entries for one decision. Each arm still asserts its own keyword, so breaking the
    port, the host or the grace ceiling in `main.py` reddens exactly its own arm.
    """
    calls = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda app, **kw: calls.append((app, kw)))

    entrypoint.run()

    assert len(calls) == 1, f"run() must start the server exactly once, not {len(calls)}"
    return calls[0]


def test_run_binds_the_configured_port_not_a_hardcoded_one(entrypoint, served) -> None:
    """A `run()` that ignored config.port would serve on 8080 in every environment, and the
    health check would pass against the wrong listener."""
    app, kwargs = served
    assert app is entrypoint.app
    assert kwargs["port"] == 9137


def test_it_binds_all_interfaces_because_the_container_is_the_boundary(served) -> None:
    """0.0.0.0 is correct HERE and would be a finding in a process facing a network
    directly: the ALB reaches the task over the container's own interface, and binding
    loopback would make every health check fail with nothing in the logs."""
    _app, kwargs = served
    assert kwargs["host"] == "0.0.0.0"  # noqa: S104 -- asserted deliberately, see docstring


def test_uvicorn_waits_no_longer_than_the_grace_ceiling(served) -> None:
    """On SIGTERM uvicorn waits for open connections before the lifecycle drains.

    Without `timeout_graceful_shutdown` that first wait has no ceiling, so one stuck
    connection keeps the task alive until ECS kills it, and SHUTDOWN_GRACE_SECONDS
    bounds only the half after it. Uvicorn takes whole seconds; 0.5 must round UP to
    1, because truncating to 0 cancels every in-flight request at once.
    """
    _app, kwargs = served
    assert kwargs["timeout_graceful_shutdown"] == 1
