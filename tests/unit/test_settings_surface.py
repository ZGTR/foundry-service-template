"""`environment_variables_read()` against every field shape, not just this service's.

WHY THIS FILE EXISTS RATHER THAN RELYING ON `test_config.py`. That file asserts the
derivation agrees with `ServiceConfig`, which today declares an empty `env_prefix` and
not one alias -- so it exercises exactly ONE of the function's four branches. The other
three are the ones a SECOND consumer will hit first: `tests/support/environment.py` is
about to clear this set before building a config, and a service that adopts a prefix or
an alias would have it clear names nothing reads while leaving the real ones set.

The expected values are hand-written, and each one was MEASURED against
pydantic-settings 2.15.0 / pydantic 2.13.5 by setting the candidate variable and asking
whether the field took it -- `probe_alias_semantics`, 12 cells. The prefix clause is the
one worth stating twice: an explicit alias is read VERBATIM, so a derivation that
prefixed it would be wrong in the direction that is silent.
"""

from __future__ import annotations

from foundry_testkit import environment_variables_read
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class _Prefixed(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SVC_")

    plain: str = "d"
    aliased_choices: str = Field(default="d", validation_alias=AliasChoices("FIRST", "SECOND"))
    aliased_string: str = Field(default="d", validation_alias="STR_ALIAS")
    plain_alias: str = Field(default="d", alias="PLAIN_ALIAS")


class _Unprefixed(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    plain: str = "d"


class _NoPrefixDeclared(BaseSettings):
    plain: str = "d"


def test_every_field_shape_resolves_to_the_name_pydantic_actually_reads() -> None:
    assert environment_variables_read(_Prefixed) == {
        # No alias: the prefix applies.
        "SVC_PLAIN",
        # An explicit alias is verbatim. `SVC_FIRST` is NOT read, and a derivation
        # that produced it would clear the wrong variable and leave `FIRST` set.
        "FIRST",
        "SECOND",
        "STR_ALIAS",
        # `alias=` with no `validation_alias=`: pydantic populates the latter from it,
        # and pydantic-settings reads it verbatim too.
        "PLAIN_ALIAS",
    }


def test_an_empty_prefix_and_no_declared_prefix_agree() -> None:
    """`model_config.get("env_prefix", "")` has two ways to be absent.

    Declaring `env_prefix=""` and declaring nothing must give the same answer, or the
    function would depend on which of two equivalent spellings a service happened to
    use -- and `ServiceConfig` uses the explicit one, so the implicit branch would be
    the unexercised half.
    """
    assert environment_variables_read(_Unprefixed) == {"PLAIN"}
    assert environment_variables_read(_NoPrefixDeclared) == {"PLAIN"}


def test_the_derivation_is_not_merely_the_field_names_upper_cased() -> None:
    """The control that makes the test above mean something.

    A function that returned `{name.upper() for name in model_fields}` would pass a
    single-field unprefixed fixture and be wrong for every other shape. Asserting the
    two sets DIFFER is what rules that out.
    """
    naive = {name.upper() for name in _Prefixed.model_fields}
    assert environment_variables_read(_Prefixed) != naive
    assert naive == {"PLAIN", "ALIASED_CHOICES", "ALIASED_STRING", "PLAIN_ALIAS"}
