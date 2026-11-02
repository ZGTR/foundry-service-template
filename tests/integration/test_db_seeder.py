"""The JSON-fixture seeder, against a REAL database, through the REAL repository.

The seeder's subject is the `{"target_class": ..., "data": [...]}` SHAPE and the
writer registry -- one fixture, many writers -- not the storage engine. So the
real arm runs on a server-free SQLAlchemy engine (`tests/support/engines.py`
explains when that is and is not the right instrument), and the engine-specific
claims live in `tests/contract/test_widget_repository_port_contract.py`, which
runs the same repository against a real PostgreSQL.

Two arms, so the seeder is proved to be writer-agnostic rather than shaped
around one: every test runs against the fake AND the real SQL repository.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from foundry_testkit import (
    JsonFixtureSeeder,
    SeedBlock,
    SeedError,
    load_seed_blocks,
    resolve_target_class,
)

from foundry_service_template.adapters.fake import InMemoryWidgetRepository
from foundry_service_template.adapters.real import SqlUnitOfWork, SqlWidgetRepository
from foundry_service_template.domain import Widget
from foundry_service_template.ports import WidgetRepository
from tests.support.engines import local_sessions

OWNED = frozenset({"widgets", "outbox"})

#: HAND-WRITTEN, never `len(blocks[0].data)`. An expectation read off the
#: artifact under test reduces the assertion to `n == n` and holds for any
#: fixture, including an empty one.
EXPECTED_SEEDED_ROWS = 3
EXPECTED_ORG_SEED_ROWS = 2


@pytest.fixture(params=["fake", "real-sql"])
def seed_target(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[WidgetRepository, JsonFixtureSeeder]]:
    if request.param == "fake":
        repository: WidgetRepository = InMemoryWidgetRepository()
        yield repository, JsonFixtureSeeder(writers={Widget: repository.add})
        return
    uow = SqlUnitOfWork(local_sessions(tmp_path / "seeded.db"), owned_tables=OWNED)
    repository = SqlWidgetRepository(uow)
    try:
        # `commit` is passed in, so the seeder writes inside the SAME transaction
        # the repository uses and lands one durable batch -- the "transaction per
        # test" §7 asks for, rather than a row-at-a-time autocommit.
        yield repository, JsonFixtureSeeder(writers={Widget: repository.add}, commit=uow.commit)
    finally:
        uow.close()


def test_a_json_fixture_fills_a_real_database_through_the_real_repository(
    seed_target: tuple[WidgetRepository, JsonFixtureSeeder], fixture_dir: Path
) -> None:
    repository, seeder = seed_target
    blocks = load_seed_blocks(fixture_dir / "widgets.json")

    written = seeder.seed(blocks)

    assert sum(written.values()) == EXPECTED_SEEDED_ROWS
    listed = repository.list_for_org("org_seed")
    assert [w.name for w in listed] == ["Seeded alpha", "Seeded beta"]
    assert len(listed) == EXPECTED_ORG_SEED_ROWS


def test_the_seeded_rows_are_org_scoped_like_every_other_write(
    seed_target: tuple[WidgetRepository, JsonFixtureSeeder], fixture_dir: Path
) -> None:
    repository, seeder = seed_target
    seeder.seed_files([fixture_dir / "widgets.json"])

    assert repository.get("org_seed", "widget_seed_0003") is None
    assert repository.get("org_other", "widget_seed_0003") is not None


def test_an_iso_timestamp_becomes_an_aware_datetime(
    seed_target: tuple[WidgetRepository, JsonFixtureSeeder], fixture_dir: Path
) -> None:
    repository, seeder = seed_target
    seeder.seed_files([fixture_dir / "widgets.json"])

    seeded = repository.get("org_seed", "widget_seed_0001")

    assert seeded is not None
    assert seeded.created_at.tzinfo is not None


def test_the_block_shape_matches_the_backend_seeder_exactly(fixture_dir: Path) -> None:
    """The shape is the contract, and it is borrowed on purpose.

    `an older backend service/src/database/seeds/mysql_seeder.py` loads
    `{"target_class": "module:Class", "data": [...]}`, and
    `src/tests/factories/content_hierarchy.py` BUILDS that same shape. An estate
    with two fixture dialects has two loaders and one of them untested.
    """
    blocks = load_seed_blocks(fixture_dir / "widgets.json")

    assert [b.target_class for b in blocks] == ["foundry_service_template.domain:Widget"]
    assert resolve_target_class(blocks[0].target_class) is Widget


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "a LIST of blocks"),
        # `min_length=1` on `FixtureFile`, and it is the one refusal a pydantic model
        # does not give you for free -- `list[FixtureBlock]` accepts `[]` happily.
        ([], "would seed nothing"),
        ([{"data": []}], "missing"),
        ([{"target_class": "no_colon", "data": []}], "module:Class"),
        ([{"target_class": "m:C", "data": {}}], "list of objects"),
        # `extra="forbid"` on `FixtureBlock`. A third key changes nothing and reads as
        # configured, which is this file's whole subject one level up from the rows.
        ([{"target_class": "m:C", "data": [], "trunacte": True}], "unknown key 'trunacte'"),
    ],
)
def test_a_malformed_fixture_is_REFUSED_not_ignored(
    payload: object, expected: str, tmp_path: Path
) -> None:
    """Refusing is the whole design.

    A seeder that skips what it cannot place reports a green seed of an empty
    database, and every test that reads it then fails somewhere else entirely --
    the expensive kind of failure, because the error names the wrong file.
    """
    import json

    fixture = tmp_path / "bad.json"
    fixture.write_text(json.dumps(payload))

    with pytest.raises(SeedError, match=expected):
        load_seed_blocks(fixture)


def test_the_parsed_block_is_the_frozen_dataclass_not_the_pydantic_model(
    fixture_dir: Path,
) -> None:
    """`parse_seed_blocks` validates with `FixtureFile` and returns `SeedBlock`.

    Handing back `FixtureBlock` would be the smaller change and the worse one: its
    `data` is a `list` of mutable dicts, so a writer that mutated a row in place would
    be mutating the fixture every later block reads. This arm exists because the
    mutation that swaps the two is otherwise INVISIBLE -- every other test in this file
    only iterates the blocks, so all of them pass either way.
    """
    blocks = load_seed_blocks(fixture_dir / "widgets.json")

    assert all(isinstance(block, SeedBlock) for block in blocks)
    assert all(isinstance(block.data, tuple) for block in blocks)
    with pytest.raises((AttributeError, TypeError)):
        blocks[0].data.append({})  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        blocks[0].target_class = "other:Thing"  # type: ignore[misc]


def test_a_block_with_no_registered_writer_is_refused(fixture_dir: Path) -> None:
    seeder = JsonFixtureSeeder(writers={int: lambda _value: None})
    blocks = load_seed_blocks(fixture_dir / "widgets.json")

    with pytest.raises(SeedError, match="no writer registered"):
        seeder.seed(blocks)


def test_a_seeder_with_no_writers_is_refused_at_construction() -> None:
    with pytest.raises(SeedError, match="at least one writer"):
        JsonFixtureSeeder(writers={})


def test_seeding_zero_blocks_is_refused_rather_than_reported_as_success() -> None:
    seeder = JsonFixtureSeeder(writers={Widget: lambda _w: None})

    with pytest.raises(SeedError, match="would report success having done nothing"):
        seeder.seed([])
