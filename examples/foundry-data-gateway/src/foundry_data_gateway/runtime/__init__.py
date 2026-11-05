"""`runtime` -- the production lifecycle (§6).

THE PUBLIC CONTRACT of this submodule: probes with distinct semantics, graceful
shutdown with an in-flight drain, structured JSON logging with a correlation id,
explicit single-headed migration application, and an idempotent startup path.

`runtime` sits ABOVE `transport` in §1's direction, so it may import the HTTP
framework and the transport submodule; it may NOT import `composition`. It
reads no configuration of its own -- every value arrives as a constructor
argument from the composition root, which is the only module that resolves the
environment.
"""

from __future__ import annotations

from foundry_data_gateway.runtime.health_endpoints import build_router
from foundry_data_gateway.runtime.lifecycle import ServiceLifecycle
from foundry_data_gateway.runtime.logging import (
    CORRELATION_ID_HEADER,
    JsonLogFormatter,
    configure_json_logging,
    correlation_id,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from foundry_data_gateway.runtime.middleware import (
    CorrelationIdMiddleware,
    DrainTrackingMiddleware,
)
from foundry_data_gateway.runtime.migrations import (
    MigrationOutcome,
    MigrationRunner,
    assert_single_head,
)
from foundry_data_gateway.runtime.probes import NamedReadinessProbe, RuntimeHealthReporter
from foundry_data_gateway.runtime.shutdown import GracefulShutdown

__all__ = [
    "CORRELATION_ID_HEADER",
    "CorrelationIdMiddleware",
    "DrainTrackingMiddleware",
    "GracefulShutdown",
    "JsonLogFormatter",
    "MigrationOutcome",
    "MigrationRunner",
    "NamedReadinessProbe",
    "RuntimeHealthReporter",
    "ServiceLifecycle",
    "assert_single_head",
    "build_router",
    "configure_json_logging",
    "correlation_id",
    "new_correlation_id",
    "reset_correlation_id",
    "set_correlation_id",
]
