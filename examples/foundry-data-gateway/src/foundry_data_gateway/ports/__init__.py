"""`ports` -- consumer-owned interfaces. One port per file.

THE PUBLIC CONTRACT of this submodule: every name another submodule may depend
on is re-exported here. A port is a `typing.Protocol` (or a narrow ABC); it
imports `domain` and stdlib/typing and NOTHING else. No `boto3`, no
`sqlalchemy`, no `fastapi` -- `scripts/check_layering.py` fails the build on
any of them, because a port that names a vendor is not a port, it is that
vendor's interface wearing a port's directory.
"""

from __future__ import annotations

from foundry_data_gateway.ports.auth import PrincipalAuth
from foundry_data_gateway.ports.clock import Clock, FrozenClock, SystemClock
from foundry_data_gateway.ports.event_bus import Event, EventHandler, Publisher, Subscriber
from foundry_data_gateway.ports.health import HealthReport, HealthReporter, ProbeResult
from foundry_data_gateway.ports.readiness import ReadinessCheck
from foundry_data_gateway.ports.secrets import SecretNotFound, SecretsPort
from foundry_data_gateway.ports.transaction import (
    TransactionalUnitOfWork,
    TransactionBoundary,
)
from foundry_data_gateway.ports.widget_repository import WidgetRepository

__all__ = [
    "Clock",
    "Event",
    "EventHandler",
    "FrozenClock",
    "HealthReport",
    "HealthReporter",
    "PrincipalAuth",
    "ProbeResult",
    "Publisher",
    "ReadinessCheck",
    "SecretNotFound",
    "SecretsPort",
    "Subscriber",
    "SystemClock",
    "TransactionBoundary",
    "TransactionalUnitOfWork",
    "WidgetRepository",
]
