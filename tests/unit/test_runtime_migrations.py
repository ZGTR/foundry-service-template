"""§6: migration application at startup is EXPLICIT and SINGLE-HEADED."""

from __future__ import annotations

import pytest
from foundry_contracts.errors import ConfigurationError

from foundry_service_template.runtime import MigrationRunner, assert_single_head


def test_one_head_resolves() -> None:
    assert assert_single_head(["rev_0003"]) == "rev_0003"


def test_two_heads_are_REFUSED_rather_than_one_lineage_applied() -> None:
    """The failure is not that a migration errors -- it is that one lineage is
    applied silently and the database ends up in a state no branch describes."""
    with pytest.raises(ConfigurationError, match="2 heads"):
        assert_single_head(["rev_a", "rev_b"])


def test_zero_heads_is_as_wrong_as_two() -> None:
    with pytest.raises(ConfigurationError, match="NO head"):
        assert_single_head([])


def test_a_duplicate_head_is_still_one_head() -> None:
    assert assert_single_head(["rev_1", "rev_1"]) == "rev_1"


def test_disabled_means_nothing_is_applied_and_the_reason_is_recorded() -> None:
    applied: list[str] = []
    runner = MigrationRunner(
        enabled=False, heads=lambda: ["rev_1"], upgrade=lambda head: applied.append(head)
    )

    outcome = runner.run()

    assert outcome.applied is False
    assert applied == []
    assert "MIGRATE_ON_STARTUP" in outcome.reason


def test_enabled_applies_the_single_head_exactly_once() -> None:
    applied: list[str] = []
    runner = MigrationRunner(
        enabled=True, heads=lambda: ["rev_9"], upgrade=lambda head: applied.append(head)
    )

    first = runner.run()
    second = runner.run()

    assert applied == ["rev_9"], "a second call must not re-apply"
    assert first == second


def test_the_head_is_asserted_BEFORE_anything_is_applied() -> None:
    """Order matters: checking after applying means the damage is already done."""
    applied: list[str] = []
    runner = MigrationRunner(
        enabled=True, heads=lambda: ["a", "b"], upgrade=lambda head: applied.append(head)
    )

    with pytest.raises(ConfigurationError):
        runner.run()

    assert applied == []


def test_the_refusal_names_a_remedy_a_DEPLOYMENT_can_perform() -> None:
    """A refusal reachable from a task definition must not say "edit your call site".

    An ECS task and a Lambda have no call site to edit, so a message that only
    offers a constructor argument hands every failing deployment the one
    instruction it cannot follow.
    """
    with pytest.raises(ConfigurationError) as refused:
        assert_single_head(["a", "b"])

    assert "MIGRATE_ON_STARTUP" in str(refused.value)
