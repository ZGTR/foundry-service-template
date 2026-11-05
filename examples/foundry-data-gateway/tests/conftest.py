"""Shared fixtures. Real resources where they exist, NAMED skips where they do not.

WHAT IS NOT HERE ANY MORE, and why that is the point of this slice. A3 shipped
`tests/support/postgres.py`, a service-local bring-up that started a PostgreSQL,
migrated it and handed back a URL. It described itself as a stand-in for the
kit's real profile, and it is now deleted rather than kept alongside: two
bring-up authorities for one database means two compose project names, two
`up -d` and two teardowns, with a `down -v` from either able to destroy the
other's database mid-run. The lifecycle lives in
`foundry_testkit.real_profile`, which is kit-owned and byte-identical in every
service.

`tests/support/` is importable as `tests.support` because this file's directory
is on `sys.path` under pytest's rootdir insertion; the `tests/__init__.py`-free
layout is deliberate, matching the package's existing test tree.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

#: `SERVICE_ENV` has no default, and `alembic upgrade head`
#: resolves its database through `ServiceConfig.from_environment()`. The kit's
#: real profile and the emitted-service probe run alembic as SUBPROCESSES that
#: inherit this process's environment, so the test process says what it is,
#: once, here. `setdefault`, so an explicit value from the caller still wins, and
#: `tests/unit/test_config.py` clears it before every read it asserts on.
os.environ.setdefault("SERVICE_ENV", "test")

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

#: The kit's real-resource lifecycle: controller-only bring-up, a per-worker
#: database cloned from a migrated template, one engine per worker, and
#: TRUNCATE between tests. It brings `real_profile`, `real_postgres`,
#: `worker_engine`, `worker_id` and `postgres_sessions` with it.
#:
#: Registered as a PLUGIN rather than imported as a module, because its work
#: happens in `pytest_configure`/`pytest_configure_node`/`pytest_unconfigure` --
#: hooks pytest only calls on registered plugins. An import would bring the
#: fixtures and silently lose the lifecycle, which would read as the fixtures
#: being broken.
pytest_plugins = ["foundry_testkit.real_profile"]

from foundry_testkit.aws_kit import AwsKit, AwsKitUnavailable, start_aws_kit  # noqa: E402
from foundry_testkit.resources import ResourceUnavailable, skip_or_fail  # noqa: E402
from foundry_testkit.seeded_case import active_profile  # noqa: E402


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture(scope="session")
def aws_kit() -> Iterator[AwsKit]:
    """A live S3/SQS/Secrets Manager endpoint, or a NAMED skip saying why not.

    The skip reason carries the remedy verbatim from the kit. A bare
    `pytest.mark.skip` here would be indistinguishable, in a green run, from a
    test that actually exercised AWS.
    """
    try:
        kit = start_aws_kit()
    except AwsKitUnavailable as exc:
        skip_or_fail(f"AWS kit unavailable: {exc}")
    try:
        yield kit
    finally:
        kit.close()


@pytest.fixture
def redis_cache() -> Iterator[object]:
    """A real Redis, flushed, or a NAMED skip."""
    from foundry_testkit.cache_kit import CacheUnavailable, fill_cache, load_cache_fixture

    try:
        fill = load_cache_fixture(FIXTURE_DIR / "widget_cache.json")
        client = fill_cache(fill)
    except (CacheUnavailable, ResourceUnavailable) as exc:
        skip_or_fail(f"cache kit unavailable: {exc}")
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


# ---------------------------------------------------------------------------
# Seeded-suite wiring (S13/S14)
# ---------------------------------------------------------------------------
#
# A class under `tests/seeded` or `tests/e2e` declares `seed_sources` and this
# builds the app over that corpus. A class that declares nothing is untouched,
# so `tests/unit`, `tests/shape`, `tests/contract` and `tests/integration` are
# unaffected by any of it.


@pytest.fixture(autouse=True)
def _seeded_environment(request):
    """Seed the declared corpus and inject client/container/seeded onto the test.

    Function-scoped on purpose, under BOTH profiles. Under `fake` the whole stack
    is in-memory, so rebuilding it per test costs microseconds and buys total
    isolation. Under `real` the expensive things -- the container, the migrated
    template, the worker's database and its single engine -- are all session-scoped
    in the kit, so what is rebuilt per test is the composition graph over an
    already-open pool. That is deliberate: going through `build_container` every
    time is what makes these tests measure the SERVICE's wiring rather than the
    test's.

    THE PROFILE IS RESOLVED ONCE, HERE, and passed down. Asking `SEED_PROFILE`
    again further in would let one half of a run disagree with the other.
    """
    cls = request.cls
    sources = getattr(cls, "seed_sources", None) if cls is not None else None
    if not sources:
        yield
        return

    from tests.support.environment import build_environment

    profile = cls.resolved_profile() if hasattr(cls, "resolved_profile") else active_profile()
    engine = request.getfixturevalue("worker_engine") if profile == "real" else None
    if profile == "real":
        # TRUNCATE before seeding, through the kit's own function-scoped fixture.
        # Requested rather than declared as a parameter so the `fake` arm does not
        # drag a database in: a fixture named in the signature is built for every
        # test that uses this one, which is all of them.
        request.getfixturevalue("postgres_sessions")
    built = []

    def rebuild(**overrides):
        """Re-seed the SAME corpus with a different collaborator.

        An edge case like "the repository started failing" is a property of the
        COLLABORATOR, not of the corpus, so it needs the same fixture with a
        differently-behaving port. Rebuilding is how a test states that without
        reaching for a mocking library.
        """
        environment = build_environment(
            sources=list(sources), profile=profile, database_engine=engine, **overrides
        )
        built.append(environment)
        request.instance.client = environment.client
        request.instance.container = environment.container
        request.instance.seeded = environment.seeded
        # `rows` reads the store back. Assigned unconditionally, including when
        # it is None: leaving the attribute absent would make a service with no
        # inspector fail with AttributeError at the line that uses it, which
        # reads as a broken test rather than as a facility nobody wired.
        request.instance.rows = environment.rows
        request.instance.environment = environment
        return environment

    rebuild()
    request.instance.rebuild = rebuild
    try:
        yield
    finally:
        for environment in built:
            environment.client.close()
            # The unit of work holds a thread-local Session checked out of the
            # worker's ONE pool. Without this, each test leaks a connection and
            # the suite exhausts `max_connections` partway through -- which is
            # exactly what `pg_stat_activity` before and after a full run measures.
            uow = getattr(environment.container, "unit_of_work", None)
            if uow is not None:
                uow.close()
