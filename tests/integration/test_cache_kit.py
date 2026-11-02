"""The cache-fill kit, against a REAL Redis -- or a NAMED skip saying why not.

The fixture shape mirrors the DB seeder's: one declared JSON document, loaded
into a real resource. `fill_cache` deliberately does NOT fall back to an
in-memory dictionary when the server is unreachable, because a test that asked
for a cache and silently got a dict proves something about a dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_testkit import CacheUnavailable, fill_cache, load_cache_fixture

#: HAND-WRITTEN. Never `len(fill.entries)` -- that is the tautological form.
EXPECTED_ENTRIES = 2


def test_the_fixture_declares_the_documented_shape(fixture_dir: Path) -> None:
    fill = load_cache_fixture(fixture_dir / "widget_cache.json")

    assert fill.backend == "redis"
    assert len(fill.entries) == EXPECTED_ENTRIES
    assert fill.entries["widget:org_seed:widget_seed_0001"] == "Seeded alpha"


def test_a_real_redis_is_filled_and_readable(redis_cache) -> None:
    """Runs against a live server; the `redis_cache` fixture names its own skip."""
    assert redis_cache.get("widget:org_seed:widget_seed_0001") == "Seeded alpha"
    assert redis_cache.get("widget:org_seed:widget_seed_0002") == "Seeded beta"
    assert redis_cache.dbsize() == EXPECTED_ENTRIES


def test_a_memory_backed_fill_uses_the_fake_not_a_mock(fixture_dir: Path) -> None:
    """`backend: "memory"` fills the in-memory adapter, which is not a mock.

    It is the same object a local run would use. The distinction matters: a mock
    agrees with whatever you assert, an in-memory adapter has behaviour you can
    be wrong about.
    """
    import json

    fixture = fixture_dir / "widget_cache.json"
    payload = json.loads(fixture.read_text())
    payload["backend"] = "memory"
    memory_fixture = fixture.parent / "_tmp_memory_cache.json"
    memory_fixture.write_text(json.dumps(payload))
    try:
        fill = load_cache_fixture(memory_fixture)
        store: dict[str, str] = {}

        fill_cache(fill, memory=store)

        assert store["widget:org_seed:widget_seed_0001"] == "Seeded alpha"
        assert len(store) == EXPECTED_ENTRIES
    finally:
        memory_fixture.unlink()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"backend": "memcached", "entries": {"a": "b"}}, "backend must be one of"),
        ({"backend": "redis", "entries": {}}, "non-empty"),
        ({"backend": "redis"}, "non-empty"),
    ],
)
def test_a_malformed_cache_fixture_is_refused(
    payload: dict[str, object], expected: str, tmp_path: Path
) -> None:
    import json

    fixture = tmp_path / "bad_cache.json"
    fixture.write_text(json.dumps(payload))

    with pytest.raises(CacheUnavailable, match=expected):
        load_cache_fixture(fixture)


def test_an_unreachable_redis_refuses_with_a_remedy_rather_than_falling_back(
    monkeypatch: pytest.MonkeyPatch, fixture_dir: Path
) -> None:
    """The half that matters most, and the one a happy-path run never reaches.

    The port here is a TCP endpoint, not a collaborator object, so the honest
    way to make it unreachable is to point the kit at a port nothing is on --
    which is what this does. The assertion is on the REMEDY: a skip reason that
    does not say how to provide the resource is indistinguishable, in a green
    run, from a test that ran.

    IT PATCHES THE ENVIRONMENT, NOT A MODULE ATTRIBUTE, and that is the point
    rather than a detail. This read `monkeypatch.setattr(cache_kit,
    "REDIS_PORT", 1)` against a module-level constant, which made it a test of a
    constant that the real resolution path had stopped consulting -- the kit now
    starts Redis on an EPHEMERAL port and publishes it into `REDIS_PORT`, so a
    constant captured at import was stale before any test ran. Setting the
    variable exercises the same seam an operator uses and the same one the
    controller writes, so this arm also fails if `_redis_target()` ever goes
    back to reading at import time.
    """
    from foundry_testkit import cache_kit

    monkeypatch.setenv("REDIS_PORT", "1")

    fill = load_cache_fixture(fixture_dir / "widget_cache.json")
    with pytest.raises(CacheUnavailable) as refused:
        cache_kit.fill_cache(fill)

    message = str(refused.value)
    assert "redis-server" in message, "the refusal must name how to provide the resource"
    assert "Refusing to fall back" in message
