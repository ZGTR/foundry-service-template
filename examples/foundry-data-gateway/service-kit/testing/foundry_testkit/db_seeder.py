"""JSON-fixture database seeding, mirroring the backend's seeder shape exactly.

A fixture is a list of blocks:

    [{"target_class": "module:Class", "data": [{...}, {...}]}]

That is byte-for-byte the shape `an older backend service`'s
`src/database/seeds/mysql_seeder.py` loads and
`src/tests/factories/content_hierarchy.py` builds, deliberately: an estate with
two fixture dialects has two loaders, two sets of edge cases and one of them
untested. The backend resolves `target_class` to a SQLAlchemy model and hands it
to `ResolvingSeeder`; this template has no ORM, so it resolves the same string to
a DOMAIN class and writes through the REAL repository inside the REAL unit of
work. Same fixture shape, same `module:Class` reference, different writer.

Writing through the repository rather than raw SQL is the point. A seeder with
its own INSERT statements is a second schema authority: it keeps passing while
the repository's column list moves underneath it, and the tests it feeds go on
proving something about a table nothing reads any more.

REFUSES rather than skips. A malformed block, an unresolvable `target_class`, a
class with no registered writer, a row whose keys do not match the class: each
is a `SeedError`. A seeder that silently ignores what it cannot place reports a
green seed of an empty database, which is the failure this file exists to rule
out -- and the one that is invisible, because an empty database makes the tests
that read it fail somewhere else entirely.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, field_validator

__all__ = [
    "FixtureBlock",
    "FixtureFile",
    "JsonFixtureSeeder",
    "SeedBlock",
    "SeedError",
    "load_seed_blocks",
    "resolve_target_class",
]


class SeedError(RuntimeError):
    """A fixture could not be loaded or placed. Never swallowed."""


@dataclass(frozen=True, slots=True)
class SeedBlock:
    """One `{"target_class": ..., "data": [...]}` block."""

    target_class: str
    data: tuple[Mapping[str, Any], ...]


class FixtureBlock(BaseModel):
    """The block shape, DECLARED, instead of an isinstance ladder that re-derives it.

    `extra="forbid"` is one refusal this model ADDS, and it is the same class as every
    refusal already here: a third key in a block does nothing today and reads as
    configured, so `{"target_class": ..., "data": [...], "trunacte": true}` is a typo
    that a lenient model accepts in silence. Measured before choosing it -- the one real
    fixture in this template, `tests/fixtures/widgets.json`, carries exactly the two
    keys, so nothing legitimate is refused by it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_class: str
    data: list[dict[str, Any]]

    @field_validator("target_class")
    @classmethod
    def _must_name_a_module_and_a_class(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError(f"'target_class' must be 'module:Class', got {value!r}")
        return value


class FixtureFile(RootModel[Annotated[list[FixtureBlock], Field(min_length=1)]]):
    """A fixture is a NON-EMPTY list of blocks.

    `min_length=1` is load-bearing and is the one thing a pydantic model does not give
    you for free: `list[FixtureBlock]` accepts `[]` happily, and an empty fixture seeds
    nothing while reading as a successful seed -- the "control that tests nothing" shape
    one layer down, and the reason the hand-written parser refused it explicitly.
    """


#: pydantic's own messages are correct and say nothing a fixture author can act on
#: ("Input should be a valid list"), so each error is translated back to the sentence
#: this file already used. The SUBSTRINGS are the contract: five arms in
#: `tests/integration/test_db_seeder.py` match on them, so a translation that drops one
#: turns those arms red rather than passing quietly with a new wording.
def _seed_error(origin: str, error: Mapping[str, Any]) -> str:
    kind, location = error["type"], error["loc"]
    block = location[0] if location and isinstance(location[0], int) else None
    where = f"{origin}: block {block}" if block is not None else origin

    if block is None:
        if kind == "too_short":
            return f"{origin}: seed fixture is empty; it would seed nothing at exit 0"
        got = error["input"].__class__.__name__
        return f"{origin}: a seed fixture is a LIST of blocks, got {got}"
    field = location[1] if len(location) > 1 else None
    if kind == "missing":
        return f"{where} is missing ['{field}']"
    if kind == "extra_forbidden":
        return (
            f"{where} carries unknown key '{field}'. A block is exactly "
            f"'target_class' and 'data'; anything else is a typo that would change "
            f"nothing while reading as configured"
        )
    if field is None:
        return f"{where} is {error['input'].__class__.__name__}, expected an object"
    if field == "data":
        return f"{where} 'data' must be a list of objects"
    return f"{where} {error['msg'].removeprefix('Value error, ')}"


def load_seed_blocks(path: Path | str) -> list[SeedBlock]:
    """Parse a fixture file into blocks, refusing anything that is not the shape."""
    source = Path(path)
    if not source.is_file():
        raise SeedError(f"seed fixture not found: {source}")
    try:
        payload = json.loads(source.read_text())
    except json.JSONDecodeError as exc:
        raise SeedError(f"{source} is not valid JSON: {exc}") from exc
    return parse_seed_blocks(payload, origin=str(source))


def parse_seed_blocks(payload: object, *, origin: str = "<memory>") -> list[SeedBlock]:
    """Validate with `FixtureFile`, then hand back the frozen `SeedBlock`s.

    `SeedBlock` stays the type the seeder consumes. Returning `FixtureBlock` instead
    would have been a smaller diff and a worse one: `data` is a `tuple` of read-only
    mappings on `SeedBlock` and a `list` of mutable dicts on the pydantic model, so a
    writer that mutated a row would be mutating the fixture every later block reads.
    The model is the validator; the dataclass is still the value.
    """
    try:
        validated = FixtureFile.model_validate(payload)
    except ValidationError as invalid:
        # The FIRST error only. Reporting every error at once would be friendlier and
        # would also change what the caller sees for a payload that is wrong in two
        # ways -- the existing arms assert one sentence, and a fixture wrong in two
        # places is wrong in the first place too.
        raise SeedError(_seed_error(origin, invalid.errors()[0])) from invalid
    return [
        SeedBlock(target_class=block.target_class, data=tuple(block.data))
        for block in validated.root
    ]


def resolve_target_class(target_class: str) -> type:
    """Resolve `"module:Class"` to the class object, or refuse."""
    module_name, _, class_name = target_class.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise SeedError(f"target_class {target_class!r}: cannot import {module_name!r}") from exc
    resolved = getattr(module, class_name, None)
    if not isinstance(resolved, type):
        raise SeedError(
            f"target_class {target_class!r}: {class_name!r} is not a class in {module_name}"
        )
    return resolved


_COERCERS: Mapping[type, Callable[[Any], Any]] = {datetime: datetime.fromisoformat}


def _coerce(row: Mapping[str, Any], target: type) -> dict[str, Any]:
    """Turn JSON scalars into the field types the domain class declares.

    Only the conversions JSON cannot express are done here -- today that is
    `datetime` from an ISO-8601 string. Everything else is passed through, so an
    unexpected key reaches the constructor and raises there rather than being
    dropped silently by this function.
    """
    annotations = getattr(target, "__annotations__", {})
    coerced: dict[str, Any] = {}
    for key, value in row.items():
        annotation = annotations.get(key)
        coercer = None
        if isinstance(annotation, str):
            coercer = _COERCERS.get(datetime) if annotation.startswith("datetime") else None
        elif annotation in _COERCERS:
            coercer = _COERCERS[annotation]
        coerced[key] = coercer(value) if coercer is not None and isinstance(value, str) else value
    return coerced


class JsonFixtureSeeder:
    """Loads fixture blocks into a REAL database through REAL repositories.

    `writers` maps a resolved domain class to the call that persists one
    instance -- in practice a repository's `add`. The seeder never writes SQL
    and never learns which backend it is feeding, so the SAME fixture seeds the
    SQLite adapter, the MySQL adapter and the in-memory fake.
    """

    def __init__(
        self,
        *,
        writers: Mapping[type, Callable[[Any], None]],
        commit: Callable[[], None] | None = None,
    ) -> None:
        if not writers:
            raise SeedError(
                "JsonFixtureSeeder needs at least one writer; with none it seeds nothing"
            )
        self._writers = dict(writers)
        self._commit = commit

    def seed(self, blocks: Iterable[SeedBlock]) -> dict[str, int]:
        """Seed every block and return `{target_class: rows_written}`.

        The count is the caller's positive control: a seed that reports zero
        rows for a block it was handed is a failed seed wearing a success, and
        `tests/integration/test_db_seeder.py` asserts the count against a
        HAND-WRITTEN literal rather than against the fixture it just read.
        """
        written: dict[str, int] = {}
        materialised = list(blocks)
        if not materialised:
            raise SeedError(
                "seed() was given no blocks; it would report success having done nothing"
            )
        for block in materialised:
            target = resolve_target_class(block.target_class)
            writer = self._writers.get(target)
            if writer is None:
                raise SeedError(
                    f"no writer registered for {block.target_class}; registered: "
                    f"{sorted(cls.__name__ for cls in self._writers)}"
                )
            for row in block.data:
                try:
                    instance = target(**_coerce(row, target))
                except TypeError as exc:
                    raise SeedError(
                        f"{block.target_class}: row {dict(row)} does not fit: {exc}"
                    ) from exc
                writer(instance)
            written[block.target_class] = written.get(block.target_class, 0) + len(block.data)
        if self._commit is not None:
            self._commit()
        return written

    def seed_files(self, paths: Sequence[Path | str]) -> dict[str, int]:
        blocks: list[SeedBlock] = []
        for path in paths:
            blocks.extend(load_seed_blocks(path))
        return self.seed(blocks)
