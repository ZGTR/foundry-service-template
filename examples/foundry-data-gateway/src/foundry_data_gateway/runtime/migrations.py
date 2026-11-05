"""§6: migration application at startup is EXPLICIT and SINGLE-HEADED.

Two separate guarantees, and the second is the one that is usually missing.

*Explicit* means startup either applies migrations or it does not, decided by
one declared flag, never by a side effect of importing a module. A service that
migrates because something it imported happened to do so cannot be reasoned
about from its own entrypoint.

*Single-headed* means the revision graph has exactly ONE head before anything
is applied. Two heads is what a merge produces when two branches each add a
revision; the failure is not that the migration errors, it is that it silently
applies ONE of the two lineages and the database ends up in a state no branch
describes. Asserting the head count BEFORE applying is the only moment at which
that is cheap to see -- `.claude/CLAUDE.md`'s live-migration-head rule is the
same invariant asserted from the other end, against a running database.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from foundry_contracts.errors import ConfigurationError

__all__ = ["MigrationOutcome", "MigrationRunner", "assert_single_head"]


@dataclass(frozen=True, slots=True)
class MigrationOutcome:
    applied: bool
    head: str
    reason: str = ""


def assert_single_head(heads: Sequence[str]) -> str:
    """Return the single head, or refuse. Zero heads is as wrong as two."""
    unique = list(dict.fromkeys(heads))
    if len(unique) == 1:
        return unique[0]
    if not unique:
        raise ConfigurationError(
            "the migration graph has NO head; the revision directory is empty or unreadable. "
            "Set MIGRATE_ON_STARTUP=false to start without migrating, or fix the revision path."
        )
    raise ConfigurationError(
        f"the migration graph has {len(unique)} heads ({unique}); refusing to apply one lineage "
        "and leave the database in a state no branch describes. Merge the revisions, then "
        "redeploy. Set MIGRATE_ON_STARTUP=false to start this process without migrating."
    )


class MigrationRunner:
    """Applies migrations at startup, once, behind an explicit flag.

    `enabled` comes from configuration and reaches here as a constructor
    argument -- this module never reads the environment. The refusals name
    `MIGRATE_ON_STARTUP` because a DEPLOYMENT is what reaches them, and a
    deployment cannot edit a call site.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        heads: Callable[[], Sequence[str]],
        upgrade: Callable[[str], None],
    ) -> None:
        self._enabled = enabled
        self._heads = heads
        self._upgrade = upgrade
        self._outcome: MigrationOutcome | None = None

    def run(self) -> MigrationOutcome:
        """Idempotent: a second call returns the first call's outcome unchanged."""
        if self._outcome is not None:
            return self._outcome
        if not self._enabled:
            self._outcome = MigrationOutcome(
                applied=False, head="", reason="MIGRATE_ON_STARTUP is false"
            )
            return self._outcome
        head = assert_single_head(self._heads())
        self._upgrade(head)
        self._outcome = MigrationOutcome(applied=True, head=head)
        return self._outcome
