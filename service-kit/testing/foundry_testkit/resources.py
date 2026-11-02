"""One place that decides "is this real resource actually here".

Every kit in this package refuses through `ResourceUnavailable`, and every
fixture turns that into `pytest.skip(reason)`. The reason string always says
WHAT was missing and HOW to provide it, because the alternative -- a bare
`pytest.mark.skip`, or worse a silent pass -- is indistinguishable in a green
run from a test that ran.

A skip is not a pass. `tests/support/test_support_kit.py` asserts each kit's
refusal carries a non-empty, actionable reason, so a kit cannot quietly become
a no-op.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from collections.abc import Callable
from typing import NoReturn

__all__ = [
    "REQUIRE_ENV",
    "ResourceUnavailable",
    "docker_available",
    "require",
    "skip_or_fail",
    "tcp_open",
]

#: Set to "1" to turn every unavailable real resource into an ERROR instead of a
#: skip. ONE definition, imported by everything that reads it -- `real_profile`
#: honours it at bring-up and the resource fixtures honour it here. Two copies of
#: this string is how the flag came to be read in exactly one of the three places
#: that claimed to read it.
REQUIRE_ENV = "REQUIRE_REAL_RESOURCES"


class ResourceUnavailable(RuntimeError):
    """A real resource this test needs is not reachable. Carries the remedy."""


def skip_or_fail(reason: str) -> NoReturn:
    """A NAMED skip, or a FAILURE when the caller demanded real resources.

    WHY THIS EXISTS AS A FUNCTION rather than as two `pytest.skip` calls. The
    flag was read in one place -- the PostgreSQL controller -- while two fixtures
    called `pytest.skip` unconditionally and two docstrings said the flag turned
    every skip into a hard error. So `REQUIRE_REAL_RESOURCES=1` produced a green
    run with four SQS arms and one Redis arm skipped, which is the precise
    outcome the flag exists to make impossible. Measured on this branch before
    the fix.

    A blanket failure is safe here, and the reason is worth stating because the
    cautious alternative is tempting. A fixture body runs only when a test ASKS
    for it, so a service that uses no Redis never reaches this and cannot be
    failed by it. What the flag then means is exactly what its name says: a test
    that asked for a real resource and did not get one is a failure, not a
    footnote.
    """
    import pytest

    if os.environ.get(REQUIRE_ENV) == "1":
        pytest.fail(f"{REQUIRE_ENV}=1 and a real resource was unavailable: {reason}", pytrace=False)
    pytest.skip(reason)


def tcp_open(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """True when something is accepting TCP on `host:port`."""
    connection = socket.socket()
    connection.settimeout(timeout)
    try:
        connection.connect((host, port))
    except OSError:
        return False
    finally:
        connection.close()
    return True


def docker_available() -> bool:
    """True only when the DAEMON answers -- not merely when the CLI exists.

    `shutil.which("docker")` is the check that looks right and is wrong: a
    container image can ship the client with no socket to talk to, and then
    every compose-backed test fails at connect time with an error that reads
    like a broken test rather than a missing daemon.
    """
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    return probe.returncode == 0


def require(condition: bool, reason: str) -> None:
    if not reason.strip():
        raise ValueError("a ResourceUnavailable reason must say what is missing and how to fix it")
    if not condition:
        raise ResourceUnavailable(reason)


def call_or_unavailable[T](factory: Callable[[], T], reason: str) -> T:
    """Turn any construction failure into a ResourceUnavailable carrying `reason`."""
    try:
        return factory()
    except ResourceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 -- any construction failure is unavailability
        raise ResourceUnavailable(
            f"{reason} (underlying error: {type(exc).__name__}: {exc})"
        ) from exc
