"""Alembic, behind the two callables `runtime.MigrationRunner` asks for.

WHAT THIS REPLACES, and why the replacement is the point. `build_migrations()`
used to hand `MigrationRunner` a pair of callables that REFUSED, with a message
saying "this template ships no alembic tree". That was the honest answer while
it was true. This slice adds `migrations/`, so leaving the refusal in place
would mean a service that declares `MIGRATE_ON_STARTUP=true`, carries a
revision tree, and still dies at startup telling the operator to supply one --
a sentence about the code that the code has made false, which is the class of
defect this estate refuses to ship.

`heads()` IS READ FROM THE SCRIPT DIRECTORY, NOT FROM THE DATABASE, and the
distinction is what `MigrationRunner` needs. It asks for the head count BEFORE
applying anything, because two heads is what a merge produces and the failure
is not an error -- it is one lineage applied silently and a database in a state
no branch describes. `ScriptDirectory.get_heads()` answers that from the
revision files, which is the only place the question can be answered before a
migration runs.

THE CONFIG IS BUILT IN MEMORY AND CARRIES ONLY `script_location`. It sets no
`sqlalchemy.url`, deliberately, and `alembic.ini` leaves that key empty for the
same reason: `migrations/env.py` resolves the database through
`ServiceConfig.from_environment()`, so there is exactly ONE authority for which
database gets migrated whether the caller is this class or the `alembic`
command line. Setting it here as well would put a second answer in the config
object -- and `set_main_option` percent-unescapes its value, so a URL-encoded
character in a credential would come out of that second path subtly wrong
while the first path was right. Two authorities for one connection string is
how a deployment migrates one database and serves another.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from foundry_contracts.errors import ConfigurationError

__all__ = ["AlembicMigrations"]


class AlembicMigrations:
    """The `heads`/`upgrade` pair for one service's own revision tree."""

    def __init__(self, *, script_location: str) -> None:
        self._script_location = script_location

    def _config(self) -> Config:
        location = Path(self._script_location)
        if not (location / "env.py").is_file():
            raise ConfigurationError(
                f"MIGRATIONS_DIR={self._script_location!r} holds no env.py, so there is no "
                "alembic tree to apply. Point MIGRATIONS_DIR at this service's migrations "
                "directory (it is `migrations` relative to the image's /app), or set "
                "MIGRATE_ON_STARTUP=false to start without migrating."
            )
        config = Config()
        config.set_main_option("script_location", str(location))
        return config

    def heads(self) -> tuple[str, ...]:
        """Every head in the revision tree, read from the files, before applying."""
        return tuple(ScriptDirectory.from_config(self._config()).get_heads())

    def upgrade(self, head: str) -> None:
        """Apply up to `head`. `MigrationRunner` has already asserted it is the only one."""
        command.upgrade(self._config(), head)
