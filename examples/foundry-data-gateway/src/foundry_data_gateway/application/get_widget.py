"""Use case: read one widget, scoped to the caller's org."""

from __future__ import annotations

from contextlib import nullcontext

from foundry_contracts.principal import CallerPrincipalV1

from foundry_data_gateway.domain import Widget, WidgetNotFound
from foundry_data_gateway.ports import TransactionBoundary, WidgetRepository


class GetWidget:
    def __init__(
        self,
        *,
        repository: WidgetRepository,
        transaction: TransactionBoundary | None = None,
    ) -> None:
        self._repository = repository
        self._transaction = transaction

    def execute(self, principal: CallerPrincipalV1, *, widget_id: str) -> Widget:
        principal.require("widgets:read")
        boundary = nullcontext() if self._transaction is None else self._transaction.enter()
        with boundary:
            widget = self._repository.get(principal.org_id, widget_id)
        if widget is None:
            raise WidgetNotFound(f"no widget {widget_id!r} for org {principal.org_id!r}")
        return widget
