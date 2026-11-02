from __future__ import annotations

import pytest
from foundry_testkit import environment_variables_read
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from foundry_service_template.composition.config import ServiceConfig

#: Every variable these tests clear before reading the environment. `_environment()`
#: below is the single call site that clears them and the single one that sets
#: anything, for the mock-budget reason its own docstring gives.
#:
#: It must name EVERY variable the class reads, and
#: `test_the_cleared_tuple_covers_every_field` asserts exactly that against
#: `model_fields`. Without it a field added tomorrow would be read from the
#: developer's own shell during `test_from_environment_defaults`, which passes
#: on the machine where it was written and fails in CI -- or, worse, the other
#: way round.
_CLEARED = (
    "SERVICE_ENV",
    "PORT",
    "PRINCIPAL_ISSUER",
    "DATABASE_URL",
    "DB_PASSWORD_SECRET_ID",
    "EVENT_BUS_MODE",
    "SQS_QUEUE_URL",  # COMMS-FLAVOUR-STRIP-LINE
    "AWS_REGION",
    "AUTH_JWKS_URI",
    "AUTH_AUDIENCE",
    "AWS_ENDPOINT_URL",
    "LOG_LEVEL",
    "SHUTDOWN_GRACE_SECONDS",
    "MIGRATE_ON_STARTUP",
    "MIGRATIONS_DIR",
    "AWS_LWA_PASS_THROUGH_PATH",
    "FOUNDRY_FUNCTION",
)
LOCAL_URL = "sqlite+pysqlite:///widgets.db"


def _environment(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    """Clear every variable `ServiceConfig` reads, then set exactly `values`.

    THE ONE `setenv`/`delenv` CALL SITE IN THIS FILE, deliberately. §7's mock
    budget counts CALL SITES, not calls, so a per-test pair would have pushed
    this file from 6 sites to 12 and demanded the ratchet be raised -- the wrong
    direction, and the reason the previous version of this file set DATABASE_URL
    through a one-entry dict loop rather than its own line. One helper takes the
    file to 2 and lets the budget be LOWERED instead.

    Clearing first is not tidiness. `BaseSettings` reads the environment for any
    field a caller leaves out, so without this every test here would inherit the
    developer's own shell -- passing on the machine where it was written and
    failing in CI, or the other way round.
    """
    for variable in _CLEARED:
        monkeypatch.delenv(variable, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_the_cleared_tuple_covers_every_field() -> None:
    """Fails closed in BOTH directions, so the tuple cannot rot either way."""
    read = environment_variables_read(ServiceConfig)
    cleared = set(_CLEARED)
    assert not (read - cleared), f"fields read but never cleared: {sorted(read - cleared)}"
    assert not (cleared - read), f"cleared but no longer read: {sorted(cleared - read)}"


# ---------------------------------------------------------------------------
# The arm that was missing when `populate_by_name=True` shipped.
#
# `environment_variables_read()` reads the DECLARATION, so it can only
# ever report the names somebody wrote down. The defect was a name nobody wrote
# down being read anyway: `populate_by_name=True` handed the environment source
# each aliased field's own name as an extra alias, so `ENV` reached
# `config.env` and `ISSUER` reached `config.issuer` -- undeclared, invisible to
# `scripts/env_ownership.yaml`, and, because `ENV` is a name shells and CI
# runners commonly set, live.
#
# Every arm in this file at the time was a POSITIVE one: set the declared name,
# assert it takes effect. All four passed. Nothing asked the other question.
# ---------------------------------------------------------------------------


def _undeclared_candidates(settings_cls: type[BaseSettings]) -> dict[str, list[str]]:
    """{field: names that must NOT be read}, derived from the class itself.

    Two sources, because they catch different regressions. The FIELD NAME
    upper-cased, when the field is aliased -- that is exactly the shadow
    `populate_by_name` creates, and it is empty here only because no field is
    aliased any more. And a PREFIXED spelling for every field, which would
    become live if somebody set a non-empty `env_prefix` while the deployment
    kept setting the bare names.
    """
    candidates: dict[str, list[str]] = {}
    for name, field in settings_cls.model_fields.items():
        alias = field.validation_alias
        declared = (
            {name.upper()} if alias is None else {str(choice).upper() for choice in alias.choices}
        )
        shadows = [n for n in {name.upper(), f"FOUNDRY_{name.upper()}"} if n not in declared]
        if shadows:
            candidates[name] = sorted(shadows)
    return candidates


def test_no_undeclared_environment_name_reaches_the_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE NEGATIVE ARM. A name nobody declared must not change the config."""
    for field, shadows in _undeclared_candidates(ServiceConfig).items():
        for shadow in shadows:
            _environment(
                monkeypatch, SERVICE_ENV="local", DATABASE_URL=LOCAL_URL, **{shadow: "SHADOW"}
            )
            actual = getattr(ServiceConfig.from_environment(), field)
            assert actual != "SHADOW", (
                f"{shadow} is not a declared name for {field!r} and it was READ. An "
                f"undeclared variable is invisible to scripts/env_ownership.yaml and to "
                f"check_cross_product_env.py, which is the registry that makes this class "
                f"impossible"
            )


def test_the_undeclared_name_check_can_actually_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE POSITIVE CONTROL, and without it the test above proves nothing.

    No field is aliased any more, so `_undeclared_candidates` finds no
    field-name shadow in production and the negative arm could pass over an
    empty set -- a control that cannot fail. This builds a settings class in
    EXACTLY the shape that shipped (an alias plus `populate_by_name=True`) and
    requires the shadow to be found and to be read, so the detector is known to
    work before its silence about production is believed.
    """

    class Shadowed(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

        env: str = Field(default="local", validation_alias=AliasChoices("SERVICE_ENV"))

    candidates = _undeclared_candidates(Shadowed)
    assert candidates == {"env": ["ENV", "FOUNDRY_ENV"]}, (
        f"the detector no longer identifies the shadow name it was built for: {candidates}"
    )

    # Through the same helper, so this adds no `monkeypatch` call site to §7's
    # budget and the variable is restored at teardown rather than by a `finally`
    # somebody can delete. `SERVICE_ENV` is cleared by the helper, so `ENV` is
    # the ONLY name in play.
    _environment(monkeypatch, ENV="SHADOW")

    assert Shadowed().env == "SHADOW", (
        "pydantic-settings no longer reads the field name under populate_by_name. That "
        "would be a welcome upstream fix, and this control then has to be REWRITTEN "
        "rather than deleted -- a control that cannot fire is not a control"
    )


def test_from_environment_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _environment(monkeypatch, SERVICE_ENV="local", DATABASE_URL=LOCAL_URL)

    config = ServiceConfig.from_environment()

    assert config.service_env == "local"
    assert config.port == 8080
    assert config.event_bus_mode == "in_process"
    assert config.migrations_dir == "migrations"


def test_a_missing_database_url_is_REFUSED_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is deliberately no default, and this is the arm that says so.

    `DB_PATH` used to default to `widgets.db`, so a deployment that never
    configured its database got a service that started, answered
    `/health/ready`, and wrote every row to a container filesystem that is
    discarded on the next deploy -- green, and losing data. A refusal naming the
    variable is the only remedy a task definition can actually act on.
    """
    _environment(monkeypatch, SERVICE_ENV="local")

    with pytest.raises(ValueError, match="DATABASE_URL"):
        ServiceConfig.from_environment()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_service_env_is_REFUSED_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """There is deliberately no default.

    It used to be `local`, and `build_principal_auth()` trusts unverified
    principal headers in a local environment, so a task definition that forgot
    this one variable started with header-trusting auth and no error. A blank
    value is the same omission spelled differently.
    """
    values = {"DATABASE_URL": LOCAL_URL}
    if value is not None:
        values["SERVICE_ENV"] = value
    _environment(monkeypatch, **values)

    with pytest.raises(ValueError, match="SERVICE_ENV"):
        ServiceConfig.from_environment()


def test_from_environment_reads_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _environment(
        monkeypatch,
        SERVICE_ENV="staging",
        PORT="9090",
        PRINCIPAL_ISSUER="az-widgets",
        DATABASE_URL="postgresql+psycopg://svc@db.example:5432/widgets",
    )

    config = ServiceConfig.from_environment()

    assert config.service_env == "staging"
    assert config.port == 9090
    assert config.principal_issuer == "az-widgets"
    assert config.database_url == "postgresql+psycopg://svc@db.example:5432/widgets"


def test_the_lambda_runtimes_own_variable_is_not_config_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPLACES `test_from_environment_detects_lambda_by_ambient_variable`.

    That test asserted `config.runtime_mode == "lambda"`, and both the field and
    the `AWS_LAMBDA_FUNCTION_NAME` field it was inferred from are gone: nothing
    outside `config.py` ever read either, which is what
    `scripts/check_iac_env_has_a_reader.py` rule 2 refuses.
    Deleting the test with the fields would have left nothing asserting that the
    deletion is COMPLETE.

    It is not enough to assert the attributes are absent, and this is the reason
    the arm exists in this form: `extra="ignore"` means a caller can still PASS
    `runtime_mode="http"` and be silently ignored, so thirteen construction
    sites in this suite stayed green while naming a field that no longer existed.
    An attribute check catches the declaration; the `model_fields` check catches
    the surface an operator can reach.
    """
    _environment(
        monkeypatch,
        SERVICE_ENV="local",
        AWS_LAMBDA_FUNCTION_NAME="foundry-service-template-prod",
        DATABASE_URL=LOCAL_URL,
    )

    config = ServiceConfig.from_environment()

    assert "runtime_mode" not in ServiceConfig.model_fields
    assert "aws_lambda_function_name" not in ServiceConfig.model_fields
    assert not hasattr(config, "runtime_mode")
    # The variable is still set in this process, and the config is indifferent
    # to it -- which is the whole claim. The Lambda Web Adapter makes the
    # entrypoint identical under both deploy targets, so there was never a
    # branch for this value to feed.
    assert "AWS_LAMBDA_FUNCTION_NAME" not in environment_variables_read(ServiceConfig)


# COMMS-FLAVOUR-STRIP:BEGIN: sqs mode validation,
# no point-to-point publish path for comms services
def test_sqs_mode_requires_queue_url() -> None:
    with pytest.raises(ValueError, match="sqs_queue_url"):
        ServiceConfig(
            service_env="local",
            port=8080,
            principal_issuer="test",
            database_url=LOCAL_URL,
            event_bus_mode="sqs",
            sqs_queue_url=None,
            aws_region=None,
        )
# COMMS-FLAVOUR-STRIP:END


def test_config_is_the_settings_class_and_reads_the_environment_through_it() -> None:
    """Replaces an assertion that config.py IMPORTS `os`.

    That test was right for the dataclass form and is the wrong question for
    S18: a `BaseSettings` reads the environment through pydantic-settings and
    names `os` nowhere, so the old assertion would go red on a correct module
    while a hand-rolled `os.environ` reader smuggled back into any OTHER file
    would still pass it. The property worth asserting is the one the layering
    rule actually depends on -- this class is the environment reader -- and the
    negative half (no other module under `src/` reads the environment) is
    `tests/shape/test_layering.py`'s, which enumerates the whole tree rather
    than this one file.
    """
    import ast
    from pathlib import Path

    from pydantic_settings import BaseSettings

    assert issubclass(ServiceConfig, BaseSettings)
    assert ServiceConfig.model_config["frozen"] is True
    # THE VALUE, and only the value. `model_config` at runtime is the MERGED
    # mapping, so pydantic-settings has already filled its own default in and
    # `"env_prefix" in ServiceConfig.model_config` is true whether or not anybody
    # wrote the key -- an assertion that cannot fail. Measured rather than
    # reasoned: deleting `env_prefix=""` from config.py leaves this file at 11
    # passed, and changing it to "SVC_" turns 7 of the 11 red. So this arm owns
    # the behaviour and `service-kit/gates/check_pydantic_boundaries.py`'s
    # clause 1, which reads the SOURCE, owns the declaration -- it exits 1 on the
    # deletion this test cannot see. Neither instrument covers the field alone.
    assert ServiceConfig.model_config["env_prefix"] == ""

    config_source = (
        Path(__file__).parent.parent.parent / "src/foundry_service_template/composition/config.py"
    )
    tree = ast.parse(config_source.read_text())
    imports_os = any(
        isinstance(node, ast.Import) and any(alias.name == "os" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert not imports_os, (
        "config.py no longer hand-rolls environment reads; pydantic-settings does it. "
        "An `import os` here means a field was added to the class and wired by hand, "
        "which is the two-halves-that-nothing-joins shape S18 removed"
    )


def test_every_declared_field_is_reachable_from_the_environment() -> None:
    """The half the dataclass form could not give, and the reason for S18.

    A `@dataclass` plus `from_environment()` spells every field twice, and a
    field added to the class but not to the reader is invisible: it exists,
    carries its default, and is not configurable, at exit 0. Here the two halves
    are one declaration, and this test asserts the property directly -- every
    field resolves to a NON-EMPTY environment variable name, so none can be
    silently unreachable.
    """
    for name, field in ServiceConfig.model_fields.items():
        alias = field.validation_alias
        if alias is None:
            resolved = [name.upper()]
        else:
            resolved = [str(choice).upper() for choice in alias.choices]
        assert resolved and all(resolved), f"{name} resolves to no environment variable"


def test_an_unparseable_boolean_is_REFUSED_rather_than_silently_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`MIGRATE_ON_STARTUP=maybe` used to mean False. Now it refuses.

    The old reader was `.lower() in {"1", "true", "yes"}`, so every value
    outside that set -- including a typo, and including `on`, `t` and `y` --
    fell through to False. A deployment that meant to migrate silently did not,
    which is the fall-through-inherits-the-branch's-severity shape.
    """
    _environment(
        monkeypatch, SERVICE_ENV="local", DATABASE_URL=LOCAL_URL, MIGRATE_ON_STARTUP="maybe"
    )

    with pytest.raises(ValueError, match="migrate_on_startup"):
        ServiceConfig.from_environment()


def test_an_unknown_event_bus_mode_is_REFUSED(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `Literal` annotation used to be carried past a `# type: ignore`.

    `EventBusMode` described a guarantee nothing enforced: the old reader handed
    `os.environ.get("EVENT_BUS_MODE", ...)` straight through with a silencing
    comment, so `EVENT_BUS_MODE=kinesis` produced a config whose annotation was
    a lie and whose `build_publisher()` fell to the in-process bus.
    """
    _environment(monkeypatch, SERVICE_ENV="local", DATABASE_URL=LOCAL_URL, EVENT_BUS_MODE="kinesis")

    with pytest.raises(ValueError, match="event_bus_mode"):
        ServiceConfig.from_environment()


def test_the_config_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config object reaches every adapter. A mutable one is an ambient global."""
    _environment(monkeypatch, SERVICE_ENV="local", DATABASE_URL=LOCAL_URL)

    config = ServiceConfig.from_environment()

    with pytest.raises(ValueError):
        config.port = 1
