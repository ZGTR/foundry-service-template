"""Transaction lifecycle required by the standalone service application.

The shared ``UnitOfWork`` contract deliberately exposes only a borrowed session.
This service owns its request transaction, so it depends on this narrower local
extension when it must commit or roll back.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from foundry_contracts.unit_of_work import UnitOfWork


class TransactionalUnitOfWork(UnitOfWork, Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class TransactionBoundary(Protocol):
    """Runtime-checkable like every other port here. Without it an adapter can only be
    checked by duck-typing at the call site, which is where a missing method surfaces as
    an AttributeError mid-request instead of a failed assertion in a test."""


    def enter(self) -> AbstractContextManager[None]: ...
