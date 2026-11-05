"""§6: a rollback-safe, IDEMPOTENT startup path."""

from __future__ import annotations

import pytest

from foundry_data_gateway.adapters.fake import FakeReadinessCheck
from foundry_data_gateway.runtime import (
    GracefulShutdown,
    MigrationRunner,
    NamedReadinessProbe,
    RuntimeHealthReporter,
    ServiceLifecycle,
)


def _lifecycle(*, migrations: MigrationRunner | None = None) -> tuple[
    ServiceLifecycle, RuntimeHealthReporter, GracefulShutdown
]:
    reporter = RuntimeHealthReporter(
        probes=(NamedReadinessProbe("db", FakeReadinessCheck(ready=True)),)
    )
    shutdown = GracefulShutdown(
        on_shutdown_signal=lambda: setattr(reporter, "draining", True), grace_seconds=1.0
    )
    lifecycle = ServiceLifecycle(
        reporter=reporter,
        shutdown=shutdown,
        migrations=migrations,
        install_signal_handlers=False,
    )
    return lifecycle, reporter, shutdown


def test_start_latches_startup_and_makes_the_service_ready() -> None:
    lifecycle, reporter, _shutdown = _lifecycle()

    lifecycle.start()

    assert lifecycle.started is True
    assert reporter.startup().healthy is True
    assert reporter.ready().healthy is True


def test_start_is_idempotent_and_runs_migrations_once() -> None:
    """A reload worker, a cold Lambda container and a second TestClient all
    enter the startup hook. A second migration run is how a green deploy breaks.
    """
    applied: list[str] = []
    runner = MigrationRunner(
        enabled=True, heads=lambda: ["rev_1"], upgrade=lambda head: applied.append(head)
    )
    lifecycle, _reporter, _shutdown = _lifecycle(migrations=runner)

    lifecycle.start()
    lifecycle.start()
    lifecycle.start()

    assert applied == ["rev_1"]


def test_a_FAILED_start_leaves_startup_UNLATCHED() -> None:
    """Rollback-safe: the latch is set last.

    A process that failed to migrate must report `/health/startup` red and be
    routed no traffic, rather than serving from a half-migrated database.
    """

    def _explode() -> list[str]:
        raise RuntimeError("cannot reach the revision directory")

    runner = MigrationRunner(enabled=True, heads=_explode, upgrade=lambda head: None)
    lifecycle, reporter, _shutdown = _lifecycle(migrations=runner)

    with pytest.raises(RuntimeError, match="revision directory"):
        lifecycle.start()

    assert lifecycle.started is False
    assert reporter.startup().healthy is False
    assert reporter.ready().healthy is False


def test_a_failed_start_can_be_retried_once_the_cause_is_fixed() -> None:
    """The idempotence guard must not latch on FAILURE as well as on success --
    a process that can never retry turns a transient fault into a dead task."""
    attempts: list[int] = []

    def _heads() -> list[str]:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return ["rev_1"]

    runner = MigrationRunner(enabled=True, heads=_heads, upgrade=lambda head: None)
    lifecycle, _reporter, _shutdown = _lifecycle(migrations=runner)

    with pytest.raises(RuntimeError):
        lifecycle.start()
    lifecycle.start()

    assert lifecycle.started is True


def test_stop_reddens_readiness_then_reports_whether_it_drained() -> None:
    lifecycle, reporter, shutdown = _lifecycle()
    lifecycle.start()

    drained = lifecycle.stop(timeout=0.2)

    assert drained is True
    assert reporter.draining is True
    assert reporter.ready().healthy is False
    assert shutdown.requested.is_set()


def test_stop_reports_FALSE_when_a_request_is_still_in_flight() -> None:
    """Recording is not reporting: a drain that timed out must SAY so."""
    lifecycle, _reporter, shutdown = _lifecycle()
    lifecycle.start()

    with shutdown.track_request():
        assert lifecycle.stop(timeout=0.1) is False


def test_stop_is_idempotent() -> None:
    lifecycle, _reporter, _shutdown = _lifecycle()
    lifecycle.start()

    assert lifecycle.stop(timeout=0.2) is True
    assert lifecycle.stop(timeout=0.2) is True
