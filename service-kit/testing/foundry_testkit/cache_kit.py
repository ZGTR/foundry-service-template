"""Cache fill from a JSON fixture, mirroring the DB seeder's block shape.

    {"backend": "redis", "entries": {"widget:org_a:w1": "First"}}

The same design decision as `db_seeder.py`: one declared fixture shape, loaded
into a REAL resource. `backend: "redis"` connects to a real Redis/Valkey server;
`backend: "memory"` fills the in-memory fake, which is the adapter under
`adapters/fake/` and not a mock -- the same object production would use in a
local run.

A `redis` fixture with no reachable server raises `CacheUnavailable`, and the
fixture turns that into a NAMED skip. It does NOT quietly fall back to memory:
a test that asked for a real cache and got a dictionary proves something about
a dictionary.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foundry_testkit.resources import ResourceUnavailable, tcp_open

__all__ = ["CacheFill", "CacheUnavailable", "fill_cache", "load_cache_fixture"]

#: Where the cache is, when nobody has said otherwise.
DEFAULT_REDIS_HOST = "127.0.0.1"
DEFAULT_REDIS_PORT = 6379
_SUPPORTED = ("redis", "memory")


def _redis_target() -> tuple[str, int]:
    """Host and port, READ AT CALL TIME rather than captured at import.

    These were module-level constants, and that made them a photograph of the
    environment taken before the thing they describe existed. `real_profile`
    starts the declared Redis in `pytest_configure` and publishes the EPHEMERAL
    host port it resolved into `REDIS_PORT`; this module is imported while
    `tests/conftest.py` is collected, which is earlier. So the constant held
    6379, nothing was listening there, and the fixture reported
    `nothing answers at 127.0.0.1:6379` while a healthy container sat on a port
    the kit had already written down. Measured on this branch, with all three
    containers up and healthy.

    `aws_kit._configured_endpoint` has the same shape for the same reason.
    """
    host = os.environ.get("REDIS_HOST") or DEFAULT_REDIS_HOST
    port = os.environ.get("REDIS_PORT")
    return host, int(port) if port else DEFAULT_REDIS_PORT


class CacheUnavailable(ResourceUnavailable):
    """The fixture's declared cache backend is not reachable. Carries the remedy."""


@dataclass(frozen=True, slots=True)
class CacheFill:
    backend: str
    entries: Mapping[str, str]


def load_cache_fixture(path: Path | str) -> CacheFill:
    source = Path(path)
    if not source.is_file():
        raise CacheUnavailable(f"cache fixture not found: {source}")
    payload = json.loads(source.read_text())
    backend = payload.get("backend")
    entries = payload.get("entries")
    if backend not in _SUPPORTED:
        raise CacheUnavailable(
            f"{source}: backend must be one of {list(_SUPPORTED)}, got {backend!r}"
        )
    if not isinstance(entries, dict) or not entries:
        # Same refusal as the seeder's empty fixture: filling nothing reads as
        # a successful fill, and every assertion downstream then measures an
        # empty cache instead of the one the fixture describes.
        raise CacheUnavailable(f"{source}: 'entries' must be a non-empty object")
    return CacheFill(backend=backend, entries={str(k): str(v) for k, v in entries.items()})


def fill_cache(fill: CacheFill, *, memory: dict[str, str] | None = None) -> Any:
    """Load `fill` into a real resource and return the handle it was loaded into."""
    if fill.backend == "memory":
        target = {} if memory is None else memory
        target.update(fill.entries)
        return target
    try:
        import redis
    except ImportError as exc:
        raise CacheUnavailable(
            "the fixture declares backend='redis' but the `redis` client is not installed "
            "(`pip install redis`). Start a server with `redis-server --daemonize yes` "
            "or `docker run -p 6379:6379 redis:7-alpine`."
        ) from exc
    host, port = _redis_target()
    if not tcp_open(host, port):
        raise CacheUnavailable(
            f"the fixture declares backend='redis' but nothing answers at "
            f"{host}:{port}. The kit starts one for you when `redis` is listed in "
            "[tool.foundry.test-resources]; add it there, or start your own with "
            "`redis-server --daemonize yes` and point REDIS_HOST/REDIS_PORT at it. "
            "Refusing to fall back to an in-memory dictionary, which would prove "
            "nothing about a cache."
        )
    client = redis.Redis(host=host, port=port, decode_responses=True)
    client.flushdb()
    client.mset(dict(fill.entries))
    return client
