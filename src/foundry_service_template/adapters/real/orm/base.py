"""The declarative base every table mapping in this service hangs off.

ONE `Base`, and it lives HERE rather than beside the first record that needed
it. `Base.metadata` is what `migrations/env.py` compares the database against,
so a second base is a second half of the schema that alembic autogenerate
cannot see: the tables mapped on it exist in the code, are absent from every
revision, and `alembic upgrade head` succeeds having created none of them.
`service-kit/gates/check_orm_standard.py` clause 2 refuses a `DeclarativeBase`
subclass outside this package for exactly that reason.

THE NAMING CONVENTION IS THE LOAD-BEARING PART, and it is easy to read as
tidiness. Without it, a constraint declared inline gets whatever name the
DATABASE chose -- Postgres names a unique constraint after the table and
columns, SQLite names nothing at all -- so `alembic revision --autogenerate`
compares a named constraint in the metadata against an unnamed one in the
database and emits a drop-and-recreate on every run. Worse, a downgrade that
must `op.drop_constraint("...")` has no name to write, and the migration is
unreversible: `alembic downgrade base` is then a command nobody can run, which
is precisely the arm this slice proves. Declaring the convention makes every
constraint name derivable from the model, in both directions.

`%(column_0_N_name)s` rather than `%(column_0_name)s`: this service's own
primary key is composite (`org_id`, `widget_id`), and the single-column token
would name a two-column index after its first column, so two different indexes
on the same leading column would collide.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

__all__ = ["NAMING_CONVENTION", "Base", "as_utc"]

#: Deterministic constraint names, derived from the model rather than the engine.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The one declarative base. Records are adapter-private; see this package."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def as_utc(value: datetime) -> datetime:
    """Read a `DateTime(timezone=True)` column as the UTC instant it holds.

    EVERY record in this package that stores a moment stores a UTC instant --
    the repositories call `.astimezone(UTC)` on the write path, so that is a
    property of the writes rather than a hope about the callers. This function
    is the corresponding STATEMENT on the read path, and it lives beside the
    naming convention because both are conventions shared by every record
    rather than facts about any one table.

    It is needed because `DateTime(timezone=True)` is honoured by some dialects
    and not others: Postgres stores `timestamptz` and hands back an aware
    value, while SQLite has no timezone-aware type and hands back a NAIVE one.
    A naive value reaching `domain.Widget` is refused outright by its
    `__post_init__`, so without this the same mapping would work on one engine
    and raise on another -- and the template's contract suite asserts the aware
    round trip on a REAL PostgreSQL, where the column type is what answers.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
