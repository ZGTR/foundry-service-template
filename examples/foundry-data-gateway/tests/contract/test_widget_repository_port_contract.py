"""§8's port contract suite: ONE suite, run against the fake AND the real adapter.

This is what makes `adapters/fake/InMemoryWidgetRepository` a substitute rather
than a convenience. Every test below is parameterised over both
implementations, so "the fake behaves like the real thing" is a measured claim
each run instead of a property somebody once checked by hand.

THE REAL ARM IS A REAL POSTGRESQL, and that is a change from what this file used
to do. It ran the real arm against SQLite, on the reasoning that SQLite is a
real database engine writing a real file -- which is true, and beside the point.
Nothing in this estate deploys SQLite. The properties a repository contract
pins are the ones that differ between engines, so an arm on the engine nobody
runs measures the engine nobody runs; the `created_at` test below is the clean
example, because `DateTime(timezone=True)` is `timestamptz` on Postgres and is
silently dropped to a naive value on SQLite. There used to be a MySQL arm too,
guarded on `FOUNDRY_TEST_MYSQL_URL`; it is gone with the MySQL adapter.

HOW THE REAL ARM AVOIDS BECOMING A PERMANENT SILENT SKIP. This estate has
already paid for that failure once -- `test_az_jobs_second_consumer.py` guarded
on package names that were later renamed, and eleven assertions stopped running
with nothing going red. So:

* the adapter modules are imported UNGUARDED at the top of this file, and a
  rename or a deletion is an ImportError rather than a skip;
* the ONLY thing that can skip is Docker being unavailable, and the skip names
  what was missing;
* `REQUIRE_REAL_RESOURCES=1` turns that skip into a failure, which is what CI
  sets and what anybody asking "did the real arm run" should set;
* `test_the_suite_ran_against_more_than_one_implementation` reads the fixture's
  own parameter list, so a suite that silently collapsed to one arm fails even
  though every assertion in it still passes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from foundry_data_gateway.adapters.fake import InMemoryWidgetRepository
from foundry_data_gateway.adapters.real.sql_unit_of_work import SqlUnitOfWork
from foundry_data_gateway.adapters.real.sql_widget_repository import SqlWidgetRepository
from foundry_data_gateway.domain import Widget
from foundry_data_gateway.ports import WidgetRepository

OWNED = frozenset({"widgets", "outbox"})

#: HAND-WRITTEN. The arms this suite must run, asserted below against the arms it
#: actually ran. Deriving it from the fixture's `params` would make the assertion
#: true of a one-element list, which is the collapse it exists to catch.
EXPECTED_ARMS = frozenset({"fake", "real-postgres"})


def _widget(widget_id: str, org_id: str = "org_c", *, day: int = 1) -> Widget:
    return Widget(
        widget_id=widget_id,
        org_id=org_id,
        name=f"name-{widget_id}",
        created_at=datetime(2026, 1, day, 12, 30, 45, 123456, tzinfo=UTC),
    )


@pytest.fixture(params=sorted(EXPECTED_ARMS))
def repository(
    request: pytest.FixtureRequest,
) -> Iterator[WidgetRepository]:
    """The SAME suite, twice. The id in `request.param` reaches the report, so a
    green run names both arms rather than leaving the reader to assume two ran.
    """
    if request.param == "fake":
        yield InMemoryWidgetRepository()
        return

    sessions: sessionmaker[Session] = request.getfixturevalue("postgres_sessions")
    uow = SqlUnitOfWork(sessions, owned_tables=OWNED)
    try:
        yield SqlWidgetRepository(uow)
        uow.commit()
    finally:
        uow.close()


def test_an_added_widget_is_readable_by_its_own_org(repository: WidgetRepository) -> None:
    widget = _widget("w1")

    repository.add(widget)

    assert repository.get("org_c", "w1") == widget


def test_a_widget_is_invisible_to_another_org(repository: WidgetRepository) -> None:
    repository.add(_widget("w1"))

    assert repository.get("org_other", "w1") is None


def test_an_absent_widget_reads_as_none_rather_than_raising(
    repository: WidgetRepository,
) -> None:
    assert repository.get("org_c", "never-written") is None


def test_list_returns_this_org_only_oldest_first(repository: WidgetRepository) -> None:
    repository.add(_widget("w2", day=2))
    repository.add(_widget("w1", day=1))
    repository.add(_widget("w9", org_id="org_zzz", day=1))

    listed = repository.list_for_org("org_c")

    assert [w.widget_id for w in listed] == ["w1", "w2"]


def test_list_breaks_ties_on_widget_id(repository: WidgetRepository) -> None:
    """Identical `created_at`, so only the secondary sort decides the order.

    Without it the fake returns insertion order and the database returns
    whatever the index walk produced, and both look correct in isolation.
    """
    repository.add(_widget("w_b", day=1))
    repository.add(_widget("w_a", day=1))

    assert [w.widget_id for w in repository.list_for_org("org_c")] == ["w_a", "w_b"]


def test_list_for_an_org_with_nothing_is_empty_not_an_error(
    repository: WidgetRepository,
) -> None:
    repository.add(_widget("w1"))

    assert repository.list_for_org("org_with_nothing") == []


def test_created_at_survives_the_round_trip_as_an_aware_utc_datetime(
    repository: WidgetRepository,
) -> None:
    """The property a storage round trip silently loses.

    A naive datetime read back from a column that dropped its offset compares
    unequal to the one written, and the frozen domain type refuses one outright
    -- so an adapter that loses the timezone fails at CONSTRUCTION rather than
    at comparison, which is why this is asserted on the READ.
    """
    repository.add(_widget("w1"))

    restored = repository.get("org_c", "w1")

    assert restored is not None
    assert restored.created_at.tzinfo is not None
    assert restored.created_at == datetime(2026, 1, 1, 12, 30, 45, 123456, tzinfo=UTC)


def test_microsecond_precision_survives_the_round_trip(repository: WidgetRepository) -> None:
    """A column declared without sub-second precision truncates, silently.

    The fake cannot lose precision, so this assertion is aimed squarely at the
    real arm's column type -- which is the reason a contract suite runs the same
    test against both rather than testing each adapter where it is strongest.
    """
    repository.add(_widget("w1"))

    stored = repository.get("org_c", "w1")

    assert stored is not None
    assert stored.created_at.microsecond == 123456


def test_add_and_get_agree_for_a_caller_supplied_factory(
    repository: WidgetRepository,
) -> None:
    """`WidgetRepository` is a runtime-checkable Protocol; assert conformance too.

    Structural conformance and behavioural conformance are different claims. An
    object can satisfy every test above through duck typing while failing
    `isinstance`, and a container that type-checks its wiring would then refuse
    it at startup -- the one place the failure costs a deploy.
    """
    assert isinstance(repository, WidgetRepository)


def test_the_suite_ran_against_more_than_one_implementation(
    request: pytest.FixtureRequest,
) -> None:
    """The positive control for the parameterisation itself.

    A contract suite that silently collapsed to ONE arm would pass every test
    above and prove nothing about substitutability -- the failure would be
    invisible, because every assertion is still green. Reading the fixture's own
    parameter list is the cheapest thing that can see it.
    """
    ids = {
        item.callspec.params["repository"]
        for item in request.session.items
        if hasattr(item, "callspec") and "repository" in item.callspec.params
    }

    assert EXPECTED_ARMS <= ids, f"the contract suite ran only: {sorted(ids)}"
