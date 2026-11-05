"""`domain` -- invariants, values, entities, policy, domain events, domain errors.

THE PUBLIC CONTRACT of this submodule. Every other submodule imports domain
types from HERE (`from foundry_data_gateway.domain import Widget`), never
from a deep path (`...domain.model.widget`). The contract is §1 of
the service module contract (README.md, "Service module contract"), and
`scripts/check_layering.py` enforces both halves: the dependency direction and
the "no deep cross-submodule import" rule.

This submodule imports NOTHING but stdlib, `foundry_contracts` (shared
protocols, by estate rule) and itself. No framework, no SDK, no adapter, no
port. That is what makes the domain testable with nothing running.
"""

from __future__ import annotations

from foundry_data_gateway.domain.errors import (
    InvalidToolPack,
    InvalidWidgetName,
    ToolRefused,
    WidgetNotFound,
)
from foundry_data_gateway.domain.events.data_tool_events import DATA_TOOL_CALLED, data_tool_called
from foundry_data_gateway.domain.events.event import Event, EventHandler
from foundry_data_gateway.domain.events.widget_events import WIDGET_CREATED, widget_created
from foundry_data_gateway.domain.model.widget import Widget
from foundry_data_gateway.domain.tool_arguments import bind_arguments
from foundry_data_gateway.domain.tool_pack import (
    Example,
    Parameter,
    Scope,
    Source,
    Tool,
    ToolPack,
    merge_packs,
    parse_pack,
)
from foundry_data_gateway.domain.tool_policy import Grant, ToolPolicy

__all__ = [
    "DATA_TOOL_CALLED",
    "Example",
    "Grant",
    "InvalidToolPack",
    "Parameter",
    "Scope",
    "Source",
    "Tool",
    "ToolPack",
    "ToolPolicy",
    "ToolRefused",
    "bind_arguments",
    "data_tool_called",
    "merge_packs",
    "parse_pack",
    "WIDGET_CREATED",
    "Event",
    "EventHandler",
    "InvalidWidgetName",
    "Widget",
    "WidgetNotFound",
    "widget_created",
]
