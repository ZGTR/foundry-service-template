"""Proves `adapters/real/sqs_event_bus.py` imports cleanly with `boto3` absent,
and that only *constructing* `SqsPublisher` in that state fails, with the
package's own error type rather than a raw `ImportError` leaking out of a
vendor SDK.

Mechanism: setting `sys.modules["boto3"] = None` makes Python's import system
treat `boto3` as blocked (a standard stdlib testing idiom), so reloading the
module under it exercises exactly the "boto3 is not installed" branch without
actually uninstalling anything from this venv.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from foundry_contracts.errors import ConfigurationError

import foundry_data_gateway.adapters.real.sqs_event_bus as sqs_event_bus


@pytest.fixture
def boto3_blocked():
    # Restored explicitly in the `finally` below, in this exact order, rather
    # than via `monkeypatch`: monkeypatch's own sys.modules undo runs AFTER
    # this fixture's teardown code (LIFO relative to setup), so relying on it
    # would reload the module while boto3 is still blocked.
    saved = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == "boto3" or name.startswith("boto3.")
    }
    for name in saved:
        del sys.modules[name]
    sys.modules["boto3"] = None
    reloaded = importlib.reload(sqs_event_bus)
    try:
        yield reloaded
    finally:
        del sys.modules["boto3"]
        sys.modules.update(saved)
        importlib.reload(sqs_event_bus)  # restore the module to its real-boto3 state


def test_module_imports_without_boto3(boto3_blocked) -> None:
    assert boto3_blocked._boto3 is None


def test_constructing_publisher_without_boto3_raises_configuration_error(boto3_blocked) -> None:
    with pytest.raises(ConfigurationError, match="aws"):
        boto3_blocked.SqsPublisher(queue_url="https://sqs.example/queue")


def test_module_uses_real_boto3_once_available() -> None:
    """Sanity check that the fixture's teardown actually restored the module,
    so this test (and everything after it) sees the real SDK again."""
    assert sqs_event_bus._boto3 is not None
