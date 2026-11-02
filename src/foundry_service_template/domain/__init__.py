"""`domain` -- invariants, values, entities, policy, domain events, domain errors.

THE PUBLIC CONTRACT of this submodule. Every other submodule imports domain
types from HERE (`from foundry_service_template.domain import Widget`), never
from a deep path (`...domain.model.widget`). The contract is §1 of
the service module contract (README.md, "Service module contract"), and
`scripts/check_layering.py` enforces both halves: the dependency direction and
the "no deep cross-submodule import" rule.

This submodule imports NOTHING but stdlib, `foundry_contracts` (shared
protocols, by estate rule) and itself. No framework, no SDK, no adapter, no
port. That is what makes the domain testable with nothing running.
"""

from __future__ import annotations

from foundry_service_template.domain.errors import InvalidWidgetName, WidgetNotFound
from foundry_service_template.domain.events.event import Event, EventHandler
from foundry_service_template.domain.events.widget_events import WIDGET_CREATED, widget_created
from foundry_service_template.domain.model.widget import Widget

__all__ = [
    "WIDGET_CREATED",
    "Event",
    "EventHandler",
    "InvalidWidgetName",
    "Widget",
    "WidgetNotFound",
    "widget_created",
]
