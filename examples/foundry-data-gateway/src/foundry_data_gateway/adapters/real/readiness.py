"""The real `ReadinessCheck`: can the widget repository actually be reached.

Deliberately cheap -- one lightweight round-trip, not a full query -- because
`/readyz` is polled frequently and must never become the bottleneck it is
supposed to detect.
"""

from __future__ import annotations

from contextlib import nullcontext

from foundry_data_gateway.ports import TransactionBoundary, WidgetRepository


class RepositoryReadinessCheck:
    def __init__(
        self,
        repository: WidgetRepository,
        transaction: TransactionBoundary | None = None,
    ) -> None:
        self._repository = repository
        self._transaction = transaction

    def check(self) -> bool:
        try:
            boundary = nullcontext() if self._transaction is None else self._transaction.enter()
            with boundary:
                self._repository.list_for_org("__readyz_probe__")
        except Exception:  # noqa: BLE001 -- any failure means "not ready"
            return False
        return True
