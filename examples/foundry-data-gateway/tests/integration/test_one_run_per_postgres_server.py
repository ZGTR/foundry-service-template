"""One test run per PostgreSQL server, asserted against a REAL PostgreSQL.

WHAT WENT WRONG. Two runs were pointed at one server through
`FOUNDRY_TEST_DATABASE_URL`. Every run uses the same names there, `template_0`
and `test_<gwN>`, so each dropped and recreated the other's databases while it
was using them: 289 setup errors, all about missing tables, none about a second
run. The kit's own advice at the time was to do exactly that.

The controller now holds a session-level advisory lock for the whole run
(`claim_server`). These tests run INSIDE such a run, so the lock under test is
already held by this run's own controller. That is the first arm: the real key,
held by a real run, refuses a second claim. The other unit arms use keys of
their own, one per test, so two xdist workers cannot refuse each other.

The last two tests start a second pytest against this run's server and read what
it did from outside: its exit code, its message, and whether this run's template
database is still the one that was there before.

WHAT IT DOES NOT PROVE. A service that declares no `postgres` resource has no
server to lock; two of its runs in one checkout still share containers.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from collections import namedtuple
from datetime import UTC, datetime
from pathlib import Path

import pytest
from foundry_testkit.real_profile import (
    DATABASE_URL_ENV,
    KEEP_RESOURCES_ENV,
    REQUIRE_ENV,
    RUN_APPLICATION_PREFIX,
    RUN_LOCK_KEY,
    TEMPLATE_DATABASE,
    RealProfile,
    RealResourcesUnavailable,
    RunClaim,
    ServerInUse,
    _describe_holder,
    _who_holds,
    claim_server,
    lock_holders,
)
from foundry_testkit.timed import joined
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
KIT_TESTING = REPOSITORY_ROOT / "service-kit" / "testing"
KIT_VENDOR = REPOSITORY_ROOT / "service-kit" / "vendor"

#: HAND-WRITTEN, one per test, and none of them the run's own key.
KEY_SECOND_CLAIM = 0x5D19_B001
KEY_KILLED_RUN = 0x5D19_B002
KEY_ACCOUNTING = 0x5D19_B003
KEY_HOLDERS = 0x5D19_B004
KEY_UNREADABLE = 0x5D19_B005
KEY_RELEASED = 0x5D19_B006

REFUSAL = "another test run holds this PostgreSQL server"
#: pytest's exit code for a usage error. A refused run must not look like a run
#: that collected nothing (5) or one whose tests failed (1).
USAGE_ERROR = 4


def _admin_url(profile: RealProfile) -> str:
    if profile.admin_url is None:  # pragma: no cover - the template declares postgres
        pytest.skip("real arm skipped: no 'postgres' resource declared")
    return profile.admin_url


def _scalar(admin_url: str, sql: str, **parameters: object) -> object:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            return connection.execute(text(sql), parameters).scalar()
    finally:
        engine.dispose()


def _sessions_named(admin_url: str, label: str) -> int:
    return int(
        _scalar(
            admin_url,
            "SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE :name",
            name=f"{RUN_APPLICATION_PREFIX} % {label}%",
        )
        or 0
    )


def _template_oid(admin_url: str) -> object:
    """Changes when the database is dropped and recreated, whatever it then holds."""
    return _scalar(
        admin_url, "SELECT oid FROM pg_database WHERE datname = :name", name=TEMPLATE_DATABASE
    )


def test_this_runs_own_controller_already_holds_the_server(real_profile: RealProfile) -> None:
    with pytest.raises(ServerInUse) as refused:
        claim_server(_admin_url(real_profile), "a-second-run")

    message = str(refused.value)
    assert REFUSAL in message
    assert RUN_APPLICATION_PREFIX in message, f"the refusal does not say who holds it: {message}"
    assert re.search(r"connected since \d{2}:\d{2}:\d{2} \S+\. Every run", message), (
        f"the refusal gives no time with a zone: {message}"
    )
    assert DATABASE_URL_ENV in message, f"the refusal names no remedy: {message}"


def test_a_second_claim_is_refused_until_the_first_is_released(real_profile: RealProfile) -> None:
    admin = _admin_url(real_profile)
    assert KEY_SECOND_CLAIM != RUN_LOCK_KEY

    first = claim_server(admin, "first-holder", key=KEY_SECOND_CLAIM)
    try:
        with pytest.raises(ServerInUse) as refused:
            claim_server(admin, "second-holder", key=KEY_SECOND_CLAIM)
        # Counted while `refused` is alive: its traceback keeps the claim's frame,
        # so a session the refusal forgot to close cannot be closed by the
        # garbage collector before it is counted.
        assert _sessions_named(admin, "second-holder") == 0, (
            "the refused claim kept its session open"
        )
        assert "first-holder" in str(refused.value)
    finally:
        first.release()

    claim_server(admin, "third-holder", key=KEY_SECOND_CLAIM).release()


def test_a_run_that_dies_leaves_the_server_free(real_profile: RealProfile) -> None:
    """A killed process closes its connection; PostgreSQL drops the lock with it."""
    admin = _admin_url(real_profile)
    dead = claim_server(admin, "killed-holder", key=KEY_KILLED_RUN)
    try:
        ended = _scalar(
            admin,
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            "WHERE application_name LIKE :name",
            name=f"{RUN_APPLICATION_PREFIX} % killed-holder%",
        )
        assert ended == 1, f"expected to end exactly the holder's backend, ended {ended}"

        claim_server(admin, "next-holder", key=KEY_KILLED_RUN).release()
    finally:
        dead.release()


def test_releasing_a_claim_leaves_no_session_behind(real_profile: RealProfile) -> None:
    admin = _admin_url(real_profile)
    before = _sessions_named(admin, "accounting-holder")

    claim = claim_server(admin, "accounting-holder", key=KEY_ACCOUNTING)
    try:
        during = _sessions_named(admin, "accounting-holder")
    finally:
        claim.release()
    after = _sessions_named(admin, "accounting-holder")

    assert (before, during, after) == (0, 1, 0), (
        f"sessions named for this claim before/during/after: {(before, during, after)}"
    )


def _execute(admin_url: str, sql: str) -> None:
    """A statement that returns no row: CREATE and DROP."""
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text(sql))
    finally:
        engine.dispose()


def _session(url: str, name: str):  # noqa: ANN202 - (engine, connection), sqlalchemy handles
    engine = create_engine(
        url,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
        connect_args={"application_name": name},
    )
    return engine, engine.connect()


def test_a_refused_claim_is_not_an_absent_resource() -> None:
    """An absent resource is a named skip. A server that answered and is busy must
    stop the run, so `pytest_configure` may not catch the one as the other."""
    assert not issubclass(ServerInUse, RealResourcesUnavailable)


@pytest.mark.parametrize("key", [-1, 2**32, 2**63 - 1])
def test_a_key_the_holder_query_cannot_read_is_refused_before_connecting(key: int) -> None:
    """`pg_locks` shows a key below 2**32 as (0, key), which is what the holder
    query asks for. Outside that range the lock would be taken and its holder never
    found. Nothing listens on port 1: a check made after connecting fails otherwise."""
    with pytest.raises(ValueError, match=r"\[0, 2\*\*32\)"):
        claim_server("postgresql+psycopg://nobody@127.0.0.1:1/none", "any-run", key=key)


def test_the_largest_key_in_range_is_held_and_its_holder_named(real_profile: RealProfile) -> None:
    admin = _admin_url(real_profile)
    top = 2**32 - 1

    first = claim_server(admin, "top-key-holder", key=top)
    try:
        with pytest.raises(ServerInUse, match="top-key-holder"):
            claim_server(admin, "top-key-second", key=top)
    finally:
        first.release()


def test_the_holder_is_the_granted_session_in_this_database(real_profile: RealProfile) -> None:
    """Three sessions have a `pg_locks` row for one key and one of them holds this
    lock. A second WAITS for it here, and a third HOLDS the same key in another
    database, where it is another lock. Named wrongly, a refused run would be sent
    to wait for a process that is not in its way."""
    admin = _admin_url(real_profile)
    other_database = f"claim_decoy_{os.getpid()}"
    _execute(admin, f'CREATE DATABASE "{other_database}"')
    holder = claim_server(admin, "granted-holder", key=KEY_HOLDERS)
    elsewhere_url = make_url(admin).set(database=other_database).render_as_string(False)
    elsewhere_engine, elsewhere = _session(elsewhere_url, "holder-in-another-database")
    waiter_engine, waiter = _session(admin, "waiter-not-granted")
    got_through: list[bool] = []

    def wait_for_the_lock() -> None:
        waiter.execute(text("SET lock_timeout = '30s'"))
        waiter.execute(text("SELECT pg_advisory_lock(:key)"), {"key": KEY_HOLDERS})
        got_through.append(True)

    waiting = threading.Thread(target=wait_for_the_lock)
    rows = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :key"
    try:
        assert elsewhere.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": KEY_HOLDERS}
        ).scalar(), "the same key in another database must be free: it is another lock"
        waiting.start()
        deadline = time.monotonic() + 30
        while _scalar(admin, rows + " AND NOT granted", key=KEY_HOLDERS) != 1:
            assert time.monotonic() < deadline, "the waiting session never queued for the lock"
            time.sleep(0.05)
        assert _scalar(admin, rows, key=KEY_HOLDERS) == 3, (
            "the holder, the waiter and the other database's holder should each have a row"
        )
        watcher_engine, watcher = _session(admin, "watcher")
        try:
            names = [row.application_name for row in lock_holders(watcher, KEY_HOLDERS)]
        finally:
            watcher.close()
            watcher_engine.dispose()
    finally:
        holder.release()
        if waiting.ident is not None:
            joined(waiting, "the session waiting for the lock")
        for connection, engine in ((waiter, waiter_engine), (elsewhere, elsewhere_engine)):
            connection.close()
            engine.dispose()
        _execute(admin, f'DROP DATABASE IF EXISTS "{other_database}" WITH (FORCE)')

    assert len(names) == 1 and names[0].endswith(" granted-holder"), names
    assert got_through == [True], "the waiter was not let through when the holder released"


def test_a_holder_this_role_may_not_read_is_named_by_its_pid(real_profile: RealProfile) -> None:
    """PostgreSQL blanks another role's session for a role without
    `pg_read_all_stats`. The refusal used to die formatting a start time of None,
    which pytest reported as an internal error and not as a refusal."""
    admin = _admin_url(real_profile)
    role = f"claim_reader_{os.getpid()}"
    _execute(admin, f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'reader'")
    restricted = make_url(admin).set(username=role, password="reader").render_as_string(False)

    holder = claim_server(admin, "unreadable-holder", key=KEY_UNREADABLE)
    try:
        with pytest.raises(ServerInUse) as refused:
            claim_server(restricted, "restricted-second", key=KEY_UNREADABLE)
    finally:
        holder.release()
        _execute(admin, f'DROP ROLE IF EXISTS "{role}"')

    message = str(refused.value)
    assert re.search(r"backend pid \d+, whose session this role may not read", message), message
    assert "unreadable-holder" not in message, "the test's role could read the holder after all"


_Holder = namedtuple("_Holder", "pid application_name backend_start")


def test_no_holder_row_is_a_run_that_has_just_ended() -> None:
    assert _describe_holder([]) == "a run that ended between the refusal and the question"


def test_the_holders_start_time_is_the_readers_clock_with_its_zone() -> None:
    """The server's clock is UTC in the kit's container. Printed bare on a host an
    hour ahead, it sent the reader looking for a run started an hour earlier."""
    started = datetime.now(UTC)
    local = started.astimezone()
    assert local.tzname(), "this host reports no zone name to print"

    said = _describe_holder([_Holder(7, "testkit run pid 7 svc", started)])

    assert said == f"'testkit run pid 7 svc', connected since {local:%H:%M:%S} {local.tzname()}"


class _Handle:
    """Stands in for a claim's connection and engine, and records what `release`
    asks of them. A statement can be made to fail the way a dead connection fails."""

    def __init__(self, *, fails: bool) -> None:
        self.asked: list[object] = []
        self._fails = fails

    def execute(self, statement: object, parameters: dict) -> None:
        self.asked.append(("unlock", parameters["key"], "pg_advisory_unlock" in str(statement)))
        if self._fails:
            raise OperationalError(str(statement), parameters, Exception("gone"))

    def close(self) -> None:
        self.asked.append("close")

    def dispose(self) -> None:
        self.asked.append("dispose")


def test_a_holder_that_cannot_be_named_is_still_a_refusal() -> None:
    """PostgreSQL has already said no when the holder is asked for. A failure there
    goes into the refusal's text; raised as itself it is not a `ServerInUse`, and a
    run that adopted the other run's stack would then take that stack down."""
    assert _who_holds(_Handle(fails=True), KEY_RELEASED) == (
        "a holder this run could not name (OperationalError)"
    )


@pytest.mark.parametrize("unlock_fails", [False, True])
def test_release_asks_for_the_unlock_first_and_closes_either_way(unlock_fails: bool) -> None:
    """The unlock is what makes the server free when `release` returns; the close
    alone frees it only once PostgreSQL has ended the backend. A connection that is
    already gone cannot answer the unlock, and must still be closed and disposed."""
    handle = _Handle(fails=unlock_fails)

    RunClaim(handle, handle, KEY_RELEASED).release()

    assert handle.asked == [("unlock", KEY_RELEASED, True), "close", "dispose"]


def test_a_released_claim_is_gone_from_a_connection_that_was_already_open(
    real_profile: RealProfile,
) -> None:
    """Asked on a connection opened BEFORE the release, so no new connection gives
    PostgreSQL time to end the claim's backend. With the close alone the claim was
    still listed in 23 of 600 tries on a loaded host."""
    admin = _admin_url(real_profile)
    watcher_engine, watcher = _session(admin, "watcher")
    still_held = 0
    try:
        for _ in range(40):
            claim_server(admin, "released-holder", key=KEY_RELEASED).release()
            still_held += len(lock_holders(watcher, KEY_RELEASED))
    finally:
        watcher.close()
        watcher_engine.dispose()

    assert still_held == 0, f"a released claim was still listed {still_held} time(s) of 40"


def _second_run(tmp_path: Path, database_url: str | None) -> subprocess.CompletedProcess[str]:
    """A whole second pytest with the kit plugin loaded, as a developer would start it."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_trivial.py").write_text("def test_trivial():\n    assert True\n")

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(KIT_TESTING), str(KIT_VENDOR), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    for name in (DATABASE_URL_ENV, REQUIRE_ENV, KEEP_RESOURCES_ENV):
        env.pop(name, None)
    if database_url is not None:
        env[DATABASE_URL_ENV] = database_url

    return subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "-p", "no:cacheprovider",
            "-p", "foundry_testkit.real_profile",
            "-q", str(tests),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        cwd=REPOSITORY_ROOT,
        env=env,
    )


def _assert_refused(result: subprocess.CompletedProcess[str]) -> None:
    output = result.stdout + result.stderr
    assert result.returncode == USAGE_ERROR, (
        f"the second run exited {result.returncode}, expected {USAGE_ERROR}:\n{output[-2000:]}"
    )
    assert REFUSAL in output, output[-2000:]
    assert "1 passed" not in output, "the second run went on to run its test"


def test_a_guest_on_a_server_in_use_is_refused_and_touches_nothing(
    real_profile: RealProfile, tmp_path: Path
) -> None:
    admin = _admin_url(real_profile)
    template_before = _template_oid(admin)
    assert template_before is not None, "this run's own template database is missing"

    result = _second_run(tmp_path, database_url=admin)

    _assert_refused(result)
    assert _template_oid(admin) == template_before, (
        "the refused guest dropped and recreated this run's template database"
    )


def test_a_second_run_in_this_checkout_is_refused_and_leaves_the_stack_up(
    real_profile: RealProfile, tmp_path: Path
) -> None:
    """The adopted case. Without an override the second run derives THIS checkout's
    compose project, `up` adopts this run's containers, and before the lock its
    `pytest_unconfigure` took them down with `down -v`."""
    if os.environ.get(DATABASE_URL_ENV):
        pytest.skip(
            f"{DATABASE_URL_ENV} is set, so this run is itself a guest and owns no stack "
            "for a second run to adopt"
        )
    admin = _admin_url(real_profile)
    template_before = _template_oid(admin)

    result = _second_run(tmp_path, database_url=None)

    _assert_refused(result)
    # The server is asked AFTER the second run has unconfigured. A `down -v` would
    # make this a connection error, not a different oid.
    assert _template_oid(admin) == template_before, (
        "this run's template database changed under a refused second run"
    )
