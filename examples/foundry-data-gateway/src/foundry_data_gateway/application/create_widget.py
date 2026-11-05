"""Use case: create a widget for the caller's org and publish the fact."""

from __future__ import annotations

from contextlib import nullcontext

from foundry_contracts.ids import new_id
from foundry_contracts.principal import CallerPrincipalV1

from foundry_data_gateway.application.validate_widget_name import validate_widget_name
from foundry_data_gateway.domain import Widget, widget_created
from foundry_data_gateway.ports import (
    Clock,
    Publisher,
    TransactionBoundary,
    WidgetRepository,
)


class CreateWidget:
    def __init__(
        self,
        *,
        repository: WidgetRepository,
        publisher: Publisher,
        clock: Clock,
        transaction: TransactionBoundary | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._clock = clock
        self._transaction = transaction

    def execute(self, principal: CallerPrincipalV1, *, name: str) -> Widget:
        principal.require("widgets:write")
        widget = Widget(
            widget_id=new_id("widget"),
            org_id=principal.org_id,
            name=validate_widget_name(name),
            created_at=self._clock.now(),
        )
        boundary = nullcontext() if self._transaction is None else self._transaction.enter()
        with boundary:
            self._repository.add(widget)
            self._publisher.publish(widget_created(widget, self._clock))
        return widget
