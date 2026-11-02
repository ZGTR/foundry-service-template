"""Use case: list every widget belonging to the caller's org, oldest first."""

from __future__ import annotations

from contextlib import nullcontext

from foundry_contracts.principal import CallerPrincipalV1

from foundry_service_template.domain import Widget
from foundry_service_template.ports import TransactionBoundary, WidgetRepository


class ListWidgets:
    def __init__(
        self,
        *,
        repository: WidgetRepository,
        transaction: TransactionBoundary | None = None,
    ) -> None:
        self._repository = repository
        self._transaction = transaction

    def execute(self, principal: CallerPrincipalV1) -> list[Widget]:
        principal.require("widgets:read")
        boundary = nullcontext() if self._transaction is None else self._transaction.enter()
        with boundary:
            return self._repository.list_for_org(principal.org_id)
