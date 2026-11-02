"""The real seed profile: one bring-up per RUN, one database per WORKER, one engine per worker.

This module replaces `tests/support/postgres.py`, which said in its own docstring
that it was "a deliberately small stand-in for the kit's `real` seed profile ... so the replacement is a deletion rather than a redesign".
It is that deletion. Two bring-up authorities for one PostgreSQL would be the
estate's second-authority defect wearing a test harness: two project names, two
`up -d`, two teardowns, and a `down -v` from either one destroying the other's
database mid-run.

WHY THE CONTROLLER BRINGS IT UP AND THE WORKERS DO NOT. Under pytest-xdist a
session-scoped fixture runs in EVERY worker and never in the controller, so
"session-scoped bring-up" means N bring-ups racing for one project name. The
lifecycle therefore lives in `pytest_configure`, guarded by
`not hasattr(config, "workerinput")` -- the one hook that runs in the controller
exactly once -- and the resulting endpoints reach the workers through
`pytest_configure_node` -> `node.workerinput`. A run with no xdist takes the same
path in-process, so there is one code path rather than two that must agree.

That last sentence was FALSE as originally written, and the correction is worth
keeping because the claim reads as harmless prose. There was no no-xdist path:
defining `pytest_configure_node` at all made xdist a hard requirement, and a
service installing without it lost its entire suite to a
`PluginValidationError` at exit 3. `@pytest.hookimpl(optionalhook=True)` on
that hook is what makes the sentence true; see its own docstring for why the
obvious `find_spec` guard does not.

THE PROJECT NAME ISOLATES CHECKOUTS, NOT CONCURRENT RUNS IN ONE CHECKOUT, and
that trade is deliberate rather than an oversight. `compose_project_name` hashes
the realpath of the checkout, so three worktrees of this template get three
independent stacks. Two runs in the SAME checkout derive the IDENTICAL name:
`up -d --wait` ADOPTS the other's containers, `template_0` and `test_<gwN>` are
one namespace, and whichever run finishes first executes `down -v` in
`pytest_unconfigure` and takes the other's databases with it mid-run.

A per-RUN name would fix that and is the wrong trade. It would give every run its
own stack, and a run that crashed before teardown would leave containers no later
run can ever adopt or tear down -- an orphan, on a machine that has already hit
100% disk today. A collision is loud at the moment it happens; an orphan is
silent forever, and an orphaned PostgreSQL with no published host port is the A3
defect this module exists to remove. So: per-checkout, one run at a time.

ADOPTION IS COMPLETELY SILENT, which is the half nobody would guess and the
reason this paragraph exists. Measured 2026-09-18: a run left up under
`FOUNDRY_KEEP_RESOURCES=1`, then three later single-file runs of 0.94s, 0.24s
and 0.19s that adopted that stack and whose `pytest_unconfigure` then tore it
down. There is no log line, no warning, and a sub-second session is
indistinguishable from a run with nothing to do -- so the NEXT run finds nothing
and reports a stack that "was never there". If you need a second run while one is
live, use a different checkout, or set `FOUNDRY_TEST_DATABASE_URL` to be a GUEST
(no bring-up, and no teardown to destroy) on a server NO OTHER RUN IS USING.

ONE RUN PER POSTGRES SERVER IS ENFORCED, because that last clause used to be
advice. A guest does not tear a stack down, but it drops and recreates `template_0`
and its worker databases like any other run, so two guests on one server destroy
each other's data: 289 setup errors from two runs on one port.
The controller takes a session-level advisory lock (`claim_server`) before it
touches the template and holds it until `pytest_unconfigure`. A second run on that
server, guest or adopted, stops with a usage error naming the holder, and does not
take the adopted stack down on its way out. It covers a server with a database on
it; a service that declares no `postgres` resource has nothing to lock, and two of
its runs in one checkout still share containers.

WHY A TEMPLATE DATABASE. `alembic upgrade head` runs ONCE, into `template_0`, and
each worker then clones it with `CREATE DATABASE test_<gwN> TEMPLATE template_0`.
Cloning is a file copy inside the server; migrating per worker would run the whole
revision tree N times against one server, and the revisions are the slowest thing
in the bring-up.

The clone is also where the estate's `env.py` rule is CHECKED rather than trusted.
PostgreSQL refuses `CREATE DATABASE ... TEMPLATE t` while any session is attached
to `t`, so a `migrations/env.py` that leaks a connection turns every worker's
first action into a failure whose message is about the template. `_assert_template_is_idle`
asks `pg_stat_activity` directly and refuses with the leak named, before the clone
that would have reported it as something else.

WHY ONE ENGINE PER WORKER. An `Engine` owns a connection pool. Building one per
test -- which is what a function-scoped `create_engine` does -- gives every test
its own pool, and a suite of a hundred tests exhausts `max_connections=100`
somewhere in the middle, as a connection error attributed to whichever test was
unlucky. `worker_engine` is session-scoped and disposed once; `postgres_sessions`
binds to it and truncates. The proof is `pg_stat_activity` totals before and after
a full run, which must be equal.

WHY TRUNCATE AND NOT A SAVEPOINT. The code under test owns its transactions and
commits freely. A savepoint harness has to make every application `commit()`
restart a nested transaction, which is a behaviour change wrapped around the exact
behaviour these tests exist to measure. Truncate is the simplest correct option
here, and it leaves room for real racing-connection tests later.

A NAMED SKIP, NEVER A SILENT PASS. When Docker is not running, the run prints
`real arm skipped: <remedy>` and the fixtures skip with that remedy.
`REQUIRE_REAL_RESOURCES=1` turns that skip into a hard error at CONFIGURE time --
not at fixture time -- because a run whose declared resources could not start is
not entitled to report anything, including the part that did run.

THAT SENTENCE USED TO CLAIM MORE THAN THIS MODULE CAN DELIVER, and the gap is
worth keeping because it is the shape rather than the instance. Configure time
covers the resources this module brings up: the ones named in
`[tool.foundry.test-resources]`. It cannot cover a resource some other kit
reaches at FIXTURE time -- `aws_kit`'s endpoint, `cache_kit`'s Redis -- because
those are not this module's to start, and a flag read here is invisible there.
Read as "every skip", the sentence was false in exactly the direction that costs
something: with the flag set, four SQS arms and one Redis arm skipped inside a
green run. The flag is now read in ONE place, `resources.REQUIRE_ENV`, and
honoured in two -- here for the declared resources, and in
`resources.skip_or_fail` for every fixture that asks for one.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import socket
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# ONE definition of the flag name, in the module every kit already imports for
# `ResourceUnavailable`. Re-exported below so callers that learned it from here
# keep working; it is imported rather than redeclared because two string
# literals that agree today are the shape this slice is fixing.
from foundry_testkit.resources import REQUIRE_ENV

__all__ = [
    "KEEP_RESOURCES_ENV",
    "REQUIRE_ENV",
    "RealPostgres",
    "RealProfile",
    "RealResourcesUnavailable",
    "ResourceDeclaration",
    "compose_project_name",
    "load_declaration",
    "owned_tables",
    "pin_fake_aws_credentials",
    "worker_database_name",
]

#: The kit's checkout root: `<root>/service-kit/testing/foundry_testkit/real_profile.py`.
#: The kit is copied to `service-kit/` in every service, so this is the same
#: arithmetic everywhere and needs no per-service constant.
ROOT = Path(__file__).resolve().parents[3]

#: THE ONE KIT-OWNED NAME for "the real database these tests run against", with
#: per-service VALUES. Normally UNSET: the kit provisions its own server from
#: `deploy/compose.yml`, so in the default path there is no variable at all and
#: six services have nothing to disagree about. Set it to skip bring-up and use a
#: server somebody else started -- a CI service container, or a developer's own.
#:
#: It replaces eight different names that meant this (FOUNDRY_TEST_MYSQL_URL,
#: TEST_DATABASE_URL, FOUNDRY_AUTH_DATABASE_URL, MEMORY_TEST_DATABASE_URL,
#: LOCAL_DATABASE_URL, ADMISSION_TEST_DATABASE_URL, DEFAULT_LOCAL_DATABASE_URL,
#: FOUNDRY_ARTIFACT_STORE_TEST_DATABASE_URL). `UNUSED_DATABASE_URL` is NOT one of
#: them and is deliberately left alone: it is a module constant holding an
#: unreachable URL for the FAKE arm, never read from the environment.
DATABASE_URL_ENV = "FOUNDRY_TEST_DATABASE_URL"

#: Leaves the containers running. A3 shipped this name and the monorepo's
#: `scripts/env_ownership.yaml` already registers it; the plan says
#: `KEEP_RESOURCES`, and renaming for the sake of the plan's wording would break a
#: live registration for nothing.
KEEP_RESOURCES_ENV = "FOUNDRY_KEEP_RESOURCES"

#: What the kit's `docker/compose/postgres.yml` fragment creates. Kit-owned on
#: both sides -- the fragment and this constant travel together -- so they cannot
#: drift the way nine hand-copied compose files did.
MAINTENANCE_DATABASE = "postgres"
POSTGRES_USER = "postgres"
POSTGRES_PASSWORD = "postgres"

#: Migrated once, cloned per worker. Distinct from PostgreSQL's built-in
#: `template0`/`template1`, which have no underscore.
TEMPLATE_DATABASE = "template_0"

#: One key, so two concurrent `CREATE DATABASE ... TEMPLATE` calls serialise.
#: PostgreSQL refuses the clone while the template has a session, and two workers
#: cloning at the same instant can each be the other's blocker -- a failure that
#: reads as a leaked migration connection and is not one.
CLONE_LOCK_KEY = 0x5D19_A4C1

#: Held by the controller from the moment it knows its server until the run ends.
#: ONE RUN PER POSTGRES SERVER, because every run uses the same names on it:
#: `template_0` and `test_<gwN>`. A second run drops and recreates the first one's
#: template and worker databases underneath it, and the first run then reports
#: setup errors about tables that are missing. Measured 2026-10-03: 289 of them,
#: from two guest runs pointed at one port.
RUN_LOCK_KEY = 0x5D19_A4C2

#: What the run's lock connection calls itself, so a refused run can say who holds
#: the server. PostgreSQL keeps 63 bytes of it; the process id comes first.
RUN_APPLICATION_PREFIX = "testkit run pid"


class RealResourcesUnavailable(RuntimeError):
    """A declared real resource is not reachable. Carries the remedy."""


class ServerInUse(RuntimeError):
    """Another test run holds this PostgreSQL server. Carries who, and the remedy.

    NOT a `RealResourcesUnavailable`. That one means the resource is absent, and an
    absent resource is a named skip. This server answered and is busy: skipping
    would turn a suite that needs its database into hundreds of skips that read as
    a missing Docker, and carrying on would destroy the other run's databases.
    """


@dataclass(frozen=True, slots=True)
class ResourceDeclaration:
    """What `[tool.foundry.test-resources]` says this service's tests need.

    DECLARED, NEVER GUESSED. The alternative -- bringing up whatever
    `deploy/compose.yml` happens to define -- makes every service pay for every
    resource any service needs, and makes `real arm ran:` a sentence about the
    compose file rather than about the tests.
    """

    resources: tuple[str, ...]
    orm_metadata: str | None
    service: str

    @property
    def wants_postgres(self) -> bool:
        return "postgres" in self.resources

    def describe(self) -> str:
        return ", ".join(self.resources) if self.resources else "none declared"


@dataclass(frozen=True, slots=True)
class RealPostgres:
    """A reachable server and the compose project it belongs to."""

    url: str
    project: str


@dataclass(frozen=True, slots=True)
class RealProfile:
    """What the controller brought up, as the workers receive it."""

    project: str
    #: A URL on the MAINTENANCE database, from which worker databases are cloned.
    #: None when no database was declared.
    admin_url: str | None
    resources: tuple[str, ...]
    #: `module:Base`, carried rather than re-read. A worker is a separate process
    #: whose working directory and `sys.path` are set up by xdist, and re-parsing
    #: `pyproject.toml` there would be a second answer to a question the
    #: controller has already answered.
    orm_metadata_ref: str | None = None
    #: Resource name -> the EPHEMERAL host port the controller resolved for it.
    #: Carried for the same reason `admin_url` is: a worker cannot ask docker
    #: again and get the same answer, because `up -d` in a worker would be a
    #: second bring-up. Empty when nothing publishing an endpoint was declared.
    endpoints: Mapping[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "admin_url": self.admin_url,
            "resources": list(self.resources),
            "orm_metadata_ref": self.orm_metadata_ref,
            "endpoints": dict(self.endpoints),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> RealProfile:
        reference = payload.get("orm_metadata_ref")
        return cls(
            project=str(payload["project"]),
            admin_url=None if payload["admin_url"] is None else str(payload["admin_url"]),
            resources=tuple(str(item) for item in payload["resources"]),  # type: ignore[union-attr]
            orm_metadata_ref=None if reference is None else str(reference),
            endpoints={
                str(name): int(port)  # type: ignore[union-attr]
                for name, port in (payload.get("endpoints") or {}).items()
            },
        )


# ---------------------------------------------------------------------------
# Declaration
# ---------------------------------------------------------------------------


def load_declaration(root: Path | None = None) -> ResourceDeclaration:
    """Read `[tool.foundry.test-resources]` out of the service's `pyproject.toml`.

    An ABSENT table means no real resources, which is a legitimate answer for a
    service with no database -- its real arm reports `real arm ran: none declared`,
    a named no-op. A MALFORMED table is a refusal rather than a default: a typo
    that silently reads as "no resources" would turn the whole real arm off and
    report a green run, which is the failure this file exists to rule out.
    """
    base = root or ROOT
    pyproject = base / "pyproject.toml"
    if not pyproject.is_file():
        raise RealResourcesUnavailable(
            f"{pyproject} does not exist, so no service declares its test resources. "
            f"The kit resolves the checkout root from its own location; a kit copied "
            f"anywhere other than <root>/service-kit/ will resolve the wrong one."
        )
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    service = str(payload.get("project", {}).get("name", base.name))
    table = payload.get("tool", {}).get("foundry", {}).get("test-resources")
    if table is None:
        return ResourceDeclaration(resources=(), orm_metadata=None, service=service)
    if not isinstance(table, dict):
        raise RealResourcesUnavailable(
            f"[tool.foundry.test-resources] in {pyproject} is a "
            f"{type(table).__name__}, expected a table."
        )
    declared = table.get("resources", [])
    if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
        raise RealResourcesUnavailable(
            f"[tool.foundry.test-resources] resources in {pyproject} must be a list of "
            f"strings such as ['postgres', 'localstack:s3,sqs'], got {declared!r}."
        )
    metadata = table.get("orm_metadata")
    if metadata is not None and not isinstance(metadata, str):
        raise RealResourcesUnavailable(
            f"[tool.foundry.test-resources] orm_metadata in {pyproject} must be a "
            f"'module:Attribute' string, got {metadata!r}."
        )
    resources = tuple(item.split(":", 1)[0] for item in declared)
    if "postgres" in resources and not metadata:
        raise RealResourcesUnavailable(
            f"{pyproject} declares the 'postgres' test resource but no orm_metadata. "
            f"The kit derives the truncate set from the mapping rather than from a "
            f"hand-written table list, so it needs to be told where `Base` lives: add "
            f"orm_metadata = '<package>.adapters.real.orm:Base' to "
            f"[tool.foundry.test-resources]."
        )
    return ResourceDeclaration(resources=resources, orm_metadata=metadata, service=service)


def compose_project_name(root: Path | None = None, service: str | None = None) -> str:
    """`shp-<service>-<sha1(realpath(checkout))[:8]>`, so two worktrees never collide.

    The hash is of the RESOLVED path: two checkouts of one repository are two
    estates, and a shared project name means one `down -v` destroys the other's
    database mid-run. That is a failure this estate has already paid for
    once with fixed container names.
    """
    base = (root or ROOT).resolve()
    name = service or load_declaration(base).service
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "service"
    digest = hashlib.sha1(str(base).encode()).hexdigest()[:8]
    return f"shp-{slug}-{digest}"


def worker_database_name(worker_id: str) -> str:
    """`test_<gwN>`, or `test_main` outside xdist.

    Sanitised because it is interpolated into DDL: a worker id is supplied by
    xdist and is always `gwN`, but a database name built from an unchecked string
    is an injection seam nobody needs.
    """
    suffix = re.sub(r"[^a-z0-9_]+", "_", worker_id.lower()).strip("_") or "main"
    return f"test_{suffix}"


#: Which container port each kit-known resource publishes, and the compose
#: variable whose `0` makes that publication ephemeral. A resource that is NOT
#: here is still started -- it is an ordinary compose service and declaring it
#: is enough -- it simply gets no endpoint published, because the kit has no
#: contract for where its clients look. `postgres` is absent on purpose: its
#: endpoint is an SQLAlchemy URL rather than a host:port, and it is resolved by
#: `_bring_up` into `admin_url`.
#:
#: Redis's bind variable is `REDIS_PORT_HOST` rather than `REDIS_PORT` ON
#: PURPOSE. `cache_kit` reads `REDIS_PORT` to decide where the CLIENT connects,
#: and compose would read the same name to decide where the container BINDS. One
#: name for both means publishing the resolved client port back into the
#: environment silently re-pins the next `up`, and the two meanings drift apart
#: exactly once, at the moment somebody sets it by hand.
ENDPOINT_CONTRACT: dict[str, tuple[int, str]] = {
    "localstack": (4566, "LOCALSTACK_PORT"),
    "redis": (6379, "REDIS_PORT_HOST"),
}


def _ephemeral_port_env() -> dict[str, str]:
    """`0` for every host-port variable the kit's fragments interpolate.

    DERIVED from `ENDPOINT_CONTRACT` rather than written out beside it, so a
    fragment added to the contract cannot be started on a fixed port because
    somebody updated one list and not the other. PostgreSQL is added explicitly
    because it is deliberately absent from the contract -- its endpoint is a URL,
    not a host:port -- and it still has to bind ephemerally.
    """
    env = {"POSTGRES_PORT": "0"}
    env.update({variable: "0" for _, variable in ENDPOINT_CONTRACT.values()})
    return env


def endpoint_environment(endpoints: Mapping[str, int]) -> dict[str, str]:
    """The variables each kit reads, from the ports the controller resolved.

    THE ENVIRONMENT IS THE SEAM, deliberately, and it is worth saying why rather
    than passing a handle. `aws_kit` and `cache_kit` already resolve their
    endpoint from `LOCALSTACK_ENDPOINT` and `REDIS_HOST`/`REDIS_PORT`, which is
    also how an operator points them at a surface of their own. Publishing the
    resolved ports into those same variables means there is ONE way an endpoint
    is chosen, not a kit-internal channel beside an operator-facing one that can
    disagree.

    Pure, so its arm can hand it a port map and read the result without starting
    anything.
    """
    published: dict[str, str] = {}
    if "localstack" in endpoints:
        published["LOCALSTACK_ENDPOINT"] = f"http://127.0.0.1:{endpoints['localstack']}"
    if "redis" in endpoints:
        published["REDIS_HOST"] = "127.0.0.1"
        published["REDIS_PORT"] = str(endpoints["redis"])
    return published


def pin_fake_aws_credentials(environ: MutableMapping[str, str]) -> dict[str, str]:
    """Pop `AWS_PROFILE` and pin fake credentials. Returns what it changed.

    The AWS-credential-leak guard. A developer with a real `AWS_PROFILE`
    exported runs the suite and boto3 resolves THAT profile for any client whose
    endpoint override is missing -- so a test that meant to talk to LocalStack
    talks to an account instead, and the first thing anyone notices is a bill or a
    mutated bucket. Popping the profile is what makes the failure loud: with no
    credentials at all, a misrouted client raises instead of succeeding.

    It returns its changes rather than performing them silently so the arm that
    proves it can assert what moved.
    """
    changed: dict[str, str] = {}
    if environ.pop("AWS_PROFILE", None) is not None:
        changed["AWS_PROFILE"] = "<popped>"
    for key, value in (
        ("AWS_ACCESS_KEY_ID", "test"),
        ("AWS_SECRET_ACCESS_KEY", "test"),
        ("AWS_DEFAULT_REGION", "us-east-1"),
        ("AWS_REGION", "us-east-1"),
    ):
        if environ.get(key) != value:
            environ[key] = value
            changed[key] = value
    return changed


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------


def _compose_file(root: Path) -> Path:
    return root / "deploy" / "compose.yml"


def _compose(
    *args: str, project: str, root: Path, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(_compose_file(root)), *args],
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, **(env or {})},
    )


#: `<host>:<port>` and nothing else. Anchored at both ends on purpose: docker
#: prints its own diagnostics to STDOUT at exit 0, and `invalid IP:0` satisfies
#: every unanchored test you would reach for -- it is non-empty, it contains a
#: colon, and it ends in an integer. Measured 2026-09-18 (A3).
PUBLISHED = re.compile(r"^(?P<host>[0-9.:\[\]a-fA-F]+):(?P<port>\d+)$")


def _pick_container_name(stdout: str, project: str, service: str = "postgres") -> str:
    """The first line that looks like one of THIS project's containers.

    Observed rather than hypothesised (A3): a version that took the first
    non-empty line returned `0.0.0.0:0` as a container name and produced the
    remedy `docker rm -f 0.0.0.0:0`. A remedy nobody can run is worse than no
    remedy -- it reads as a precise instruction. Pure, so its arm can hand it that
    exact string.
    """
    for line in stdout.splitlines():
        if line.strip().startswith(f"{project}-"):
            return line.strip()
    return f"{project}-{service}-1"


def _resolve_published_port(
    stdout: str, returncode: int, container: str, service: str = "postgres"
) -> int:
    """What docker SAID, turned into a port or a refusal. No I/O of its own.

    THE REFUSAL NAMES THE CONTAINER, not the resource behind it. Everything this
    function can find wrong is a fact about a container and the caller's next
    action is to remove one; a message about ports or connections sends the
    reader to PostgreSQL, which is the one place the fault is not.
    """
    matched = PUBLISHED.match(stdout.strip())
    port = int(matched.group("port")) if matched else 0
    if returncode != 0 or not matched or not 0 < port < 65536:
        raise RealResourcesUnavailable(
            f"the {service} container {container!r} publishes no usable host port "
            f"(`docker compose port {service} ...` exited {returncode} and said "
            f"{stdout.strip()!r}). This is almost always a container left behind by a run "
            f"that crashed before teardown: `up -d` ADOPTS it, and a port of 0 makes a "
            f"client fall back to the conventional port and report a connection failure "
            f"against a port nobody asked for. Remove it and re-run:\n    docker rm -f {container}"
        )
    return port


def _refuse_a_port_nothing_listens_on(port: int, container: str) -> None:
    """Dial the port. It is the medium every caller's verdict is read through.

    The range check above catches the measured instance; this catches the class. A
    port bound on a different interface, or a container that died between
    `up --wait` and `port`, both parse perfectly and both fail later as a
    connection error inside a test, where the container is no longer in view.
    """
    with contextlib.closing(socket.socket()) as probe:
        probe.settimeout(5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return
    raise RealResourcesUnavailable(
        f"the postgres container {container!r} reports host port {port}, but nothing accepts "
        f"a connection there. The container is up and unreachable, which a later query would "
        f"report as a database failure. Remove it and re-run:\n    docker rm -f {container}"
    )


def _published_port(project: str, root: Path, service: str = "postgres", inside: int = 5432) -> int:
    """The host port `service` publishes for its container port `inside`.

    Generic over the service because EVERY resource the kit starts needs an
    ephemeral host port, not just PostgreSQL. A fragment pinned to its
    conventional port (LocalStack's 4566, Redis's 6379) cannot be started twice
    on one machine, so a second worktree's run dies at `up -d` with a bind
    conflict -- and `compose_project_name` hashing the checkout path, which is
    what makes two checkouts independent, does nothing about a port they both
    ask the kernel for by number.
    """
    listed = _compose("ps", "--format", "{{.Name}}", service, project=project, root=root)
    container = _pick_container_name(listed.stdout, project, service)
    published = _compose("port", service, str(inside), project=project, root=root)
    port = _resolve_published_port(published.stdout, published.returncode, container, service)
    _refuse_a_port_nothing_listens_on(port, container)
    return port


def _bring_up(
    declaration: ResourceDeclaration, project: str, root: Path
) -> tuple[str | None, dict[str, int]]:
    """`up -d --wait` the DECLARED services, and return the maintenance URL.

    `--wait` is what makes readiness each container's OWN healthcheck rather than
    a TCP probe of ours. The postgres fragment runs `pg_isready`, which is the
    difference that matters: postgres accepts connections during startup recovery
    and then refuses queries, so a port check goes green too early.

    Only the declared services are named, so a compose file that also `include:`s
    LocalStack does not pull a 1 GB image for a service that declared no AWS.
    """
    if not declaration.resources:
        return None, {}
    compose_file = _compose_file(root)
    if not compose_file.is_file():
        raise RealResourcesUnavailable(
            f"{compose_file} does not exist, but {declaration.service} declares "
            f"[tool.foundry.test-resources] = {list(declaration.resources)}. Either add the "
            f"compose file or remove the declaration -- a declared resource with no way to "
            f"start it is a real arm that can never run."
        )
    started = _compose(
        "up",
        "-d",
        "--wait",
        *declaration.resources,
        project=project,
        root=root,
        # Every publishing fragment binds `${<NAME>_PORT:-<conventional>}`, and
        # `0` lets the kernel choose. A fixed port collides with a developer's own
        # server and with a second worktree, and both failures read as "the
        # resource is broken" rather than "two runs asked for one port".
        env=_ephemeral_port_env(),
    )
    if started.returncode != 0:
        raise RealResourcesUnavailable(
            f"`docker compose -p {project} up -d --wait {' '.join(declaration.resources)}` "
            f"exited {started.returncode}. Start Docker Desktop and re-run, or set "
            f"{DATABASE_URL_ENV} to a server this run should use instead. "
            f"stderr: {started.stderr.strip()[:500]}"
        )
    endpoints = {
        name: _published_port(project, root, name, inside)
        for name, (inside, _) in ENDPOINT_CONTRACT.items()
        if name in declaration.resources
    }
    if not declaration.wants_postgres:
        return None, endpoints
    port = _published_port(project, root)
    return (
        f"postgresql+psycopg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
        f"@127.0.0.1:{port}/{MAINTENANCE_DATABASE}",
        endpoints,
    )


def _tear_down(project: str, root: Path) -> None:
    """`down -v`, scoped to this project name and never by pattern.

    Scoped, because `docker rm` by pattern is how one worktree's teardown killed
    another's database. Only ever called for a project this
    run CLAIMED in `_start`, so a GUEST on `FOUNDRY_TEST_DATABASE_URL` never
    reaches it.
    """
    if os.environ.get(KEEP_RESOURCES_ENV) == "1":
        return
    try:
        _compose(
            "down", "-v", "--remove-orphans", project=project, root=root,
            env=_ephemeral_port_env(),
        )
    except FileNotFoundError:
        # No `docker` binary, so the `up` this run claimed never started anything.
        return


# ---------------------------------------------------------------------------
# Databases
# ---------------------------------------------------------------------------


def _admin_engine(admin_url: str):
    """A short-lived AUTOCOMMIT engine on the maintenance database.

    `CREATE DATABASE` cannot run inside a transaction block and SQLAlchemy opens
    one by default, so the isolation level is the whole reason this helper exists.
    Every caller disposes it in a `finally`: a pooled admin connection left open
    is itself a `pg_stat_activity` row, and this module's own control counts those.
    """
    from sqlalchemy import create_engine

    return create_engine(admin_url, isolation_level="AUTOCOMMIT")


def _url_for(admin_url: str, database: str) -> str:
    """The same server, a different database -- with the password INTACT.

    `render_as_string(hide_password=False)`, never `str(url)`. SQLAlchemy's
    `URL.__str__` masks the password as `***`, so `str(make_url(u).set(...))`
    returns a URL that LOOKS right and cannot authenticate. Measured here on the
    first run: the migration subprocess was handed
    `postgresql+psycopg://postgres:***@127.0.0.1:55624/template_0` and PostgreSQL
    answered `FATAL: password authentication failed for user "postgres"` -- which
    reads as a credential problem in the compose fragment, and sends the reader to
    the one place the fault is not. The in-process admin engine connected fine
    from the unmasked original, so the two halves disagreed about a URL that
    printed identically in every log line.
    """
    from sqlalchemy.engine import make_url

    return make_url(admin_url).set(database=database).render_as_string(hide_password=False)


def _database_exists(admin_url: str, database: str) -> bool:
    from sqlalchemy import text

    engine = _admin_engine(admin_url)
    try:
        with engine.connect() as connection:
            found = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database}
            ).scalar()
        return found is not None
    finally:
        engine.dispose()


def session_count(admin_url: str, database: str) -> int:
    """`pg_stat_activity` rows for one database. The A4 named control, as a unit.

    Exposed rather than inlined because it is read at three different moments --
    before the clone, and before and after a full run -- and a control somebody
    has to re-implement at each site is a control that will disagree with itself.
    """
    from sqlalchemy import text

    engine = _admin_engine(admin_url)
    try:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE datname = :name"),
                    {"name": database},
                ).scalar()
                or 0
            )
    finally:
        engine.dispose()


class RunClaim:
    """This run's hold on one PostgreSQL server, until `release`.

    A SESSION-level advisory lock on a connection kept open for the whole run.
    Session-level is the point: the lock goes when the connection goes, so a run
    killed with SIGKILL leaves nothing for the next one to clear. A lock FILE would
    outlive the process that wrote it, and would not be seen by a run in another
    checkout that reaches the same server through `FOUNDRY_TEST_DATABASE_URL`.
    """

    def __init__(self, engine, connection, key: int) -> None:  # noqa: ANN001 - sqlalchemy handles
        self._engine = engine
        self._connection = connection
        self._key = key

    def release(self) -> None:
        """Give the server back, and have it be free when this returns.

        The unlock is asked for before the close. Closing alone frees the lock only
        once PostgreSQL has ended the backend, which is after `close()` returns: the
        session was still listed straight after a release in 23 of 600 tries on a
        loaded host. A connection that is already gone took the
        lock with it, so a failed unlock is not an error here.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        try:
            with contextlib.suppress(SQLAlchemyError):
                self._connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": self._key}
                )
            self._connection.close()
        finally:
            self._engine.dispose()


def lock_holders(connection, key: int) -> list:  # noqa: ANN001 - a sqlalchemy connection
    """The sessions that HOLD advisory lock `key` in this connection's database.

    Granted rows only: a session waiting for the lock has a row too, and it is not
    the holder. This database only: advisory locks are per database, and the same
    key held in another one is another lock.
    """
    from sqlalchemy import text

    return list(
        connection.execute(
            text(
                "SELECT l.pid, a.application_name, a.backend_start FROM pg_locks l "
                "JOIN pg_stat_activity a ON a.pid = l.pid "
                "WHERE l.locktype = 'advisory' AND l.granted AND l.classid = 0 "
                "AND l.objid = :key AND l.objsubid = 1 "
                "AND a.datname = current_database()"
            ),
            {"key": key},
        )
    )


def _describe_holder(holders: list) -> str:
    if not holders:
        return "a run that ended between the refusal and the question"
    holder = holders[0]
    if holder.application_name is None or holder.backend_start is None:
        # PostgreSQL blanks another role's session for a role without
        # pg_read_all_stats. The pid is still there.
        return f"backend pid {holder.pid}, whose session this role may not read"
    # The reader's clock, with its zone: the server's is UTC in the kit's container.
    since = holder.backend_start.astimezone()
    return f"{holder.application_name!r}, connected since {since:%H:%M:%S %Z}"


def _who_holds(connection, key: int) -> str:  # noqa: ANN001 - a sqlalchemy connection
    """Who holds `key`, for a refusal that has already been decided.

    PostgreSQL has said no by the time this is asked, so a failure to name the
    holder goes into the answer. Raised as itself it would not be read as a refusal,
    and a run that adopted the other run's stack would go on to take it down.
    """
    from sqlalchemy.exc import SQLAlchemyError

    try:
        return _describe_holder(lock_holders(connection, key))
    except SQLAlchemyError as exc:
        return f"a holder this run could not name ({type(exc).__name__})"


def claim_server(admin_url: str, project: str, *, key: int = RUN_LOCK_KEY) -> RunClaim:
    """Hold `admin_url`'s server for this run, or refuse and name who holds it.

    `pg_try_advisory_lock`, never the waiting form. A second run that waited would
    sit silent for as long as the first one runs, which reads as a hang.

    The lock is taken on the MAINTENANCE database, because advisory locks are
    per database and that is the one every run on a server connects to.

    Once PostgreSQL has said no, the answer is `ServerInUse`; see `_who_holds`.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    if not 0 <= key < 2**32:
        # `pg_locks` shows a one-argument key as (classid, objid). Below 2**32 that
        # is (0, key), which is what `lock_holders` asks for.
        raise ValueError(f"an advisory lock key must be in [0, 2**32), got {key}")

    # PostgreSQL keeps the first 63 bytes of the name; the process id comes first.
    name = f"{RUN_APPLICATION_PREFIX} {os.getpid()} {project}"
    engine = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
        connect_args={"application_name": name},
    )
    connection = None
    claimed = False
    try:
        connection = engine.connect()
        if connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar():
            claimed = True
            return RunClaim(engine, connection, key)
        who = _who_holds(connection, key)
    finally:
        # One way out for a refusal and for a failure: neither keeps a session.
        if not claimed:
            if connection is not None:
                connection.close()
            engine.dispose()
    raise ServerInUse(
        f"another test run holds this PostgreSQL server: {who}. Every run uses the same "
        f"database names on a server ({TEMPLATE_DATABASE!r} and the per-worker clones), so "
        f"this run would drop the other one's databases while it is using them. Wait for "
        f"it to finish, or point {DATABASE_URL_ENV} at a server no other run is using."
    )


def _assert_template_is_idle(admin_url: str) -> None:
    """Refuse a clone the template cannot serve, with the leak named.

    PostgreSQL refuses `CREATE DATABASE ... TEMPLATE t` while any session is
    attached to `t`, and its message is about the template, several steps from the
    cause. The cause is always the same: something in the migration path left a
    connection open. `migrations/env.py` disposing its engine in a `finally` is
    what makes this pass, and this assertion is what makes that a CHECKED property
    of the next service's `env.py` rather than a remembered one.
    """
    attached = session_count(admin_url, TEMPLATE_DATABASE)
    if attached:
        raise RealResourcesUnavailable(
            f"{attached} session(s) are still attached to {TEMPLATE_DATABASE} after "
            f"`alembic upgrade head` exited. PostgreSQL refuses `CREATE DATABASE ... "
            f"TEMPLATE {TEMPLATE_DATABASE}` while the template has any session, so every "
            f"worker's first action would fail with a message about the template rather "
            f"than about the leak. The leak is in this service's migrations/env.py: it must "
            f"dispose its engine in a `finally`, or use a NullPool inside `with connect()`."
        )


def _create_template(admin_url: str, root: Path) -> None:
    """Create `template_0` and migrate it to head, once per run.

    `alembic upgrade head` AS A SUBPROCESS, never `Base.metadata.create_all`.
    `create_all` builds the schema the models describe; the migrations are what a
    deployment applies, and the gap between the two is the defect worth catching.
    A subprocess also guarantees its connections are gone when it exits, which is
    what `_assert_template_is_idle` then verifies rather than assumes.
    """
    from sqlalchemy import text

    engine = _admin_engine(admin_url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{TEMPLATE_DATABASE}"'))
            connection.execute(text(f'CREATE DATABASE "{TEMPLATE_DATABASE}"'))
    finally:
        engine.dispose()
    _migrate(_url_for(admin_url, TEMPLATE_DATABASE), root)
    _assert_template_is_idle(admin_url)


def _migrate(url: str, root: Path) -> None:
    """`alembic upgrade head` against `url`, as a subprocess.

    `sys.executable -m alembic`, never the bare console script. The script lives
    in the venv's `bin/`, which `python -m pytest` does NOT put on PATH -- so a
    suite invoked as `.venv/bin/python -m pytest` raises
    `FileNotFoundError: 'alembic'` for every real-database test, which reads as a
    missing dependency rather than as an unexported PATH. The interpreter running
    the tests is the interpreter that has alembic installed, so asking it directly
    removes the ambient dependency entirely.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "DATABASE_URL": url},
    )
    if result.returncode != 0:
        # NOT a skip. The server answered; the schema did not apply. That is a
        # defect in this repository, and reporting it as an absent resource would
        # hide a broken migration inside a green run.
        raise RuntimeError(
            f"`alembic upgrade head` exited {result.returncode} against {url!r}.\n"
            f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )


def clone_for_worker(admin_url: str, worker_id: str) -> str:
    """`CREATE DATABASE test_<gwN> TEMPLATE template_0`, and return its URL.

    THE ADVISORY LOCK IS NOT OPTIONAL under xdist. Two workers issuing the clone
    at the same instant can each be the reason the other is refused -- PostgreSQL
    reports "source database is being accessed by other users", which reads
    exactly like the leaked-migration-connection failure above and is a different
    fault entirely. Serialising them costs milliseconds and removes a flake whose
    two causes are indistinguishable from the message.
    """
    from sqlalchemy import text

    database = worker_database_name(worker_id)
    engine = _admin_engine(admin_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": CLONE_LOCK_KEY})
            try:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
                connection.execute(
                    text(f'CREATE DATABASE "{database}" TEMPLATE "{TEMPLATE_DATABASE}"')
                )
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": CLONE_LOCK_KEY}
                )
    finally:
        engine.dispose()
    return _url_for(admin_url, database)


def owned_tables(metadata_ref: str) -> tuple[str, ...]:
    """The truncate set, DERIVED from `Base.metadata` and never hand-written.

    A hand-written list is the estate's enumerate-the-artifact defect in miniature:
    a table added in a migration and mapped in a record class is not truncated,
    so its rows survive into the next test and the leak shows up somewhere else
    entirely. `sorted_tables` is dependency-ordered, and `CASCADE` makes the order
    irrelevant anyway -- it is used because it is deterministic, so a failure
    message names the same tables every run.

    TRUNCATING A DERIVED SET IS ONLY SAFE BECAUSE THE DATABASE IS PRIVATE. A
    per-worker database cloned from a template that only ever held this service's
    migrations contains this service's tables and nothing else, so "every table in
    the mapping" and "every table in the database" are the same set. A shared
    database would make the derivation dangerous, which is the argument the A3
    stand-in made for hand-writing it; the per-worker database is what answers it.

    Refuses an empty mapping: a TRUNCATE over no tables succeeds, isolates
    nothing, and reads as a working harness.
    """
    import importlib

    module_name, _, attribute = metadata_ref.partition(":")
    if not module_name or not attribute:
        raise RealResourcesUnavailable(
            f"orm_metadata must be 'module:Attribute', got {metadata_ref!r}"
        )
    module = importlib.import_module(module_name)
    base = getattr(module, attribute, None)
    if base is None:
        raise RealResourcesUnavailable(
            f"orm_metadata {metadata_ref!r}: {module_name} has no attribute {attribute!r}"
        )
    metadata = getattr(base, "metadata", base)
    # REFUSED BY SHAPE, because the near miss is the likely mistake. `orm_metadata`
    # accepts either a `DeclarativeBase` subclass (whose `.metadata` is taken) or a
    # `MetaData` instance. Pointing it at the `MetaData` CLASS -- an easy thing to
    # write, and what `sqlalchemy:MetaData` resolves to -- yields an object whose
    # `sorted_tables` is an unbound property, and iterating it raises `TypeError:
    # 'property' object is not iterable` from inside the kit. That traceback says
    # nothing about the configuration line that produced it.
    sorted_tables = getattr(metadata, "sorted_tables", None)
    if not isinstance(sorted_tables, list):
        raise RealResourcesUnavailable(
            f"orm_metadata {metadata_ref!r} resolved to {metadata!r}, which is not a "
            f"MetaData instance and has no `metadata` attribute. Point it at your "
            f"`DeclarativeBase` subclass (e.g. '<package>.adapters.real.orm:Base') or at "
            f"a MetaData INSTANCE -- not at the MetaData class."
        )
    tables = tuple(table.name for table in sorted_tables)
    if not tables:
        raise RealResourcesUnavailable(
            f"orm_metadata {metadata_ref!r} maps no tables, so TRUNCATE would isolate "
            f"nothing while exiting 0. A record module that is never imported is the usual "
            f"cause: `adapters/real/orm/__init__.py` must import every record class."
        )
    return tables


def truncate(engine, tables: Sequence[str]) -> None:
    """`TRUNCATE ... RESTART IDENTITY CASCADE`, under a lock timeout.

    THE LOCK TIMEOUT IS THE DIFFERENCE BETWEEN A DIAGNOSIS AND A HANG. `TRUNCATE`
    takes an ACCESS EXCLUSIVE lock, so a previous test that left a session open
    with an uncommitted write blocks it FOREVER -- the suite stops with no output,
    no failing test and no timeout, which is the least debuggable outcome
    available. Three seconds turns that into a named error against the test that
    is about to run, and the leak is then a line number away.
    """
    from sqlalchemy import text

    if not tables:
        raise RealResourcesUnavailable("truncate() was given no tables; it would isolate nothing")
    with engine.begin() as connection:
        connection.execute(text("SET lock_timeout = '3s'"))
        connection.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))


# ---------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------

_PROFILE_KEY = "_foundry_real_profile"
_REMEDY_KEY = "_foundry_real_remedy"
#: The compose project this run must `down -v` in `pytest_unconfigure`. Kept apart
#: from `_PROFILE_KEY` because the profile exists only after a COMPLETE start, and
#: containers exist from the moment `up` is asked for.
_OWNED_PROJECT_KEY = "_foundry_real_owned_project"
#: This run's `RunClaim`, released in `pytest_unconfigure`.
_RUN_CLAIM_KEY = "_foundry_real_run_claim"


def _start(config: pytest.Config) -> None:
    """Controller-side bring-up. Runs exactly once per RUN, never per worker."""
    declaration = load_declaration(ROOT)
    project = compose_project_name(ROOT, declaration.service)
    if any(name.startswith(("localstack", "minio")) for name in declaration.resources):
        pin_fake_aws_credentials(os.environ)

    endpoints: dict[str, int] = {}
    override = os.environ.get(DATABASE_URL_ENV)
    if override:
        # A GUEST on somebody else's server: no bring-up, and no teardown either.
        profile = RealProfile(
            project=project,
            admin_url=_url_for(override, MAINTENANCE_DATABASE),
            resources=declaration.resources,
            orm_metadata_ref=declaration.orm_metadata,
        )
    else:
        # CLAIMED BEFORE `up`, not after the start completes. An
        # `up --wait` that times out on a loaded host, a port refusal, or a failed
        # template all raise AFTER containers exist; claiming only on success left
        # each of those stacks running with nothing that would ever `down` it, and
        # once pytest rotated its basetemp the compose file was gone too.
        if declaration.resources:
            setattr(config, _OWNED_PROJECT_KEY, project)
        admin_url, endpoints = _bring_up(declaration, project, ROOT)
        profile = RealProfile(
            project=project,
            admin_url=admin_url,
            resources=declaration.resources,
            orm_metadata_ref=declaration.orm_metadata,
            endpoints=endpoints,
        )
    # The controller runs tests too when there is no xdist, so it needs the same
    # environment the workers are handed. Published here rather than only in
    # `pytest_configure_node`, which xdist calls and a plain run never does.
    os.environ.update(endpoint_environment(profile.endpoints))
    if profile.admin_url is not None:
        # BEFORE the template, which is the first thing this run drops. A guest and
        # a run that adopted a live stack in its own checkout both arrive here on a
        # server somebody else may be using, and neither can tell from the bring-up.
        setattr(config, _RUN_CLAIM_KEY, claim_server(profile.admin_url, project))
        _create_template(profile.admin_url, ROOT)
    setattr(config, _PROFILE_KEY, profile)


def pytest_configure(config: pytest.Config) -> None:
    """Bring the declared resources up ONCE, in the controller.

    `not hasattr(config, "workerinput")` is the controller test. A session-scoped
    fixture cannot do this job: under xdist it runs in every worker and never in
    the controller, so N workers would race for one project name and the last
    `down -v` would run while others were still querying.
    """
    if hasattr(config, "workerinput"):
        payload = config.workerinput.get("foundry_real")  # type: ignore[attr-defined]
        if payload is not None:
            profile = RealProfile.from_dict(payload)
            setattr(config, _PROFILE_KEY, profile)
            # A worker is a separate PROCESS: the controller's `os.environ` update
            # did not reach it, so the endpoint variables are republished from the
            # payload. Without this a worker's `aws_kit` would fall back to the
            # conventional 4566, which is either nothing or -- worse -- another
            # worktree's LocalStack.
            os.environ.update(endpoint_environment(profile.endpoints))
        return

    if config.option.collectonly:
        # `--collect-only` runs no test, so no fixture can ask for a resource, and a
        # bring-up here is a full `up --wait` / `down -v` cycle (~10 s) that nothing
        # reads. `prove_seeded_tests.py` counts every arm's selection this way, which
        # made half of its Docker cycles this one.
        return

    try:
        _start(config)
    except ServerInUse as exc:
        # THE STACK IS NOT THIS RUN'S TO TAKE DOWN. `_start` claims the compose
        # project before `up`, and here `up` adopted containers another run is
        # using. Left claimed, `pytest_unconfigure` would `down -v` them, and the
        # refusal that exists to protect the other run would be what destroyed it.
        if hasattr(config, _OWNED_PROJECT_KEY):
            delattr(config, _OWNED_PROJECT_KEY)
        raise pytest.UsageError(str(exc)) from exc
    except (RealResourcesUnavailable, OSError) as exc:
        remedy = str(exc)
        setattr(config, _REMEDY_KEY, remedy)
        if os.environ.get(REQUIRE_ENV) == "1":
            # A HARD ERROR AT CONFIGURE TIME, not a fixture-time failure. A run
            # whose real arm could not start is not entitled to report anything,
            # including the part that did run -- and a fixture-time failure would
            # let a suite that happens not to use the fixture report success.
            raise pytest.UsageError(
                f"{REQUIRE_ENV}=1 and the real arm could not start: {remedy}"
            ) from exc
        print(f"\nreal arm skipped: {remedy}")
        return

    print(f"\nreal arm ran: {load_declaration(ROOT).describe()}", flush=True)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:  # noqa: ANN001 - xdist's own hook signature
    """Hand the controller's endpoints to each worker.

    `workerinput` is the only channel: an xdist worker is a separate process with
    its own `pytest_configure`, so anything the controller learned has to be sent
    rather than recomputed. Recomputing would mean each worker bringing its own
    stack up, which is the defect this plugin exists to remove.

    `optionalhook=True` IS WHAT LETS THIS PLUGIN LOAD WITHOUT XDIST, and without
    it the module's mere presence made xdist a hard requirement of every service
    the kit ships to. `pytest_configure_node` is xdist's hookspec, and pluggy
    validates hook NAMES when a plugin is registered -- so with no xdist there is
    no spec, the name is unknown, and the run dies with
    `PluginValidationError: unknown hook 'pytest_configure_node'` at exit 3
    before a single test executes. Not a skip and not a failing test: an
    INTERNALERROR over the whole suite, which is the least debuggable shape
    available and points at a plugin rather than at a missing dependency.

    THE OBVIOUS GUARD IS THE WRONG ONE, measured rather than reasoned. Defining
    the hook conditionally on `find_spec("xdist")` fixes the not-installed case
    and leaves `-p no:xdist` still at exit 3 with the identical error, because a
    BLOCKED plugin is importable and contributes no hookspec. `optionalhook`
    keys on the spec's absence instead of the module's, which is the condition
    that actually governs, and it is inert when xdist is present: the hook still
    fires under `-n 2`, proved by an arm that writes a marker from inside it
    against a plain-hook control that does the same.
    """
    profile = getattr(node.config, _PROFILE_KEY, None)
    if profile is not None:
        node.workerinput["foundry_real"] = profile.as_dict()


def pytest_unconfigure(config: pytest.Config) -> None:
    """`down -v` in a `finally`-equivalent: pytest calls this even after a failure,
    including a start that failed or raised `UsageError` inside `pytest_configure`.

    It cannot run on SIGKILL, which is what `subprocess.run(timeout=...)` sends; a
    caller that can kill a run that way owns the teardown of what the run started.
    """
    if hasattr(config, "workerinput"):
        return
    claim = getattr(config, _RUN_CLAIM_KEY, None)
    if claim is not None:
        # Before the teardown, so a kept stack (`FOUNDRY_KEEP_RESOURCES=1`) or a
        # guest's server is free for the next run the moment this one ends.
        claim.release()
    project = getattr(config, _OWNED_PROJECT_KEY, None)
    if project is not None:
        _tear_down(project, ROOT)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def real_profile(request: pytest.FixtureRequest) -> RealProfile:
    """What the controller brought up, or a NAMED skip carrying the remedy."""
    profile = getattr(request.config, _PROFILE_KEY, None)
    if profile is None:
        remedy = getattr(request.config, _REMEDY_KEY, "the real arm did not start")
        pytest.skip(f"real arm skipped: {remedy}")
    return profile


@pytest.fixture(scope="session")
def worker_id(request: pytest.FixtureRequest) -> str:
    workerinput = getattr(request.config, "workerinput", None)
    return "main" if workerinput is None else str(workerinput.get("workerid", "main"))


@pytest.fixture(scope="session")
def real_postgres(real_profile: RealProfile, worker_id: str) -> RealPostgres:
    """This worker's own database, cloned from the migrated template.

    Session-scoped by necessity rather than by preference: the clone is a file
    copy inside the server, and doing it per test would make the real arm slow
    enough that somebody turns it off. Isolation between tests comes from
    `postgres_sessions`, which truncates.
    """
    if real_profile.admin_url is None:
        pytest.skip(
            "real arm skipped: this service declares no 'postgres' test resource "
            "in [tool.foundry.test-resources]"
        )
    return RealPostgres(
        url=clone_for_worker(real_profile.admin_url, worker_id), project=real_profile.project
    )


@pytest.fixture(scope="session")
def worker_engine(real_postgres: RealPostgres) -> Iterator[object]:
    """ONE `Engine` for this worker, disposed once.

    An `Engine` owns a connection pool. A function-scoped engine gives every test
    its own pool, and a suite exhausts `max_connections` partway through as a
    connection error attributed to whichever test was unlucky. The proof is
    `pg_stat_activity` totals before and after a full run, which must be equal --
    see `tests/integration/test_real_profile_connection_accounting.py`.
    """
    from sqlalchemy import create_engine

    engine = create_engine(real_postgres.url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def postgres_sessions(worker_engine, real_profile: RealProfile) -> Iterator[object]:
    """A session factory on this worker's database, over tables truncated for this test."""
    from sqlalchemy.orm import sessionmaker

    reference = real_profile.orm_metadata_ref
    if reference is None:
        # Unreachable through `real_postgres`, which skips first -- and stated as
        # a refusal rather than an `assert`, because `python -O` strips asserts
        # and this one guards a TRUNCATE.
        raise RealResourcesUnavailable(
            "postgres_sessions needs orm_metadata to derive the truncate set; "
            "[tool.foundry.test-resources] declares none."
        )
    truncate(worker_engine, owned_tables(reference))
    yield sessionmaker(bind=worker_engine, expire_on_commit=False)
