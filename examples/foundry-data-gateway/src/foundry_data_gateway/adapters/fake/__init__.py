"""`adapters.fake` -- in-memory port implementations. No network, no filesystem,
no vendor SDK.

THE PUBLIC CONTRACT of this sub-package, and the substitute for a mock. §7 of
the module contract forbids `unittest.mock`/`MagicMock`/collaborator
`monkeypatch` for anything that has a port; these are what you use instead, and
`tests/contract/` runs the SAME contract suite against each of them and against
the corresponding `adapters/real/` implementation, so "the fake behaves like the
real thing" is a measured claim rather than a hope.

Eagerly imported, unlike `adapters.real`: nothing here has a vendor dependency
that could fail to import.

THERE IS DELIBERATELY NO UNIT-OF-WORK FAKE. `InMemoryUnitOfWork` used to live
here, handing out a `dict` as its "session", and the outbox publisher carried a
second code path to write into it. What that arrangement actually tested was
the fake: a rolled-back dict is cleared by the fake's own `rollback()`, so the
suite's "a rollback discards the outbox row" claim was a claim about a dict
comprehension rather than about a transaction. A transaction cannot be
substituted, because the behaviour under test IS the database's. The real
`SqlUnitOfWork` is constructed lazily and opens no connection until first use,
so a test that needs one costs nothing to build -- see
`tests/integration/test_outbox_transaction.py`, which uses the real one and
gets a real rollback.
"""

from __future__ import annotations

from foundry_data_gateway.adapters.fake.auth import FakePrincipalAuth
from foundry_data_gateway.adapters.fake.clock import FrozenClock
from foundry_data_gateway.adapters.fake.event_bus import RecordingPublisher
from foundry_data_gateway.adapters.fake.header_principal_auth import HeaderPrincipalAuth
from foundry_data_gateway.adapters.fake.health import FakeHealthReporter
from foundry_data_gateway.adapters.fake.readiness import FakeReadinessCheck
from foundry_data_gateway.adapters.fake.secrets import FakeSecrets
from foundry_data_gateway.adapters.fake.transaction import RecordingTransaction
from foundry_data_gateway.adapters.fake.widget_repository import InMemoryWidgetRepository

__all__ = [
    "FakeHealthReporter",
    "FakePrincipalAuth",
    "FakeReadinessCheck",
    "FakeSecrets",
    "FrozenClock",
    "HeaderPrincipalAuth",
    "InMemoryWidgetRepository",
    "RecordingPublisher",
    "RecordingTransaction",
]
