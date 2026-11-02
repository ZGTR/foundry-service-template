"""Conforming migration application, in vocabulary unlike the real module's.

`SchemaUpgrade` rather than `MigrationRunner`, `only_head` rather than
`assert_single_head`. The conforming fixture is a COMPLETE service: it was
missing this clause when check_lifecycle first ran, and the fix was to add the
behaviour rather than to soften the matcher -- a fixture reworded until the gate
falls silent is decoration, and it leaves the rule unenforced.
"""

from collections.abc import Callable, Sequence


def only_head(revisions: Sequence[str]) -> str:
    """Return the single head revision, or refuse.

    Two heads is what a merge produces; applying one lineage silently leaves the
    database in a state no branch describes.
    """
    unique = list(dict.fromkeys(revisions))
    if len(unique) != 1:
        raise RuntimeError(
            f"expected exactly one head revision, found {unique}. "
            "Set MIGRATE_ON_STARTUP=false to start without upgrading the schema."
        )
    return unique[0]


class SchemaUpgrade:
    def __init__(self, *, enabled: bool, revisions: Callable[[], Sequence[str]]) -> None:
        self._enabled = enabled
        self._revisions = revisions
        self._done = False

    def apply(self) -> str | None:
        if self._done or not self._enabled:
            return None
        head = only_head(self._revisions())
        self._done = True
        return head
