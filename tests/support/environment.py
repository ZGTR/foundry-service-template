"""Build a seeded app for `tests/seeded` and `tests/e2e`.

This is the one file a new service rewrites. `seeded_case.py` is copied verbatim
across the estate, `db_seeder.py` already loads the fixture format, and
everything that knows what THIS service is lives here.

It goes through `build_container` + `create_app` rather than assembling the
stack itself, and that distinction is the whole point of the seeded tier: a test
that builds its own service proves the TEST's wiring works, while a test that
goes through the composition root proves the SERVICE's wiring works -- which is
where the bugs that reach production actually are. Measured in this estate: a
rollout that mis-wired a router's auth token in `app_factory` turned 42 of 45
seeded tests red, and a suite assembling its own app would not have noticed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from foundry_contracts.clock import FrozenClock
from foundry_testkit.db_seeder import JsonFixtureSeeder, load_seed_blocks
from foundry_testkit.seeded_case import SeededEnvironment, SeedProfile

from foundry_service_template.adapters.fake import InMemoryWidgetRepository, RecordingPublisher
from foundry_service_template.composition.app_factory import create_app
from foundry_service_template.composition.config import ServiceConfig
from foundry_service_template.composition.container import build_container
from foundry_service_template.domain import Widget
from tests.support.store_rows import FakeWidgetRows, SqlWidgetRows

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"

#: Pinned so a seeded corpus produces the same timestamps on every run. A clock
#: that moves makes `created_at` unassertable and turns any ordering test into a
#: flake that only fails near midnight.
FROZEN_NOW = datetime(2026, 1, 1, tzinfo=UTC)

#: The FAKE arm's database URL, and it is deliberately unreachable. Under `fake`
#: the repository is injected, so `build_persistence` is never called and nothing
#: opens a connection -- but `ServiceConfig` requires the field, and a plausible
#: URL here would mean a fake arm that accidentally reached persistence would find
#: a real database and pass. Port 1 is what makes that failure loud. This is the
#: same constant four other services spell `UNUSED_DATABASE_URL`; it is a module
#: constant, never an environment variable.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://seeded:seeded@127.0.0.1:1/seeded"


def build_environment(
    *,
    sources: Sequence[str],
    profile: SeedProfile = "fake",
    repository: object | None = None,
    database_engine: object | None = None,
) -> SeededEnvironment:
    """Build the seeded app for one arm.

    THE TWO ARMS DIFFER IN EXACTLY ONE THING: whether persistence is a real
    PostgreSQL or the in-memory fake. Everything else -- the corpus, the seeder,
    the frozen clock, the recording publisher, `build_container`, `create_app` --
    is shared, because a real arm that also swapped the clock or the publisher
    would be measuring a different service and any disagreement between the arms
    would be unattributable.

    THE PUBLISHER IS THE FAKE UNDER BOTH PROFILES, and that is the contract rather
    than a shortcut: "real" here means real INFRASTRUCTURE, not real peer
    services. A cross-service collaborator is a port, and peer-service reality
    belongs to the `-e2e` repositories.
    """
    if profile not in ("fake", "real"):
        raise ValueError(f"unknown seed profile {profile!r}; expected 'fake' or 'real'")

    publisher = RecordingPublisher()
    if profile == "real":
        if database_engine is None:
            # REFUSED, never defaulted. Falling back to `build_persistence`
            # building its own engine would give every test its own connection
            # pool -- the exact defect the engine seam exists to close -- and the
            # suite would still be green until `max_connections` ran out
            # somewhere in the middle of an unrelated test.
            raise ValueError(
                "the 'real' profile needs the worker's engine: pass "
                "database_engine=<Engine>. The kit's `worker_engine` fixture owns "
                "exactly one per xdist worker; see foundry_testkit.real_profile."
            )
        container = build_container(
            _config(str(database_engine.url)),
            clock=FrozenClock(FROZEN_NOW),
            publisher=publisher,
            database_engine=database_engine,
        )
    else:
        container = build_container(
            _config(UNREACHABLE_DATABASE_URL),
            clock=FrozenClock(FROZEN_NOW),
            widget_repository=repository or InMemoryWidgetRepository(),
            publisher=publisher,
        )

    seeded = _seed(container, sources)
    app = create_app(container)
    app.state.test_publisher = publisher
    return SeededEnvironment(
        client=TestClient(app, raise_server_exceptions=False),
        container=container,
        seeded=seeded,
        profile=profile,
        rows=_rows(container, database_engine, repository),
    )


def _rows(container, database_engine, repository):
    """What the store HOLDS, for a test that will not settle for the response.

    KEYED ON WHICH STORE WAS WIRED, never on the profile string. A real-profile
    test that injects a deliberately-failing repository is still backed by an
    in-memory store, and asking PostgreSQL about rows that were written into a
    dict reports an empty database -- which reads as a persistence bug in the
    service rather than as the inspector looking in the wrong place.
    """
    if repository is not None or database_engine is None:
        return FakeWidgetRows(container.widget_repository)
    return SqlWidgetRows(database_engine)


def _seed(container, sources: Sequence[str]):
    """Load each fixture through the REAL repository, via the existing seeder.

    `JsonFixtureSeeder` writes through the repository inside the real unit of
    work rather than issuing its own INSERTs, so the corpus cannot drift from
    the schema the service actually reads. See its module docstring.

    ONE SEEDER, ONE FIXTURE, BOTH ARMS. The writer is `widget_repository.add` in
    each case -- the in-memory fake under `fake`, `SqlWidgetRepository` under
    `real` -- so the corpus is literally the same bytes and any divergence between
    the arms is a property of the adapter rather than of two fixture dialects.
    The COMMIT is the only asymmetry: the SQL repository flushes inside the unit
    of work and leaves the commit decision to its caller, so the seeder is handed
    one; the fake has no transaction and is handed None.
    """
    uow = getattr(container, "unit_of_work", None)
    seeder = JsonFixtureSeeder(
        writers={Widget: container.widget_repository.add},
        commit=None if uow is None else uow.commit,
    )
    loaded = []
    for source in sources:
        blocks = load_seed_blocks(FIXTURE_ROOT / f"{source}.json")
        seeder.seed(blocks)
        loaded.extend(blocks)
    return _SeedResult(loaded)


class _SeedResult:
    """What the corpus declared, so a test asserts against it rather than a count.

    Hardcoding `assert len(results) == 3` makes a fixture edit break a test that
    was not about the number, and -- worse -- lets a seeder that silently loaded
    nothing pass anything asserting zero.
    """

    def __init__(self, blocks) -> None:
        self._blocks = list(blocks)

    def rows(self, target: str) -> list[dict]:
        return [
            dict(row)
            for block in self._blocks
            if block.target_class == target
            for row in block.data
        ]

    def ids(self, target: str, key: str) -> list:
        return [row[key] for row in self.rows(target) if key in row]

    def count(self, target: str) -> int:
        return len(self.rows(target))


def _config(database_url: str) -> ServiceConfig:
    """The same configuration for both arms, differing only in the database.

    The URL is a PARAMETER rather than a branch inside this function, so the two
    arms cannot drift in any other field. Under `real` it is the per-worker
    database the kit cloned from the migrated template; under `fake` it is
    `UNREACHABLE_DATABASE_URL`, which nothing connects to.
    """
    return ServiceConfig(
        service_env="test",
        port=8080,
        principal_issuer="test-issuer",
        database_url=database_url,
        event_bus_mode="in_process",
        sqs_queue_url=None,
        aws_region=None,
    )
