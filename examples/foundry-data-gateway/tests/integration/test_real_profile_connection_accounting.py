"""A4's named controls, asserted against a REAL PostgreSQL.

Three claims live here, and none of them can be answered by a substitute.

THE TEMPLATE IS IDLE BEFORE THE CLONE. PostgreSQL refuses
`CREATE DATABASE ... TEMPLATE t` while any session is attached to `t`, so a
`migrations/env.py` that leaks a connection makes every worker's first action
fail with a message about the template rather than about the leak. The claim is a
fact about `pg_stat_activity`, so it is read from `pg_stat_activity`.

A RUN LEAVES THE SERVER WHERE IT FOUND IT. One engine per worker is the whole
reason the kit has an engine seam. Without it, each seeded test builds a
container, `build_persistence` builds an engine, the engine builds a pool, and a
suite exhausts `max_connections=100` partway through -- reported as a connection
error attributed to whichever test was unlucky, several tests after the one that
caused it. The arm below measures the count across a loop of container builds and
also demonstrates the LEAK, because a conservation assertion that has never been
shown to fail is indistinguishable from one measuring a constant.

TWO CHECKOUTS DO NOT SHARE CONTAINERS. Read through Docker's own project label,
which is the medium `docker compose` resolves containers by -- not through the
string this process computed, which would be comparing a function to itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from foundry_testkit.real_profile import (
    TEMPLATE_DATABASE,
    RealProfile,
    compose_project_name,
    load_declaration,
    owned_tables,
    session_count,
    truncate,
    worker_database_name,
)

from tests.support.environment import build_environment

ROOT = Path(__file__).resolve().parents[2]

CORPUS = ["widgets"]

#: How many containers to build and drop. Small enough to stay fast, large enough
#: that a one-connection-per-container leak is unmistakable against the handful of
#: connections a healthy run holds. HAND-WRITTEN: deriving it from an observed
#: count would make the comparison agree with itself.
BUILD_ROUNDS = 12


def _admin_url(profile: RealProfile) -> str:
    if profile.admin_url is None:  # pragma: no cover - real_postgres skips first
        pytest.skip("real arm skipped: no 'postgres' resource declared")
    return profile.admin_url


def test_the_migrated_template_has_no_sessions_left_on_it(real_profile, real_postgres) -> None:
    """THE NAMED CONTROL. `alembic upgrade head` ran as a subprocess; it is gone.

    `real_postgres` is requested so this runs AFTER a clone has actually
    succeeded: asserting that the template is idle in a session where no clone was
    attempted would be true of a server nobody had migrated either.
    """
    admin = _admin_url(real_profile)

    assert session_count(admin, TEMPLATE_DATABASE) == 0, (
        "sessions are still attached to the migration template. The leak is in "
        "migrations/env.py: it must dispose its engine in a `finally`."
    )


def test_session_count_can_report_a_nonzero(real_profile, real_postgres, worker_engine) -> None:
    """THE POSITIVE CONTROL for the arm above.

    A `session_count` that returned 0 for every database -- a typo in the column
    name, a `datname` that never matches -- satisfies the template assertion for
    free and would report a leaking template as idle. So the same function is
    pointed at a database this session is demonstrably connected to.

    The connection is FORCED rather than assumed: `create_engine` is lazy, so a
    freshly built engine has no connection and the control would read 0 and be
    blamed on the query.
    """
    admin = _admin_url(real_profile)
    from sqlalchemy import text

    with worker_engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        live = session_count(admin, worker_database_name_of(real_postgres))

    assert live > 0, "session_count answers 0 for a database this test is connected to"


def worker_database_name_of(real_postgres) -> str:
    from sqlalchemy.engine import make_url

    return make_url(real_postgres.url).database or ""


def test_building_a_container_per_test_returns_every_connection_it_took(
    real_profile, real_postgres, worker_engine
) -> None:
    """THE CONSERVATION ARM: `pg_stat_activity` equal before and after.

    This is the seeded fixture's own loop, run `BUILD_ROUNDS` times: truncate,
    build the container through `build_environment` with the worker's ONE engine,
    seed it, close the client and the unit of work. If any of that keeps a
    connection, the count rises monotonically and the real suite dies at
    `max_connections`.

    THE TRUNCATE IS PART OF THE LOOP, not scaffolding around it. Without it the
    second round re-seeds `widget_seed_0001` into a table that still holds it and
    dies on `pk_widgets` -- which is what happened when this arm was first written,
    and it is the fixture's own contract stated out loud: the corpus is seeded
    against an EMPTY database every time.
    """
    admin = _admin_url(real_profile)
    database = worker_database_name_of(real_postgres)
    tables = owned_tables(real_profile.orm_metadata_ref or "")

    before = session_count(admin, database)
    for _ in range(BUILD_ROUNDS):
        truncate(worker_engine, tables)
        environment = build_environment(
            sources=CORPUS, profile="real", database_engine=worker_engine
        )
        environment.client.close()
        environment.container.unit_of_work.close()
    after = session_count(admin, database)

    assert after == before, (
        f"{after - before} connection(s) leaked across {BUILD_ROUNDS} container builds "
        f"({before} -> {after}). At this rate a suite of 128 tests would hold "
        f"{(after - before) * 128 // BUILD_ROUNDS} connections against max_connections=100."
    )


def test_an_engine_per_container_is_what_the_seam_prevents(
    real_profile, real_postgres, worker_engine
) -> None:
    """THE NEGATIVE CONTROL, and it is the half that makes the arm above mean something.

    A conservation assertion that has never been shown to fail cannot be told
    apart from one measuring a constant -- a `session_count` reading the wrong
    database would satisfy it forever. So this builds the container the way a
    caller WITHOUT the engine seam would, by letting `build_persistence` build its
    own engine from the URL, and requires the count to RISE.

    It goes through the same `build_container` as production: the only difference
    is the absent `database_engine=`, which is exactly the difference the seam
    exists to make. The engine is disposed at the end, so this arm leaves the
    server as it found it even though its subject is a leak.
    """
    admin = _admin_url(real_profile)
    database = worker_database_name_of(real_postgres)

    from foundry_data_gateway.composition.container import build_container
    from tests.support.environment import _config

    before = session_count(admin, database)
    containers = []
    try:
        for _ in range(BUILD_ROUNDS):
            container = build_container(_config(real_postgres.url))
            # Force the pool to open a connection. `create_engine` is lazy, so a
            # container that is built and never used holds nothing -- and an arm
            # that skipped this step would measure zero growth and be read as
            # proof that the seam is unnecessary.
            container.widget_repository.list_for_org("org_seed")
            containers.append(container)
        during = session_count(admin, database)
    finally:
        for container in containers:
            container.unit_of_work.close()

    assert during > before, (
        "building a container per test WITHOUT the engine seam took no extra connection, "
        "so the conservation arm above is not measuring what it claims. Either "
        "`build_persistence` stopped building its own engine, or `session_count` is "
        "reading the wrong database."
    )


def test_this_checkouts_containers_carry_this_checkouts_project_label(
    real_profile, real_postgres
) -> None:
    """TWO PARALLEL CHECKOUTS MUST NOT SHARE CONTAINERS.

    Read through DOCKER's label rather than through the string this process
    computed: comparing `compose_project_name(ROOT)` to `real_profile.project`
    would be comparing one function call to another, and would hold even if no
    container carried that project at all.

    The second half is the negative control -- a sibling checkout path derives a
    DIFFERENT name, so the running container is not one it would adopt. That is
    the shared-database failure stated as an assertion: there, every worktree shared one
    container name, so a sibling's rebuild killed your database tests mid-suite.
    """
    listed = subprocess.run(
        ["docker", "ps", "--filter", f"label=com.docker.compose.project={real_profile.project}",
         "--format", "{{json .}}"],
        capture_output=True, text=True, timeout=120,
    )
    assert listed.returncode == 0, listed.stderr
    names = [json.loads(line)["Names"] for line in listed.stdout.splitlines() if line.strip()]

    assert names, (
        f"no running container carries the project label {real_profile.project!r}, so either "
        f"the bring-up adopted something else or the name this process computed is not the "
        f"one compose used"
    )
    assert all(name.startswith(f"{real_profile.project}-") for name in names), names

    # The SERVICE name is held constant and the PATH is varied, because the path is
    # the whole subject: two checkouts of one repository share a `[project] name`
    # and must not share a project. Passed explicitly because the sibling directory
    # does not exist, and `load_declaration` correctly refuses a path with no
    # pyproject rather than inventing a default.
    sibling = compose_project_name(
        ROOT.parent / f"{ROOT.name}-sibling", load_declaration(ROOT).service
    )
    assert sibling != real_profile.project
    assert not any(name.startswith(f"{sibling}-") for name in names), (
        "a sibling checkout's project name matches a container this checkout started; "
        "one `down -v` would destroy the other's database mid-run"
    )


def test_the_worker_database_is_the_one_the_engine_is_bound_to(
    real_postgres, worker_engine, worker_id
) -> None:
    """The clone actually happened, and it is per WORKER.

    Without this, a kit that silently fell back to the shared `service_test`
    database would pass every other arm in this file: the counts would still
    balance, the template would still be idle, and two xdist workers would be
    truncating each other's rows mid-test.
    """
    assert worker_database_name_of(real_postgres) == worker_database_name(worker_id)
    assert str(worker_engine.url.database) == worker_database_name(worker_id)
