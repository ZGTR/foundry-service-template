"""The arm that would have caught the stale-container adoption (reviewer finding, A3).

WHAT WENT WRONG, so the test can be read against it. `up -d` ADOPTS a container
already carrying this project's labels rather than creating a new one, so a
container left behind by a run that crashed before teardown is silently reused
by every later run in the same checkout. One such container published no host
port, and `docker compose port postgres 5432` reported that by printing
`invalid IP:0` AT EXIT 0. The original parser asked only whether the output
contained a colon and ended in an integer -- both true -- so it built a URL on
port 0, and libpq then substituted its own default and failed against port 5432.
The message accused PostgreSQL. The fault was a container from a previous run
and the remedy was one `docker rm -f`, which that message could not lead anyone
to.

WHY THIS TEST CREATES A REAL CONTAINER. The verdict under test is read through
Docker, so the control mutates through Docker: a unit test over the parser would
pin the string `invalid IP:0`, which is this defect's SYMPTOM and not its
mechanism, and would stay green if a future docker printed something else at
exit 0. The stale container is built from its own compose file with `expose:`
instead of `ports:`, under a scratch project name; `docker compose` resolves a
running container by PROJECT LABEL, so the production function reads it through
the production compose file exactly as it would read a real one.

THE POSITIVE CONTROL IS NOT OPTIONAL. `_published_port` raising
`RealResourcesUnavailable` proves nothing on a machine where Docker is broken -- every
arm raises there, including the one that must not. So the same production
function is run against the real fixture's project in the same session and must
return a port that accepts a connection.
"""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from foundry_testkit.real_profile import (
    PUBLISHED,
    RealResourcesUnavailable,
    _pick_container_name,
    _published_port,
    _refuse_a_port_nothing_listens_on,
    _resolve_published_port,
    compose_project_name,
)

#: The checkout, passed explicitly. The kit resolves it from its own location for
#: production callers; an arm that relied on that default would stop testing the
#: parameter the moment a service moved its kit.
ROOT = Path(__file__).resolve().parents[2]

#: The two refusals `_published_port` can raise. They share an exception type and
#: a remedy, so every arm below must assert WHICH one fired: without that, a
#: deleted clause is covered by its neighbour and the arm stays green.
PUBLICATION_REFUSAL = "publishes no usable host port"
REACHABILITY_REFUSAL = "nothing accepts a connection there"

#: Its own project name, so nothing here can touch the fixture's containers.
STALE_PROJECT = "shp-tpl-stale-container-arm"

#: A project with no containers at all: the name-resolution fallback is what the
#: zero-port arm reads, and it must not collide with a real one.
ZERO_PORT_PROJECT = "shp-tpl-zero-port-arm"

#: A postgres that EXPOSES 5432 without publishing it -- the shape a crashed run
#: leaves behind. `expose:` rather than a doctored `ports:` because compose
#: merges `ports` by appending, so an override cannot remove a publication.
STALE_COMPOSE = """
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: service_test
    expose:
      - "5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 3s
      timeout: 3s
      retries: 20
"""


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300)


@pytest.fixture(scope="module")
def stale_container(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A running postgres with no published host port. Always torn down."""
    compose_file: Path = tmp_path_factory.mktemp("stale") / "compose.yml"
    compose_file.write_text(STALE_COMPOSE)
    up = _docker(
        "compose", "-p", STALE_PROJECT, "-f", str(compose_file), "up", "-d", "--wait", "postgres"
    )
    if up.returncode != 0:
        pytest.skip(f"cannot build the stale container without Docker: {up.stderr.strip()[:300]}")
    try:
        yield STALE_PROJECT
    finally:
        _docker("compose", "-p", STALE_PROJECT, "-f", str(compose_file), "down", "-v")


def test_the_stale_container_really_has_no_published_port(stale_container: str) -> None:
    """Prove the FIXTURE before trusting the verdict it produces.

    A fixture that failed to start, or that quietly published a port after all,
    would make the refusal below pass for the wrong reason -- and a red arm that
    agrees with the conclusion you want is indistinguishable from a working one.
    """
    listed = _docker(
        "ps", "--filter", f"name={stale_container}", "--format", "{{.Names}} {{.Ports}}"
    )

    assert f"{stale_container}-postgres-1" in listed.stdout, listed.stdout
    assert "->5432" not in listed.stdout, (
        f"the stale fixture published a port after all: {listed.stdout}"
    )


def test_a_stale_container_is_refused_by_name_with_a_runnable_remedy(stale_container: str) -> None:
    """The production function, against the container the defect leaves behind."""
    with pytest.raises(RealResourcesUnavailable) as excinfo:
        _published_port(stale_container, ROOT)

    message = str(excinfo.value)
    assert f"{stale_container}-postgres-1" in message, "the refusal must name the container"
    assert f"docker rm -f {stale_container}-postgres-1" in message, (
        "the refusal must carry a command the reader can run, not a description of the problem"
    )
    # NAME THE CLAUSE. Two different guards in `_published_port` raise this same
    # exception type with this same remedy, so an arm that checked only the type
    # and the container name would be satisfied by whichever one happened to fire
    # -- and would stay green with its own clause deleted.
    assert PUBLICATION_REFUSAL in message, message


def test_the_published_port_pattern_rejects_dockers_own_error_text() -> None:
    """Pin the exact bytes, because every weaker test of them passes.

    `invalid IP:0` is what docker printed to STDOUT, at exit 0, for the container
    above. It is non-empty, it contains a colon, and it ends in a parseable
    integer, so it satisfies each check an author reaches for before anchoring
    the pattern. The second half is the positive control: an anchored pattern
    that matched nothing at all would pass the first assertion for free.
    """
    assert PUBLISHED.match("invalid IP:0") is None
    assert int("invalid IP:0".rsplit(":", 1)[1]) == 0, "the pre-fix parser accepted this as port 0"
    assert PUBLISHED.match("0.0.0.0:55549").group("port") == "55549"
    assert PUBLISHED.match("[::]:55549").group("port") == "55549"


def test_a_wellformed_zero_port_is_refused_although_the_pattern_accepts_it() -> None:
    """The range clause, which the anchored pattern does NOT cover.

    The anchor rejects the exact bytes docker printed here, so blinding the range
    check left every other arm in this file green -- the clause was defence
    against a shape nothing exercised, which in this estate is decoration rather
    than defence. `0.0.0.0:0` is well-formed and asserted below to MATCH, so the
    anchor alone waves it through; only the range check turns it into a refusal,
    and port 0 is precisely the value libpq silently rewrites to 5432.

    It hands the string to `_resolve_published_port` DIRECTLY. No real docker
    emits `0.0.0.0:0` on demand, and the first version of this arm faked
    `_compose` to produce it -- which `check_mock_budget` refused, correctly. The
    seam is a pure function over what docker said, so it needs no double, and the
    whole path is still driven against a real container two arms above.
    """
    assert PUBLISHED.match("0.0.0.0:0") is not None, (
        "if the pattern rejected this, the range clause below would be unreachable "
        "and this arm would be proving nothing"
    )

    with pytest.raises(RealResourcesUnavailable) as excinfo:
        _resolve_published_port("0.0.0.0:0\n", 0, f"{ZERO_PORT_PROJECT}-postgres-1")

    message = str(excinfo.value)
    assert f"docker rm -f {ZERO_PORT_PROJECT}-postgres-1" in message
    assert PUBLICATION_REFUSAL in message, (
        "the refusal must be the PUBLICATION one. `_resolve_published_port` is pure since "
        "the kit took it over, so deleting the range clause makes it RETURN 0 rather than "
        "reach the reachability guard -- `pytest.raises` then fails and this assertion never "
        "runs. Both are red; they are red for different reasons, and the message says which. "
        f"Actual: {message}"
    )


def test_dockers_diagnostic_text_is_never_returned_as_a_container_name() -> None:
    """A remedy nobody can run is worse than no remedy: it reads as an instruction.

    `0.0.0.0:0` is what an earlier version of this fix actually handed back while
    it was being built, producing `docker rm -f 0.0.0.0:0`. Docker writes its
    diagnostics to STDOUT at exit 0 -- the same mechanism as the defect this file
    exists for -- so the line `ps` prints is not necessarily a container name.

    Direct, because a real docker cannot be made to print this: `ps` either names
    the container or says nothing, so the filter is unreachable through the live
    path. The second assertion is the positive control -- a filter that rejected
    everything would satisfy the first for free and would silently replace every
    real container name with a guess.
    """
    assert _pick_container_name("0.0.0.0:0\n", ZERO_PORT_PROJECT) == (
        f"{ZERO_PORT_PROJECT}-postgres-1"
    )
    assert _pick_container_name("", ZERO_PORT_PROJECT) == f"{ZERO_PORT_PROJECT}-postgres-1"
    assert _pick_container_name(
        f"invalid IP:0\n{ZERO_PORT_PROJECT}-postgres-1\n", ZERO_PORT_PROJECT
    ) == f"{ZERO_PORT_PROJECT}-postgres-1", "a real name after a diagnostic line must still win"


def test_a_port_nothing_listens_on_is_refused_by_name_too(stale_container: str) -> None:
    """The second clause, which the range check above cannot reach.

    A port bound on another interface, or a container that died between
    `up --wait` and `port`, both produce a number that parses. Without this the
    clause would be unproven -- blinding it left every other arm in this file
    green, which is what an unexercised clause looks like from the outside.

    The closed port is obtained by binding one and releasing it, rather than
    picking a literal: a hardcoded "surely nothing is on 9" is an assumption
    about the machine, and the day it is wrong this arm passes for the wrong
    reason.
    """
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        closed_port = reserved.getsockname()[1]

    with pytest.raises(RealResourcesUnavailable) as excinfo:
        _refuse_a_port_nothing_listens_on(closed_port, f"{stale_container}-postgres-1")

    message = str(excinfo.value)
    assert f"{stale_container}-postgres-1" in message
    assert f"docker rm -f {stale_container}-postgres-1" in message
    assert str(closed_port) in message
    assert REACHABILITY_REFUSAL in message, message


def test_the_real_fixtures_port_is_accepted_and_reachable(real_postgres) -> None:
    """THE POSITIVE CONTROL. Without it, a broken Docker passes every arm above."""
    port = _published_port(real_postgres.project, ROOT)

    assert 0 < port < 65536
    assert str(port) in real_postgres.url, "the fixture must dial the port it resolved"
    assert compose_project_name(ROOT) == real_postgres.project
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        pass
