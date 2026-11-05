"""``SeededAPICase`` — the services' equivalent of the backend's seeded test class.

In ``an older backend service`` a test class declares::

    class TestContentByteFinish:
        mysql_data_sources = ['integration/user/content_byte_finish/chapters', ...]

and an autouse conftest fixture truncates, seeds and hands the instance a
``client``.  This module is that contract for a Foundry service, with two
differences that follow from the services being port-shaped rather than
ORM-shaped:

* ``seed_sources`` replaces ``mysql_data_sources`` (the resources are not all
  MySQL, and one of them is not even a database).
* ``seed_profile`` selects the arm.  ``"fake"`` rebuilds the container per
  test — total isolation for nothing, because nothing is persistent.
  ``"real"`` uses the backend's SAVEPOINT protocol verbatim, including the
  ``sources_key``/``clean`` handshake that lets the next test with an
  IDENTICAL ``seed_sources`` skip truncate+seed.

A service wires this by implementing ``build_environment`` in its own
``tests/support/environment.py`` and pointing ``SEEDED_ENVIRONMENT`` at it;
everything below is service-agnostic and copied verbatim between repos.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "SeededAPICase",
    "SeedProfile",
    "SeededEnvironment",
    "EnvironmentBuilder",
    "seed_state",
]

# `store_rows` is NOT imported here. This module is imported at COLLECTION time
# by every service's conftest, and its own docstring asks to be kept
# import-light; `store_rows.SqlStoreRows.select` imports sqlalchemy lazily for
# the same reason. A service reaches the bases from `foundry_testkit.store_rows`
# directly.


# Cooperation contract with the conftest fixture, ported from the backend's
# src/tests/support/transactional_test.py:
#   - conftest records repr(seed_sources) in 'sources_key' and sets
#     'clean' = False before every test runs;
#   - the savepoint fixture sets 'clean' = True after a successful rollback,
#     proving the resource is back at its seeded baseline;
#   - conftest then skips truncate+seed for the next test declaring an
#     IDENTICAL seed_sources while 'clean' is True.
# Keep this module import-light: conftest imports it at collection time.
seed_state: dict[str, Any] = {"sources_key": None, "clean": False, "sequences": None}


SeedProfile = str  # "fake" | "real"


def active_profile(default: SeedProfile = "fake") -> SeedProfile:
    """The seed profile for this run.

    ``composition/config.py`` is the only PRODUCTION module allowed to read
    the environment (S2); test support is not production code, and the arm
    selector has to come from outside the test to let one suite run twice.
    """
    return os.environ.get("SEED_PROFILE", default)


@dataclass
class SeededEnvironment:
    """What a seeded test is handed.

    ``client``    - an HTTP client bound to the composed app.
    ``container`` - the composition container, for asserting on adapter state
                    that the API does not expose.
    ``seeded``    - the ``SeedResult``; assert corpus facts against this rather
                    than hardcoding counts that drift from the fixture.
    ``rows``      - a ``StoreRows`` over whichever store this arm wired; see
                    ``foundry_testkit.store_rows``. Optional, because a service
                    with no persistence has nothing to read back -- but a
                    persistent service that leaves it ``None`` has a seeded tier
                    that can only assert responses, which is the gap this field
                    exists to close.
    """

    client: Any
    container: Any
    seeded: Any
    profile: SeedProfile = "fake"
    teardown: Any = None
    rows: Any = None


class EnvironmentBuilder(Protocol):
    """Implemented per service in ``tests/support/environment.py``."""

    def __call__(
        self, *, sources: Sequence[str], profile: SeedProfile
    ) -> SeededEnvironment: ...


class SeededAPICase:
    """Base for every class under ``tests/seeded/`` and ``tests/e2e/``.

    Subclasses declare the corpus and nothing else::

        class TestRetrievalAPI(SeededAPICase):
            seed_sources = ["retrieval/hybrid/documents", "retrieval/hybrid/chunks"]

            def test_ec1_empty_query_returns_empty_not_error(self):
                resp = self.client.post("/v1/retrievals", json={...})
                assert resp.status_code == 200

    The autouse fixture in ``tests/conftest.py`` populates ``client``,
    ``container`` and ``seeded`` before each test.  A subclass that declares
    no ``seed_sources`` is a findable error, not a silently un-seeded test —
    ``scripts/check_seeded_tests.py`` fails on it.
    """

    #: Fixture paths relative to ``tests/fixtures/``, without ``.json``.
    seed_sources: Sequence[str] = ()

    #: ``"fake"`` (default) or ``"real"``; ``SEED_PROFILE`` overrides.
    seed_profile: SeedProfile | None = None

    # Injected per test by the conftest fixture.
    client: Any
    container: Any
    seeded: Any

    #: What the STORE holds, for a test that will not settle for the response.
    #: The SAME questions under both profiles -- one implementation reads the
    #: real database, the other projects the fake -- so a row assertion is one
    #: test rather than a real-arm-only test, which matters because
    #: `service-kit/ci/verify.sh` refuses a run whose two profiles collect
    #: different numbers of tests.
    rows: Any

    @classmethod
    def resolved_profile(cls) -> SeedProfile:
        return active_profile(cls.seed_profile or "fake")
