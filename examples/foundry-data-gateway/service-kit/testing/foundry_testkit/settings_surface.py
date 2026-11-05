"""Which environment variables a `BaseSettings` class actually reads.

WHY THIS IS IN THE KIT RATHER THAN IN ONE SERVICE'S TESTS. Two places need the answer
and they need the SAME answer: a config test that clears every variable before asserting
a default, and a test-support factory that must build a config insensitive to whatever
the developer's shell holds. Two hand-maintained rosters that agree today is the shape
this estate keeps paying for -- `.claude/CLAUDE.md` records it as five entrypoint copies,
as a rule count that read "Nine" beside twenty-two rules, and as a table of owned tables
written at the default prefix. The roster here is DERIVED from `model_fields`, so a
field added tomorrow is covered without anyone remembering.

WHAT MAKES THE DERIVATION NON-OBVIOUS, measured against pydantic-settings 2.15.0 and
pydantic 2.13.5 rather than reasoned about:

    field with no alias                 -> env_prefix + FIELD_NAME, upper-cased
    validation_alias="STR"              -> STR, and the prefix does NOT apply
    validation_alias=AliasChoices(A, B) -> both A and B, prefix does NOT apply
    alias="STR"                         -> STR, prefix does NOT apply

The prefix clause is the one worth stating: an explicit alias is used verbatim, so a
roster that prefixed it would clear a name nothing reads and leave the real one set.

WHAT IT CANNOT TELL YOU. It reads the DECLARATION, so it reports the names somebody
wrote down. A name nobody wrote down being read anyway is a different question and
needs a different arm -- that was exactly the `populate_by_name=True` defect, where the
environment source was handed each aliased field's own name as an extra alias, so `ENV`
reached a field declared only as `SERVICE_ENV`. A test that clears this set would not
have cleared `ENV`. Keep the negative arm; this function is not a substitute for it.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings

__all__ = ["environment_variables_read"]


def environment_variables_read(settings_class: type[BaseSettings]) -> set[str]:
    """Every environment variable `settings_class` resolves, from the class itself."""
    prefix = settings_class.model_config.get("env_prefix", "") or ""
    names: set[str] = set()
    for name, field in settings_class.model_fields.items():
        alias = field.validation_alias
        if alias is None:
            names.add(f"{prefix}{name}".upper())
        elif isinstance(alias, str):
            names.add(alias.upper())
        else:
            names.update(str(choice).upper() for choice in alias.choices)
    return names
