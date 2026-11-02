"""§6: the three probes have DISTINCT semantics. One test per sentence.

Collapsing any two of these is the defect: a liveness probe that consults a
dependency turns a database blip into a restart storm, and a startup probe that
can go back to "no" gets the process KILLED mid-drain instead of drained.
"""

from __future__ import annotations

from foundry_service_template.adapters.fake import FakeReadinessCheck
from foundry_service_template.runtime import NamedReadinessProbe, RuntimeHealthReporter


def _reporter(*, ready: bool = True) -> RuntimeHealthReporter:
    return RuntimeHealthReporter(
        probes=(NamedReadinessProbe("db", FakeReadinessCheck(ready=ready)),)
    )


def test_liveness_never_consults_a_dependency() -> None:
    reporter = _reporter(ready=False)
    reporter.mark_started()

    assert reporter.live().healthy is True
    assert reporter.ready().healthy is False


def test_readiness_is_false_before_startup_completes() -> None:
    """A process that has not finished starting cannot serve, even if its
    dependencies are all reachable."""
    reporter = _reporter(ready=True)

    assert reporter.ready().healthy is False
    assert reporter.ready().probes[0].name == "startup"


def test_readiness_becomes_true_once_startup_latches() -> None:
    reporter = _reporter(ready=True)

    reporter.mark_started()

    assert reporter.ready().healthy is True
    assert [p.name for p in reporter.ready().probes] == ["db"]


def test_startup_latches_and_never_goes_back() -> None:
    reporter = _reporter(ready=True)
    reporter.mark_started()

    reporter.draining = True

    # Readiness moves; startup does NOT. "Startup finished" is a fact about the
    # past, and an orchestrator reading a red startup probe kills the process.
    assert reporter.ready().healthy is False
    assert reporter.startup().healthy is True
    assert reporter.live().healthy is True


def test_mark_started_is_idempotent() -> None:
    reporter = _reporter()

    reporter.mark_started()
    reporter.mark_started()

    assert reporter.startup().healthy is True


def test_a_probe_that_raises_is_reported_as_UNHEALTHY_not_swallowed() -> None:
    """Recording is not reporting.

    An observer that catches a failure and lets the verdict stay green is the
    most expensive artifact in the estate: a detailed, plausible, green report of
    a failed run. Here the exception is caught -- an endpoint must not 500 -- but
    the VERDICT flips, which is the half that makes catching it legitimate.
    """

    class Exploding:
        def check(self) -> bool:
            raise RuntimeError("connection refused")

    reporter = RuntimeHealthReporter(probes=(NamedReadinessProbe("db", Exploding()),))
    reporter.mark_started()

    report = reporter.ready()

    assert report.healthy is False
    assert report.probes[0].detail == "RuntimeError"


def test_readiness_aggregates_every_probe_and_one_red_is_enough() -> None:
    reporter = RuntimeHealthReporter(
        probes=(
            NamedReadinessProbe("db", FakeReadinessCheck(ready=True)),
            NamedReadinessProbe("queue", FakeReadinessCheck(ready=False)),
        )
    )
    reporter.mark_started()

    report = reporter.ready()

    assert report.healthy is False
    assert [(p.name, p.healthy) for p in report.probes] == [("db", True), ("queue", False)]


def test_a_probe_must_carry_a_name() -> None:
    """An unnamed red probe tells an operator nothing about where to look."""
    import pytest

    with pytest.raises(ValueError, match="non-empty"):
        NamedReadinessProbe("", FakeReadinessCheck())
