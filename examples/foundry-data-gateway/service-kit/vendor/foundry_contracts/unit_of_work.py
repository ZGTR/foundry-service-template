"""Transaction boundaries a blackbox borrows rather than owns.

Boundary rule 3, stated precisely: a blackbox may share a database *instance* with
its caller, and may mutate only an enumerated set of its own tables, through a
unit of work the caller supplies.

Sharing the instance is what makes an atomic hand-off possible -- a job row and the
caller's own domain row commit together or not at all. Enumerating the tables is
what stops that sharing from becoming a shared schema: the package declares the
tables it owns, and a conformance test asserts it writes to no others.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class UnitOfWork(Protocol):
    """A transaction the caller opened and the caller will close.

    A blackbox never commits. It enlists in the caller's transaction and lets the
    caller decide the outcome, because only the caller knows whether its own work
    alongside ours also succeeded.
    """

    def session(self) -> Any:
        """Return the underlying session/connection handle for this transaction."""
        ...

    def owned_tables(self) -> frozenset[str]:
        """Return the table names this unit of work permits writes to."""
        ...


class TableOwnershipViolation(Exception):
    """A package attempted to write outside its enumerated tables."""


def assert_writes_within(uow: UnitOfWork, tables: frozenset[str], *, package: str) -> None:
    """Fail loudly when ``package`` would write a table the unit of work does not grant."""
    granted = uow.owned_tables()
    trespass = tables - granted
    if trespass:
        raise TableOwnershipViolation(
            f"{package} would write {sorted(trespass)}, which the unit of work does not own "
            f"(granted: {sorted(granted)})"
        )
