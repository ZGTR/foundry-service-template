"""Own one request transaction over a thread-local unit-of-work session."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from foundry_data_gateway.ports import TransactionalUnitOfWork


class RequestTransaction:
    """Commit or roll back, then close, the current request thread's session."""

    def __init__(self, unit_of_work: TransactionalUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    @contextmanager
    def enter(self) -> Iterator[None]:
        try:
            yield
        except Exception:
            self._unit_of_work.rollback()
            raise
        else:
            self._unit_of_work.commit()
