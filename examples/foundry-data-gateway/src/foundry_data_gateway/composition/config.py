"""The composition root's configuration (S18: pydantic-settings).

This module is the ONE place in this package that reads the process environment.
`ruff`'s `flake8-tidy-imports.banned-api` bans `os.environ` everywhere else in
the workspace, `service-kit/gates/check_layering.py` rule 3 makes a read outside
this file a finding, and `tests/shape/test_layering.py` asserts the same over
the tree.

Every other module receives a `ServiceConfig` value through a constructor
argument. Reading the environment happens exactly once, at process start, in
`from_environment()` -- never lazily, never at import time. An ambient value
picked up mid-request is exactly the class of bug this pattern exists to rule
out: see `.claude/CLAUDE.md`'s account of `stage`/`STAGE`/`APP_NAME` being read
at import time and silently retargeting a seeding script at production.

WHY `BaseSettings` RATHER THAN A `@dataclass` PLUS A HAND-WRITTEN READER.
The dataclass form spelled every field twice -- once as a typed attribute, once
as an `os.environ.get(...)` line in `from_environment()` -- and the two halves
were joined by nothing a tool could check. A field added to one and not the
other is invisible: the attribute exists, carries its default, and is never
configurable, at exit 0. The coercions were hand-rolled too (`int(...)`,
`float(...)`, `.lower() in {"1", "true", "yes"}`), so an unparseable value either
raised a bare `ValueError` naming no variable or, in the boolean case, silently
became `False`.

THREE BEHAVIOUR CHANGES THIS BUYS, NAMED RATHER THAN DISCOVERED LATER.

1. **A nonsense boolean now REFUSES.** `MIGRATE_ON_STARTUP=maybe` used to fall
   through `in {"1", "true", "yes"}` to `False`, so a deployment that meant to
   migrate silently did not. Pydantic raises instead, naming the field.
   Pydantic's accepted set is also wider than the old three: `on`/`off`,
   `t`/`f`, `y`/`n`, `yes`/`no`, `0`/`1`, `true`/`false`, case-insensitive.
2. **An unknown `EVENT_BUS_MODE` now REFUSES.** It was annotated `Literal` and
   fed through a `# type: ignore[arg-type]`, so the annotation described a
   guarantee nothing enforced. The `Literal` is now checked at startup.
3. **CONSTRUCTING A `ServiceConfig` EXPLICITLY STILL CONSULTS THE ENVIRONMENT
   for any field the caller leaves out.** That is `BaseSettings`, not a mistake,
   and it is the one property worth reading twice: a test that builds
   `ServiceConfig(env="local", port=8080, ...)` in a process where
   `EVENT_BUS_MODE=sqs` is exported gets `event_bus_mode="sqs"`. Measured, not
   assumed (`PORT=7777` in the environment beat an unset `port` on an explicit
   construction). Tests that care pass every field they assert on; a test that
   needs a hermetic value must clear the variable, which is what
   `tests/unit/test_config.py` does through its `_CLEARED` tuple.

`env_prefix` IS DECLARED AND EMPTY, AND THE EMPTY VALUE IS THE DECISION.
`.claude/CLAUDE.md`: "a guarantee that is TRUE only because of a library DEFAULT
is not a guarantee". `""` happens to be pydantic-settings' default, so writing
it changes no behaviour and records that somebody chose it. A non-empty prefix
was measured wrong for this estate rather than judged inelegant:
`deploy/compose.yml` sets `DATABASE_URL`, `deploy/iac/serverless.yml` sets
`SERVICE_ENV` and `DATABASE_URL`, `deploy/iac/app/main.tf` sets `SERVICE_ENV`
and `PORT`, and `AWS_REGION` and `AWS_ENDPOINT_URL` are AWS-owned names that no
service may rename -- boto3 reads them under those exact spellings. A prefix
would give this service a second answer to a question AWS has already answered,
which is the cross-product environment defect (`check_cross_product_env.py`)
arriving from the other side.

NO FIELD HAS AN ALIAS, AND THAT IS A SAFETY PROPERTY RATHER THAN A STYLE.
Every field's environment variable is its own name upper-cased, so the name a
reader sees in this class and the name an operator sets in a task definition
cannot diverge.

The first version of this module aliased two fields -- `env` read `SERVICE_ENV`
and `issuer` read `PRINCIPAL_ISSUER` -- and carried `populate_by_name=True` so
tests could still construct by field name. That combination gave each aliased
field a SECOND, UNDECLARED environment variable: `ENV` and `ISSUER` were read
and took effect. Measured mechanism, pydantic-settings 2.15.0, in
`pydantic_settings/sources/utils.py:342`:

    if populate_by_name and field_name not in alias_names:
        alias_names.append(field_name)

`_get_alias_names` is shared by the INIT source and the ENV source, and
`sources/base.py:324` computes
`include_name = populate_by_name or validate_by_name`, so the flag cannot be
turned on for construction alone. There is no split to exploit and
`validate_by_name` behaves identically. With `case_sensitive=False` on top,
`ENV=production` in an ambient shell reached `config.env`.

`ENV` is a name shells and CI runners commonly already set, and the read was
INVISIBLE to `scripts/env_ownership.yaml` and `check_cross_product_env.py` --
the registry this estate keeps precisely so that a service cannot read a
variable nobody declared. Renaming the two fields to `service_env` and
`principal_issuer` removes the alias, removes the need for the flag, and makes
the class unrepresentable rather than merely absent: with no alias there is no
second name to shadow. Dropping the flag alone would have removed today's two
shadows and left the next alias one flag away from re-creating them.

`check_pydantic_boundaries.py` clause 5 refuses a config root that sets the
flag, and `tests/unit/test_config.py` drives the real environment to prove that
no undeclared name reaches the config -- with a positive control that builds a
settings class in the shape that shipped, so the assertion is known to fire.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EventBusMode = Literal[
    "in_process",
    "sqs",  # COMMS-FLAVOUR-STRIP-LINE: comms has no point-to-point mode
    "outbox",
]

# THERE IS NO `runtime_mode` AND NO `aws_lambda_function_name`, and their absence
# is the point rather than an omission.
#
# This class used to declare both: `RUNTIME_MODE` landed in a `runtime_mode`
# field, and `AWS_LAMBDA_FUNCTION_NAME` landed in a second field that existed
# only to infer the first. Nothing outside this module ever read either one.
# `main.py`'s docstring said so in as many words -- "`AWS_LAMBDA_FUNCTION_NAME`
# only ever affects `ServiceConfig.runtime_mode`, which nothing here branches
# on -- it exists for adapters/handlers that care (e.g. choosing a
# Lambda-friendly log format)" -- which is a sentence about a consumer that was
# never written, in a TEMPLATE, where it would have been copied into every
# emitted service.
#
# That is the `EVENT_BUS_MODE: outbox` defect exactly: a
# declared configuration surface asserting a capability no code path provides.
# `scripts/check_iac_env_has_a_reader.py` rule 2 is the gate for it, and it
# could not see this pair until that gate learned to resolve a `BaseSettings` --
# before the migration `runtime_mode` was computed into a LOCAL inside
# `from_environment()`, so no variable-to-field mapping existed to follow, and
# `AWS_LAMBDA_FUNCTION_NAME` was a bare `os.environ` read.
#
# The fix is deletion rather than a consumer. Writing a log-format branch so the
# field has a reader would satisfy the gate and leave the estate asserting a knob
# nobody asked for; the Lambda Web Adapter already makes this entrypoint
# identical under both deploy targets, which is why no branch was ever needed.
# Neither variable appears in `deploy/iac/serverless.yml`, so removing the fields
# leaves nothing declared-but-unread behind either (measured).
#
# ONE RESIDUAL, RECORDED HERE RATHER THAN HALF-FIXED. `service-kit/docker/
# entrypoint.sh` still detects the runtime and `export RUNTIME_MODE=...`, and its
# own comment still says it does so "for ServiceConfig to read". That citation is
# now false: the shell exports a variable no Python reads. It is deliberately NOT
# fixed in this change, because that file is kit-owned and byte-identical across
# five copies (`scripts/check_entrypoint_forwards_cmd.py` enumerates them), so
# editing it here -- even the comment -- puts every consuming service into kit
# drift until it syncs. That is a fleet change with its own sequencing, not a
# line in this slice. an earlier change carries it.
#
# A service that genuinely needs to know: declare the field AND the consumer in
# the same change, and let rule 2 be the thing that holds you to it.


class ServiceConfig(BaseSettings):
    """Everything the container needs to wire the service, resolved once."""

    model_config = SettingsConfigDict(
        # Declared and empty -- see the module docstring. Not omitted.
        env_prefix="",
        # The config object is handed to every adapter and use case. A mutable
        # one is an ambient global with extra steps.
        frozen=True,
        # A process environment holds hundreds of variables that are not this
        # service's configuration. Refusing them would make the service refuse
        # to start inside any CI runner.
        extra="ignore",
        # `populate_by_name` is DELIBERATELY ABSENT, and its absence is load
        # bearing. It let a caller construct by field name, and it ALSO handed
        # the environment source the field name as an extra alias -- see the
        # module docstring for the measurement. No field is aliased any more, so
        # construction by field name works without it, and
        # `check_pydantic_boundaries.py` clause 5 refuses a config root that
        # sets it or `validate_by_name`.
    )

    #: `SERVICE_ENV`. NAMED for its variable rather than aliased to it; the
    #: module docstring records what the alias cost.
    #:
    #: REQUIRED, with no default, for the same reason as `database_url` below.
    #: It defaulted to `local`, and `build_principal_auth()` accepts unverified
    #: principal headers in a local environment, so a deployment that forgot
    #: this one variable started with header-trusting auth and no error
    #:. Declared with an empty default so the refusal in
    #: the validator, which names the variable, is the one a deployment sees.
    service_env: str = ""
    port: int = 8080
    #: `PRINCIPAL_ISSUER`, likewise named rather than aliased.
    principal_issuer: str = "foundry-data-gateway"
    #: The SQLAlchemy URL this service persists to, WITHOUT a password. There is
    #: ONE persistence axis now, where there used to be a `db_backend` selector
    #: over hand-written SQLite and MySQL adapters: the ORM absorbs the dialect,
    #: so which engine is in use is a property of this string and of nothing in
    #: `src/`. Postgres is the estate's engine
    #: (`postgresql+psycopg://user@host:5432/dbname`); `deploy/compose.yml`
    #: points this at the kit's `postgres` fragment.
    #:
    #: REQUIRED, with no default, and that is the point. `db_path` defaulted to
    #: a local file, so a deployment that failed to configure its database got a
    #: service that started, answered `/health/ready`, and wrote every row to a
    #: disposable container filesystem -- green, and losing data
    #: (`knowledge/wiki/a-fall-through-inherits-the-branchs-severity.md`). It is
    #: declared with an empty default rather than as a required field so that
    #: the REFUSAL below is the one a deployment sees: pydantic's own missing-
    #: field error names `database_url`, and a task definition has no field to
    #: set -- it has a variable.
    database_url: str = ""
    event_bus_mode: EventBusMode = "in_process"
    sqs_queue_url: str | None = None  # COMMS-FLAVOUR-STRIP-LINE
    aws_region: str | None = None
    #: JWKS endpoint and audience for the signature-verifying auth adapter. Both
    #: are None on a local run, where the header adapter is the intended default.
    #: Non-locally, `build_container()` refuses to start without them rather than
    #: falling back to unverified headers.
    auth_jwks_uri: str | None = None
    auth_audience: str | None = None
    #: The NAME of the secret holding the database password -- never the password.
    #: `build_container()` resolves it through a `SecretsPort`, and the engine
    #: injects it at CONNECT time, so the credential never sits on a config
    #: object that a log line or a traceback can print. None where the URL needs
    #: no password (a local run, a socket-authenticated Postgres).
    db_password_secret_id: str | None = None
    #: §6 runtime knobs. Read HERE, like everything else that comes from the
    #: environment, and handed to `runtime/` as constructor arguments -- no
    #: module under `runtime/` reads the environment itself.
    #: A LOCAL AWS endpoint (LocalStack, or moto-server). None in production,
    #: where boto3 resolves the real one. It is configuration, not a test hook:
    #: a local `docker compose` run needs it exactly as much as a test does.
    aws_endpoint_url: str | None = None
    log_level: str = "INFO"
    #: The ceiling on the in-flight drain. A drain with no ceiling turns one
    #: stuck request into a deployment that never completes.
    shutdown_grace_seconds: float = 30.0
    #: Whether startup applies migrations. EXPLICIT by §6: a service migrates
    #: because this says so, never as a side effect of importing a module.
    migrate_on_startup: bool = False
    #: Data tools. `TOOL_PACKS_DIR` holds `*.tools.yaml` packs and `TOOL_POLICY_FILE` the
    #: tenant grants (locally), or `TOOL_CATALOGUE_URI` names their S3 prefix (deployed);
    #: neither means the service serves no data tools. `AGENT_MODEL_ID` is the model
    #: named in the generated foundry-agent definition.
    tool_packs_dir: str | None = None
    tool_policy_file: str | None = None
    #: `s3://bucket/prefix/` holding the packs and `policy.yaml`; the deployed form.
    tool_catalogue_uri: str | None = None
    tool_webhook_secret_file: str | None = None
    #: The webhook HMAC key itself, injected by ECS from Secrets Manager in a deployment
    #: (a file path locally). Exactly one of the two is used; the value wins.
    tool_webhook_secret: str | None = None
    agent_model_id: str = "data-agent-model"
    #: `DATA_SOURCE_URLS`: JSON object from each pack source's `connection` name to its
    #: DSN, e.g. `{"alqosh": "mysql+pymysql://reader:...@host/alqosh"}`. Delivered from a
    #: secret in a deployment; the packs themselves never hold credentials.
    data_source_urls: dict[str, str] = {}
    #: Where this service's alembic tree is. Relative to the process working
    #: directory, which is `/app` in the image and the repository root locally --
    #: `migrations` resolves correctly in both, which is why the default is a
    #: relative path rather than one computed from `__file__`. Computing it from
    #: the package would be wrong in exactly one of the two layouts, and the
    #: image is the one where being wrong costs a deploy.
    migrations_dir: str = "migrations"
    #: `AWS_LWA_PASS_THROUGH_PATH` / `FOUNDRY_FUNCTION` (the Lambda dispatch change).
    #: Read here, like everything else that comes from the environment, and
    #: handed to `lambda_dispatch.mount_non_http_dispatch()` as constructor
    #: arguments -- that module does not read `os.environ` itself, for the
    #: same reason nothing under `runtime/` does. Both None for `api` and for
    #: every non-Lambda deploy target; a non-HTTP Lambda function sets both so
    #: the Lambda Web Adapter can forward its raw invocation event here
    #: instead of running a second Runtime API client beside the adapter.
    aws_lwa_pass_through_path: str | None = None
    foundry_function: str | None = None

    @model_validator(mode="after")
    def _refuse_a_configuration_no_deployment_can_run_on(self) -> ServiceConfig:
        """Every refusal names the ENVIRONMENT VARIABLE, not the field.

        A task definition and a Lambda have no call site to edit, so
        `pass database_url=` is an instruction only a unit test can follow --
        `.claude/CLAUDE.md`'s "a refusal a DEPLOYMENT can reach must name a
        remedy a deployment can perform".
        """
        if not self.service_env.strip():
            raise ValueError(
                "ServiceConfig.service_env must be non-empty; set SERVICE_ENV to local or "
                "test for a local run, or to the deployed stage (dev, staging or production). "
                "There is no default, because an unset value used to mean local, which "
                "accepts unverified principal headers"
            )
        if self.port <= 0:
            raise ValueError("ServiceConfig.port must be positive; set PORT")
        if self.shutdown_grace_seconds <= 0:
            raise ValueError(
                "ServiceConfig.shutdown_grace_seconds must be positive; set SHUTDOWN_GRACE_SECONDS"
            )
        # COMMS-FLAVOUR-STRIP:BEGIN: sqs mode validation,
        # no point-to-point publish path for comms services
        if self.event_bus_mode == "sqs" and not self.sqs_queue_url:
            raise ValueError("event_bus_mode='sqs' requires sqs_queue_url; set SQS_QUEUE_URL")
        # COMMS-FLAVOUR-STRIP:END
        if not self.database_url:
            # Checked HERE rather than at first query: a service that does not
            # know where its database is should die at startup, not on the first
            # request that reaches persistence.
            raise ValueError(
                "ServiceConfig.database_url must be non-empty; set DATABASE_URL to a "
                "SQLAlchemy URL such as postgresql+psycopg://user@host:5432/dbname"
            )
        return self

    @classmethod
    def from_environment(cls) -> ServiceConfig:
        """Read `ServiceConfig` from the process environment, once.

        `BaseSettings.__init__` is what actually reads it, so this is a thin
        classmethod -- and it is kept rather than deleted because the NAME is
        the contract every caller and every gate refers to. `main.py` calls it
        at module scope, exactly once, and nothing else in `src/` constructs a
        config from the environment.
        """
        return cls()

