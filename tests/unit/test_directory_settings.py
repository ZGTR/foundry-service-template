"""A callee URL under the service directory's variable, an older name for one release.

The directory variable wins when both are set, the legacy name
alone still works and logs ONE deprecation warning naming both, an empty value counts
as unset, and neither set returns None so the caller decides whether that refuses --
with a remedy naming the directory variable and where its value comes from.
"""

from __future__ import annotations

import logging

import pytest

from foundry_service_template.composition.directory_settings import (
    DIRECTORY_SOURCE,
    directory_url,
    missing_callee_remedy,
)

NEW = "https://new.directory.invalid"
OLD = "https://old.legacy.invalid"
LOGGER = "foundry_service_template.composition.directory_settings"


def _deprecations(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


def test_directory_variable_wins_over_the_legacy_one(caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER)

    got = directory_url("FOUNDRY_CALLEE_URL", NEW, "CALLEE_URL", OLD)

    assert got == NEW
    assert _deprecations(caplog) == []


def test_legacy_variable_alone_still_works_and_warns_once(caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER)

    got = directory_url("FOUNDRY_CALLEE_URL", None, "CALLEE_URL", OLD)

    assert got == OLD
    (warning,) = _deprecations(caplog)
    assert "CALLEE_URL is deprecated" in warning
    assert "FOUNDRY_CALLEE_URL" in warning
    assert DIRECTORY_SOURCE in warning


def test_an_empty_directory_value_is_unset(caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER)

    assert directory_url("FOUNDRY_CALLEE_URL", "", "CALLEE_URL", OLD) == OLD
    assert directory_url("FOUNDRY_CALLEE_URL", "", "CALLEE_URL", "") is None


def test_neither_variable_returns_none_without_a_warning(caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER)

    assert directory_url("FOUNDRY_CALLEE_URL", None, "CALLEE_URL", None) is None
    assert _deprecations(caplog) == []


def test_the_remedy_names_the_variable_and_the_directory() -> None:
    remedy = missing_callee_remedy("FOUNDRY_CALLEE_URL")

    assert "FOUNDRY_CALLEE_URL" in remedy
    assert "scripts/service_directory.yaml" in remedy
    assert "build_container" not in remedy
