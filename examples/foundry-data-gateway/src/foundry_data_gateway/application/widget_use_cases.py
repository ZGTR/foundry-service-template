"""The widget resource's use cases, as one injectable bundle.

Not a use case itself and deliberately behaviour-free: it is a frozen record so
a controller takes ONE constructor argument instead of three, without anybody
reaching for a service locator to avoid the wiring. The composition root builds
it; `scripts/check_mvc_separation.py` still sees three separate use-case files,
because that is what §1 asks for and this record does not merge them.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_data_gateway.application.create_widget import CreateWidget
from foundry_data_gateway.application.get_widget import GetWidget
from foundry_data_gateway.application.list_widgets import ListWidgets


@dataclass(frozen=True, slots=True)
class WidgetUseCases:
    create: CreateWidget
    get: GetWidget
    list: ListWidgets
