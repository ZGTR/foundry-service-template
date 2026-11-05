"""`application` -- use cases. Sequencing only.

THE PUBLIC CONTRACT of this submodule. One use case per file (§1); each is a
small class constructed with its collaborators and invoked through `execute`.

This submodule imports `domain` and `ports` and NOTHING else -- no framework,
no SDK, no adapter, no transport, no composition. `scripts/check_layering.py`
fails the build on any of those, which is what lets every use case be tested
with fakes and no process running.
"""

from __future__ import annotations

from foundry_data_gateway.application.create_widget import CreateWidget
from foundry_data_gateway.application.get_widget import GetWidget
from foundry_data_gateway.application.list_widgets import ListWidgets
from foundry_data_gateway.application.validate_widget_name import (
    MAX_NAME_LENGTH,
    validate_widget_name,
)
from foundry_data_gateway.application.widget_use_cases import WidgetUseCases

__all__ = [
    "MAX_NAME_LENGTH",
    "CreateWidget",
    "GetWidget",
    "ListWidgets",
    "WidgetUseCases",
    "validate_widget_name",
]
