# foundry-service-template

A reference backend service that runs, unmodified, as an **AWS Lambda (image package
type)** or an **ECS Fargate task**: one Docker image, one ECR repository, two thin IaC
wrappers. `scripts/new_service.sh` emits a new, self-contained service from it.

It ships a small worked domain (`widget`: create, get, list, one validation rule) so
every layer is exercised by code that actually runs. Delete the domain; keep the shape.

What you get:

- **One image, two targets.** `service-kit/docker/Dockerfile` builds the image both
  targets run, with the Lambda Web Adapter so the same HTTP app serves Lambda events.
  Two Dockerfiles for one service drift silently (different dependency resolution,
  different users); one file cannot.
- **Seven layers, one direction**, enforced by gates in `service-kit/gates/` (see
  "Service module contract" below).
- **Real test resources, not mocks**: PostgreSQL, Redis and AWS endpoints run as
  real servers in tests (`foundry_testkit`), each xdist worker on its own database.
- **Hash-locked dependencies** for both the image and the test tools
  (`requirements/*.lock`, installed with `--require-hashes`).
- **CI that spends nothing until you ask**: lint, gates, tests, IaC validation and an
  image build on every push; deploy only on a manual dispatch behind a GitHub
  environment protection rule.
- **A shared kit** (`service-kit/`) that emitted services keep in sync with
  `service-kit/sync.py`, checked by `check_kit_drift.py`.

Licence: MIT (see `LICENSE`).

## Clone to running container, fastest path

```bash
# from this repository's root, installing exactly as CI's Install step does.
# foundry-contracts is VENDORED under service-kit/vendor/foundry_contracts
# and resolved through PYTHONPATH, exactly as CI and the container resolve it --
# it is a source tree, not a distribution. pytest finds it without the export
# (pyproject's own `pythonpath` names it); the running service does not.
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements/service.lock
pip install --no-deps -e .
pip install --require-hashes -r requirements/test.lock
pip install ruff
export PYTHONPATH=service-kit/vendor

pytest tests
ruff check src tests scripts service-kit

# run the service process directly, outside its container. It still needs a
# real PostgreSQL: DATABASE_URL is required, and SQLite is not a supported
# runtime (see "The contract suite runs on a real server" below). Pick ONE:
#
#   a) no Docker at all -- a PostgreSQL 16 you already run natively:
#        createdb service_test
#        export DATABASE_URL=postgresql+psycopg://$USER@127.0.0.1:5432/service_test
#
#   b) Docker for the database only -- the kit's disposable PostgreSQL:
#        docker compose -f deploy/compose.yml up -d --wait postgres
#        export DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/service_test
#
# SERVICE_ENV has no default: `local` is what accepts the unverified x-* principal
# headers the curl below sends, so a deployment has to name its stage on purpose.
export SERVICE_ENV=local
python -m alembic upgrade head
PORT=8080 python -m foundry_service_template.composition.main
curl localhost:8080/healthz
curl -X POST localhost:8080/widgets \
  -H 'x-org-id: org_1' -H 'x-subject-id: user_1' -H 'x-scopes: widgets:write' \
  -H 'content-type: application/json' -d '{"name":"Sprocket"}'
```

```bash
# or run the real container. deploy/compose.yml is the one place this service's build
# args live (SERVICE_PACKAGE, HAS_MIGRATIONS); CI builds through it too. Run it through
# compose rather than a bare `docker run`: compose supplies SERVICE_ENV and
# DATABASE_URL, without which the config refuses to start, and the database itself.
# Readiness stays 503 until the schema exists, so migrate once with the same image --
# the entrypoint runs an explicit command instead of the HTTP service.
# Set POSTGRES_PORT / SERVICE_PORT if 5432 / 8080 are already taken on this host.
docker compose -f deploy/compose.yml build service
docker compose -f deploy/compose.yml up -d --wait postgres
docker compose -f deploy/compose.yml run --rm service python -m alembic upgrade head
docker compose -f deploy/compose.yml up -d --wait service
curl localhost:8080/health/ready
docker compose -f deploy/compose.yml down -v
```

The image installs its third-party dependencies with
`pip install --require-hashes -r requirements/service.lock`; this package's
own code is COPY'd into the image directly (never pip-installed there), so
the lockfile only has to cover genuine PyPI packages. Regenerate it after
changing `requirements/service.in`:

```bash
pip install pip-tools
pip-compile --generate-hashes --output-file=requirements/service.lock \
  requirements/service.in
```

## The two deploy targets

Both come from the **same image URI** -- an ECR digest, never a floating tag.

| Target | IaC | Notes |
| --- | --- | --- |
| Lambda (image package type) | `deploy/iac/serverless.yml` | Default for a new service. One rule on the estate's shared internal load balancer, and ONE function. A second, scheduled `drainOutbox` function used to ship here whose entire body imported a symbol and exited; it was deleted, and arm 6 of `scripts/check_service_iac.sh` now refuses any function whose command only imports. Declare a scheduled drain when your service needs one, with a command that CALLS an entrypoint rather than importing one. |
| ECS Fargate | `deploy/iac/app/` (release) and `deploy/iac/resources/` (durable) | Cluster, task definition, a target group and one rule on the shared internal load balancer. Move here when a service exceeds 15 minutes of work, needs a long-poll consumer, or its cost curve crosses over. |

### Ingress is private on both targets

Neither target creates a load balancer or a public API. The estate's
internal-edge stack owns one internal load balancer, its HTTPS listener, its
certificate and a private zone; a service adds a target group and a host-header
rule for `<service>.<zone suffix>`, and its tasks accept 8080 only from that load
balancer's security group. The template used to ship an `aws_lb` with
`internal = false` behind a security group open to `0.0.0.0/0`, and an API
Gateway HTTP API on the Lambda path. Both are deleted.

| Input | ECS (`deploy/iac/app`) | Lambda (`deploy/iac/serverless.yml`) |
| --- | --- | --- |
| Exposure (`private`, `edge_iam` or `public`; equals the service's row in the Foundry workspace's `scripts/service_directory.yaml`) | `var.exposure` | `custom.exposure`, a committed literal |
| The shared listener | `var.internal_listener_arn` | `INTERNAL_LISTENER_ARN`, declared in `deploy/deploy_env.json` |
| This service's rule priority, from the directory | `var.listener_rule_priority` | `LISTENER_RULE_PRIORITY`, a repository variable |
| The private zone | `var.internal_zone_suffix` | `INTERNAL_ZONE_SUFFIX`, declared in `deploy/deploy_env.json` |
| The load balancer's security group | `var.internal_alb_sg_id` | not needed: the load balancer invokes the function |
| The readiness route the target group checks | `var.ready_path` | not needed: a Lambda target has no health check |

On the Lambda path the listener and the zone are not typed into a repository. The
internal-edge stack publishes them as SSM parameters, and `deploy/deploy_env.json`
declares both:

```json
"INTERNAL_LISTENER_ARN": {"ssm": "/foundry/{stage}/network/internal-listener-arn"},
"INTERNAL_ZONE_SUFFIX": {"ssm": "/foundry/{stage}/network/internal-zone-suffix"}
```

`{stage}` is the deploy's target environment, so the edge stack's `Stage` parameter
must be the same word. `service-kit/ci/deploy_env.py check` reads both before the
image is pushed, and where the edge is not applied, or the deploy role may not read
the two parameters, the job stops there and names each. Like every declared value,
each is written to the job's log as an `::add-mask::` command before the deploy
command runs, which asks the runner to mask it in the lines that follow; the
service's private URL holds the zone. The rule priority stays a repository variable,
`LISTENER_RULE_PRIORITY`: it is this service's row, and nothing publishes it.

A service emitted before this change has a `serverless.yml` that reads the two and a
declaration that does not hold them. Once it takes the kit's workflow, which no
longer passes them, `deploy_env.py exec` refuses its deploy, naming both, until its
`deploy/deploy_env.json` declares them. That refusal is the deploy step's, after the
image is pushed: the check step passes a declaration that holds nothing. Where the
service's copy of `tests/shape/test_deploy_job_is_wired.py` holds the one-source
test, that test is red first. Its two repository variables can then be deleted.

`tests/shape/test_private_ingress.py` parses every Terraform file under
`deploy/iac` (`*.tf` and `*.tf.json`, in every directory) and `serverless.yml`, and
refuses an internet-facing load balancer, an ingress rule from `0.0.0.0/0` or
`::/0`, a task with a public IP, and an `httpApi` or `http` event while
`custom.exposure` is not `public`. A `*.tofu` or `*.tofu.json` file under
`deploy/iac` is refused whatever it holds: OpenTofu may load it as configuration, and
loads `main.tofu` in place of `main.tf`, while Terraform ignores it, so the test
does not read it. Write configuration as `.tf`. A `*.tofutest.hcl` or
`*.tofutest.json` file is also refused: OpenTofu runs it in place of the
`.tftest.hcl` or `.tftest.json` file of the same name, hiding the test a reviewer
reads. Keep one test source, `.tftest.hcl` or `.tftest.json`, for both binaries. A
symlink under `deploy/iac` is refused as well: OpenTofu follows it, and the test
does not.

The stack's `ApiUrl` is now the private name, which answers inside the VPC and
nowhere else. So the deploy job's smoke does not ask it: `deploy/smoke.json` declares
`"transport": "invoke"`, and `service-kit/ci/smoke.py` asks the function itself through
`aws lambda invoke`, with the event the load balancer would hand it. The same two arms
run, one success and one refusal with no credentials. A green smoke of that kind shows
the function answers and refuses; it does not exercise anything in front of the
function (the listener rule, the target group and its permission to invoke, the
security groups, DNS, TLS), and its last line says so.

What decides the transport is the URL, not `custom.exposure`: `deploy/iac/app` has
one ingress, the shared internal listener, for every value of it, and `ApiUrl` stays
the private name until a service re-points it. A service whose `ApiUrl` is not a
name in the private zone declares `"transport": "http"`.

`tests/shape/test_deploy_job_is_wired.py` holds the declaration to how that output is
WRITTEN. It does not resolve the URL and cannot tell where it answers. The convention
it reads: an `ApiUrl` whose `Value` names `${env:INTERNAL_ZONE_SUFFIX}` is a private
name and needs `invoke`; any other spelling is read as public and needs `http`. A
private name written another way (through `custom.*`, from SSM, as a literal, from
another variable or a stack parameter) is therefore read as public. Two things follow,
and the tests' messages say both:

- Keep a private name in the template's spelling. `invoke` over a private name written
  another way is refused, and the fix is the spelling, not `http`.
- A second test pins the output to `OWN_API_URL`, that file's record of what the stack
  writes, so a re-spelt private name does not pass as public unnoticed: the output
  cannot change without the record changing, and the test's message says what to check
  first. A service that re-points `ApiUrl` at a public URL sets `OWN_API_URL` to that
  value and declares `http`. That edit is the statement that the URL is public; no test
  can read it from the text.

The output is read only where `resources`, `Outputs` and `ApiUrl` are each a mapping
written in `serverless.yml`. A list of fragments or a `${file(...)}` reference there is
refused by name.

Neither target is applied by this template -- `.github/workflows/ci.yml`
gates the deploy job behind an explicit `workflow_dispatch` input and a
GitHub Environment approval. `deploy/iac/serverless.yml` and
`deploy/iac/app/*.tf` both read `IMAGE_URI` / `var.image_uri`; that variable
is the one seam between the two IaC tools.

Validated offline in this workspace (no real AWS account touched):

```
terraform fmt -check -diff   deploy/iac/app + deploy/iac/resources     -> clean, no diff
terraform init -backend=false && terraform validate  -> "Success! The configuration is valid."
python -c "import yaml; yaml.safe_load(open('deploy/iac/serverless.yml'))"  -> parses; IMAGE_URI reference confirmed
```

The Lambda target additionally installs the [AWS Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter)
as an extension layer inside the same image (see `Dockerfile`); it forwards
each Lambda invocation to the ordinary HTTP server this service already runs
for ECS, so `src/foundry_service_template/main.py` never branches on which
target it is running under. Both the Python base and the adapter layer are
pinned by digest, not a floating tag, and dependency installation is
hash-checked -- see "Motivation" above.

### Calling another private service

A service that calls another private service must carry the estate's caller
group. The shared internal load balancer admits HTTPS from that group, the
internal-edge stack's `EstateCallerSecurityGroupId` output. It admits one more
group only when the stack was given an `ExistingCallerSecurityGroupId`. A caller
in neither times out at the load balancer. Both targets take the caller group as
an opt-in. It is off by default, and off changes nothing.

| | ECS (`deploy/iac/app`) | Lambda (`deploy/iac/serverless.yml`) |
| --- | --- | --- |
| Switch | `var.estate_caller_sg_id`, empty by default | `custom.callerVpc: estate`, a committed literal; `none` by default |
| The caller group | set that variable to `EstateCallerSecurityGroupId` | `ESTATE_CALLER_SG_ID` |
| Subnets | nothing to set: the tasks already run in the VPC's `Tier = private` subnets | `PRIVATE_SUBNET_IDS`, comma-separated |
| What changes | the tasks carry the caller group beside their own | the function is attached to the VPC with the caller group |

The group is an egress-side membership on both targets. Neither adds an ingress
rule for it: the service still admits 8080 only from the load balancer's group.
`tests/shape/test_private_ingress.py` refuses any other source into the
service's group, in each of the three ways Terraform spells an ingress rule and
in every `*.tf` and `*.tf.json` file under `deploy/iac`: the release root, the
resources root, a local module. It refuses three more things, because it cannot
read past them:

- a group on an `aws_ecs_service`'s tasks other than their own and the caller
  group. Whatever a second group admits reaches the tasks, so it is refused
  without its rules being read. What the tasks need to reach goes in their own
  group's egress. A database that admits by group has to name the service's group
  in a rule written in the database's own stack. Read that id from the release
  root's `service_security_group_id` output. Here, a database client group on
  the tasks is refused, and so is a standalone rule on a group this repository is
  only given the id of: the test cannot tell that group from the service's own.
- a `module` call anywhere under `deploy/iac`. Its resources may be in another
  repository, where the test does not follow.
- a `*.tofu`, `*.tofu.json`, `*.tofutest.hcl` or `*.tofutest.json` file anywhere
  under `deploy/iac`, as above.
- a symlink anywhere under `deploy/iac` outside `.terraform`, to a file or a
  directory. What it points to would be loaded by OpenTofu and read by nothing.

Two things it does not read and does not refuse:

- A second launcher of the task definition. Only `aws_ecs_service` is read, so an
  EventBridge or scheduler target that runs the same tasks behind another group
  passes.
- A rule written through a generic resource. An `aws_cloudformation_stack` or an
  `aws_cloudcontrolapi_resource` that declares a security-group ingress on the
  service's group passes.

**ECS.** Set `estate_caller_sg_id`. A value that is not empty and not one
security group id (`sg-` and 8 or 17 hex digits) is refused at plan time.

**Lambda.** Without the switch the function runs outside the VPC and cannot
reach the internal load balancer at all, so the switch attaches it. Set `custom.callerVpc`
to `estate` and declare the two variables in `deploy/deploy_env.json`, for
example:

```json
"ESTATE_CALLER_SG_ID": {"ssm": "/foundry/{stage}/network/estate-caller-sg-id"},
"PRIVATE_SUBNET_IDS":  {"ssm": "/foundry/{stage}/network/private-subnet-ids"}
```

The internal-edge stack publishes the first, beside the listener and the zone. The
network foundation stack publishes the
second, a comma-separated list of the VPC's private subnets.

They go there because the workflow is a kit file a service does not edit, and
because of when each failure is caught:

- Not declared: `scripts/check_service_iac.sh` refuses `estate`, before the build.
- Declared, but the parameter is absent or empty: `deploy_env.py check` refuses,
  before the image is pushed.
- The framework's own refusal of an unset variable comes last, at
  `serverless deploy`, after the push. An empty value it does not refuse at all:
  it deploys as an empty id.

The switch is a committed literal in a separate file, `deploy/iac/caller_vpc.yml`,
because the framework has no cleaner way to say "off". It resolves every
variable in `serverless.yml` whether or not the value is used, so the two
variables kept there would be demanded from services that call nobody. And only
a null `provider.vpc` compiles to no VPC at all: an empty mapping still adds the
VPC access policy to the function's role. A value other than `none` or `estate`
would resolve to null without an error, so `scripts/check_service_iac.sh`
refuses one.

Do not attach a function with a `vpc:` of its own. It replaces the provider's
for that function, so the switch stops governing it: `none` would still be
attached, and `estate` could deploy without the caller group. The gate reads
every function and refuses both. A `VpcConfig` written under `resources`, in
`resources.extensions` or as a raw `resources.Resources` entry, does the same one
step later: the framework merges it over the compiled function under either value
of the switch. The gate refuses that too.

**What a VPC-attached function then needs.** It has no internet path of its own.
It reaches only what its security groups let out, and the caller group, as the
internal-edge stack defines it, lets out HTTPS inside the VPC: the internal load
balancer, and the interface endpoints that admit the group.

- Every AWS API the service's code calls (Secrets Manager, KMS, SQS and so on)
  needs its interface endpoint in the VPC. A NAT gateway serves instead only
  together with the second group in the next point: the caller group lets out
  443 to the VPC's own range, and a NAT route leads outside it. The
  internal-edge preflight lists which endpoints the VPC already has.
- Whatever the caller group does not let out needs a second group of the
  service's own, added to the list in `caller_vpc.yml`: a NAT route to the
  internet, a database port, the S3 gateway endpoint.
- The image pull and the function's logs are the Lambda service's and need
  neither.

Arms 15 to 20 of `scripts/check_service_iac.sh` hold both directions: a
`terraform test` against a mock provider (unset leaves the tasks exactly their
own group, set adds the caller group, an id of 8 or 17 hex digits is accepted
and a name, a list or any other length is refused), the resolved serverless
config with `custom.callerVpc` forced each way in a copy and read function by
function, and `estate` with its two variables declared.

## What a new service must change

Run `scripts/new_service.sh foundry-<new-name> [destination]`. Without a
destination it emits to `<monorepo>/services/foundry-<new-name>` when run from
the monorepo, and to a sibling directory of this template when run from a standalone
copy; it refuses a destination inside the monorepo's
`packages/`. It copies the tracked template files, renames the Python module
(`foundry_service_template` -> `foundry_new_name`), rewrites every reference,
and vendors `foundry_contracts` -- from the monorepo's sibling package when one
exists, otherwise from this repository's own `vendor/`. Then, in the new service:

1. **`src/<pkg>/domain/`** -- replace `model/widget.py` and
   `events/widget_events.py` with the real domain. Keep the shape: one entity
   per file, invariants in `__post_init__`, an event per fact worth publishing.
2. **`src/<pkg>/application/`** -- replace `create_widget.py` / `get_widget.py`
   / `list_widgets.py` with the real use cases. One use case per file; each
   imports `domain` and `ports` and nothing else.
3. **`ports/` and `adapters/{real,fake}/`** -- swap `WidgetRepository` for the
   new domain's persistence contract; keep one fake per port (a gate enforces
   it), and give each port a CONTRACT SUITE under `tests/contract/` run against
   the fake AND the real adapter.
4. **`transport/http/`** -- all four MVC files per resource: the controller, the
   view (presenter), the schema, and the model it presents. A controller that
   shapes a response inline fails `scripts/check_mvc_separation.py`.
5. **`ports/event_bus.py`'s event names** and **`domain/events/`** -- rename
   `widget.created` to whatever this service actually emits, and choose the
   right adapter for the delivery guarantee you need
   (in-process for cheap-to-lose cache invalidation, SQS for cross-service
   fan-out, outbox for anything that must not be lost).
6. **`composition/config.py`** -- add any new environment variable *here only*;
   every other module still may not import `os`.
7. **`runtime/`** -- usually nothing. The probes, the drain, the JSON logging
   and the migration head assertion are generic; wire real dependency probes
   into `_build_runtime()` in `composition/container.py` instead.
8. **`contracts/widget.v1.schema.json`** -- replace with the new wire
   contract(s), one file per version. Then run `python scripts/export_openapi.py`
   and commit `contracts/openapi/v1.json`: the suite fails while it differs from
   what the app serves -- see "The published API contract" below.
9. **`requirements/service.in`** -- add whatever new third-party
   dependencies the real domain needs, then regenerate
   `requirements/service.lock` (command above). The Dockerfile's
   `--require-hashes` install fails closed if this drifts from what the
   code actually imports.
10. **`deploy/iac/serverless.yml` / `deploy/iac/app/variables.tf` / `deploy/iac/resources/variables.tf`** --
    service name, `exposure` (it must equal the service's row in the service directory),
    IAM statements for whatever the new service actually calls, and whether
    it needs a database at all. This template ships one: set `DATABASE_URL` to
    the real server and `DB_PASSWORD_SECRET_ID` to the secret holding its
    password -- see "Persistence" below. A service with no database deletes
    `migrations/`, at which point `check_orm_standard.py` exits 3.
11. **Decide Lambda vs ECS** -- start on Lambda; move to ECS only for a
    measured reason.

Everything else -- the DI container's shape, the three probes, the six contract
gates, the shared `docker/` folder, the test kit and the CI pipeline's stage
order -- carries over unchanged, and `tests/shape/test_new_service_script.py`
runs all six gates against a freshly emitted service to prove it.

## The published API contract: `contracts/openapi/v1.json`

The service commits the OpenAPI document it serves, and three things keep that
file honest.

| Piece | What it does |
| --- | --- |
| `scripts/export_openapi.py` | Builds the app through `build_container()` and `create_app()` with a hermetic offline config (no socket, no database, no ambient variable) and writes the spec as sorted, indented JSON. `--check` exits 1 with a diff on drift and 2 when the app cannot be built. |
| `tests/shape/test_openapi_contract.py` | Runs `--check` in the ordinary suite, so a route change without a regenerated spec is a red test. |
| `service-kit/ci/openapi_breaking.sh` | CI's step. `install` fetches oasdiff 1.33.0 and refuses a tarball whose sha256 is not the pinned one; `compare` runs `oasdiff breaking <base spec> <this tree's spec> --fail-on ERR`. |

The versioning rule `compare` enforces: a path carries its major version
(`/v1/...`), and inside a major only additive change lands. A breaking change
ships as a new major path beside the old one. The old operation gets
`deprecated: true` and an `x-sunset` date in the spec, the service must answer
calls to it with `Deprecation` and `Sunset` response headers (the template
emits neither, because it deprecates no route), and it may be removed only after
that date. oasdiff's `api-path-removed-without-deprecation` and
`api-removed-before-sunset` checks are that rule, so no second implementation
of it lives here.

`compare` looks for the spec at `contracts/openapi/v1.json` and at
`openapi/v1.json`, the estate directory's default path, where a service kept its
spec before it adopted the kit. It resolves the base and the working tree
separately, so a change that moves the file is still compared across the move.
Both files on one side is a refusal (exit 2): one service publishes one contract.
`service-kit/gates/check_folder_structure.py` refuses a top-level `openapi/`, so a
service on this kit moves its spec; the second path is there so that the move, and
any break made with it, is judged and not skipped.

An oasdiff exit above 1 means oasdiff gave no verdict. With 1.33.0 that is 102 when
it cannot load a spec (a dangling `$ref`, a file that is not JSON) and 104 when both
specs load and it cannot compare them (two path templates that differ only in the
parameter's name). `compare` then exits 1, prints oasdiff's code and says there is
no verdict.

`compare` exits 3, a named skip, when the base has no spec at either path or CI
names no base; the workflow tolerates exactly that code. The same command runs
locally:

```bash
bash service-kit/ci/openapi_breaking.sh install /tmp/oasdiff-bin
PATH=/tmp/oasdiff-bin:$PATH bash service-kit/ci/openapi_breaking.sh compare origin/develop
```

The worked `widget` routes are unversioned (`/widgets`). A new service puts its
real routes under `/v1` before it first commits a spec other services read.

## Persistence: SQLAlchemy 2's ORM, one adapter, one schema authority

There is one repository adapter and it works on every dialect SQLAlchemy
supports. `DATABASE_URL` picks the engine, the driver is selected from its
dialect at first connect, and nothing under `src/` imports a DBAPI driver. The
template ships PostgreSQL 16 in `deploy/compose.yml` because that is what this
estate deploys.

`DATABASE_URL` is **required and has no default**. The variable it replaced,
`DB_PATH`, defaulted to `widgets.db` -- so a deployment that forgot to configure
storage started successfully and wrote to a container filesystem that is
discarded on the next task placement. A refusal that names the variable is a
remedy a task definition can act on; a working default is a data-loss bug with a
green health check.

| | |
| --- | --- |
| Records | `adapters/real/orm/{base,widget_record,outbox_record}.py` -- one declarative base, and `migrations/env.py` imports it |
| Adapter | `adapters/real/sql_widget_repository.py`, one for every dialect |
| Unit of work | `adapters/real/sql_unit_of_work.py` -- a `Session`, opened lazily, owning a declared table set |
| Schema | owned by `migrations/`; no adapter issues DDL |
| Engine | `adapters/real/engine_factory.py`, the ONE file permitted to name a driver |
| Credential | resolved lazily through a `SecretsPort` from `DB_PASSWORD_SECRET_ID`, injected on the `do_connect` event |

There is deliberately **no `DB_PASSWORD` environment variable**. `config.py`
reads the secret's NAME; `build_container(config, secrets=<adapter>)` resolves
the value, and only at connect time -- so the container graph stays free of both
I/O and secret material, and a crash dump that prints the environment prints a
secret id rather than a password. Supplying both a password inside
`DATABASE_URL` and a `DB_PASSWORD_SECRET_ID` is refused rather than ranked: two
credential authorities means the one that loses is the one somebody rotated.

### The contract suite runs on a real server

`tests/contract/test_widget_repository_port_contract.py` holds the fake and the
SQL adapter to one set of assertions -- per-tenant scoping, tie-broken ordering,
timezone-aware `created_at`, microsecond precision -- and
`tests/integration/test_outbox_transaction.py` does the same for the
transactional outbox. Both declare their arms in an `EXPECTED_ARMS` frozenset
with a positive control asserting the parameterisation ran.

The `real-postgres` arm reaches the PostgreSQL in `deploy/compose.yml` on an
ephemeral port, under a project name derived from the checkout path so two
worktrees do not fight, and migrates it with `alembic upgrade head` -- the same
command a deployment runs. A checkout without Docker gets a NAMED skip carrying
the remedy; `REQUIRE_REAL_RESOURCES=1` turns that skip into a failure.

SQLite has not disappeared; it has been demoted to what it can honestly answer.
`tests/support/engines.py` builds a server-free engine for tests whose subject
is the composition root or a handler seam rather than the database, and says in
its own docstring which questions it must not be used for.

### Migrations

`alembic.ini` + `migrations/`, with `version_table = "foundry_alembic_version"`
declared rather than defaulted: alembic's bare default is `alembic_version`,
which is the same literal another product's chain in this estate resolves to, so
two services sharing a database would share a revision pointer and each would
read the other's head as its own.

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/service_test \
  python -m alembic upgrade head
```

`downgrade()` is written and exercised: `upgrade head`, `downgrade base`,
`upgrade head` is part of this slice's proof, and the downgrade drops both
tables and both named indexes rather than leaving a half-schema behind.

`deploy/compose.yml` sets `HAS_MIGRATIONS: "yes"`, which is what copies
`alembic.ini` and `migrations/` into the image. A service with no schema sets it
to `no` and `service-kit/gates/check_orm_standard.py` exits 3 -- a named skip,
never a pass.

### What the gate holds

`check_orm_standard.py` closes the three routes by which hand-written SQL comes
back one file at a time: a DBAPI driver imported anywhere but
`engine_factory.py`, a `DeclarativeBase` subclass outside `adapters/real/orm/`
(a second base is a second half of the schema that autogenerate cannot see), and
a `sqlalchemy.text()` call with no entry in `scripts/orm_text_allowlist.json`.
That register ships empty with its drift check live in both directions: an
undeclared call is a finding, and an entry naming a file that no longer makes one
is a finding too.

## Service module contract: seven layers, one direction

The layers are DIRECTORIES, not a convention. This section is the binding contract
(v2), and this package is its reference implementation.

```
src/foundry_service_template/
  domain/            invariants, entities, domain events, domain errors
    model/           MVC "Model" -- one entity per file
    events/
    errors.py
  ports/             consumer-owned Protocols. One port per file.
  application/       use cases; sequencing only. One use case per file.
  adapters/
    real/            provider/SQL/SDK implementations (lazy public contract)
    fake/            in-memory implementations used by tests and local runs
  transport/
    http/
      controllers/   MVC "Controller" -- route + delegate, <= 12 lines/handler
      views/         MVC "View" -- presenters returning PLAIN DATA
      schemas/       wire DTOs (pydantic)
    events/          queue consumers and scheduled entrypoints
  composition/       the ONLY place concretes are constructed
    config.py        the ONLY module that reads os.environ
    wiring.py  container.py  app_factory.py  main.py
  runtime/           production lifecycle: probes, drain, logging, migrations
```

Direction, enforced fail-closed by `scripts/check_layering.py`:

```
composition -> runtime -> transport -> application -> domain
      |           |          |             |
      +-----------+----------+-------------+--> ports
      |
      +--> adapters --> ports, domain
```

**Each submodule's `__init__.py` is its PUBLIC CONTRACT.** Cross-submodule
imports go through the package root (`from foundry_service_template.domain
import Widget`), never a deep path -- a deep import bypasses the contract, so
the contract stops describing the coupling and a rename inside `domain` breaks a
module that was never meant to see that path.

`adapters/real/__init__.py` publishes its contract LAZILY, through PEP 562's
module `__getattr__`. Eager re-export would import `boto3` at package-import
time and defeat the import guard this package pays for. SQLAlchemy is a hard
dependency rather than an optional one, so the ORM modules are imported
unguarded and a rename is a red rather than a silent pass.

### The six gates (contract §9)

Exit codes: **0** green, **1** finding, **2** refusal, **3** named skip -- and a
3 is never a pass.

| Gate | What it holds |
| --- | --- |
| `scripts/check_layering.py` | §1 direction, no framework in the inner layers, one environment reader, no deep cross-submodule import |
| `scripts/check_mvc_separation.py` | §2 model/view/controller/schema present and separate; no inline response shaping |
| `scripts/check_size_limits.py` | §4 handler 12 / function 50 / composition 60 / class 200 / module 400 |
| `scripts/check_mock_budget.py` | §7 mock ratchet, may only shrink, with an inventory |
| `scripts/check_docker_shared.py` | §5 folder complete, wrappers forward `"$@"`, exactly one Dockerfile |
| `scripts/check_lifecycle.py` | §6 six behaviours declared in `runtime/` AND asserted in `tests/` |

Every gate carries a **conforming** and an **offending** fixture under
`scripts/gate_fixtures/`, compares against a **hand-written literal** count, has
a **two-sided floor**, and excludes the fixture tree from its production scan
while asserting that exclusion in both directions. Neutralising the marker in
the offending fixture turns all six red.

`tests/shape/test_layering.py` keeps the older, narrower checks alongside them,
each proved to go red on a deliberate violation:

1. no module outside `composition/config.py` names the environment;
2. `domain/` and `application/` (and `transport/`) import no adapter;
3. every port in `ports/` has a matching `adapters/fake/` implementation;
4. every route handler is under 12 lines;
5. `build_container()` opens zero network sockets;
6. (`tests/shape/test_single_dockerfile.py`) exactly one `Dockerfile` exists
   in this package, at `docker/Dockerfile`, and it installs from a hash-pinned
   lockfile -- see "Motivation" above for the incident this rules out
   structurally.

## Production lifecycle (contract §6)

`runtime/` owns six behaviours, each with its own test:

| Behaviour | Where | Proved by |
| --- | --- | --- |
| `/health/live`, `/health/ready`, `/health/startup`, distinct | `runtime/probes.py`, `runtime/health_endpoints.py` | `tests/unit/test_runtime_probes.py`, `tests/integration/test_health_ready.py` |
| SIGTERM graceful shutdown with in-flight drain | `runtime/shutdown.py` | `tests/unit/test_runtime_shutdown.py` (sends a REAL signal) |
| structured JSON logging with a correlation id | `runtime/logging.py`, `runtime/middleware.py` | `tests/unit/test_runtime_logging.py`, `tests/integration/test_runtime_middleware_http.py` |
| readiness gated on real dependency probes | `runtime/probes.py` | `tests/unit/test_runtime_probes.py` |
| explicit, single-headed migration application | `runtime/migrations.py` | `tests/unit/test_runtime_migrations.py` |
| rollback-safe, idempotent startup | `runtime/lifecycle.py` | `tests/unit/test_runtime_lifecycle.py` |

`/healthz` and `/readyz` remain as aliases, because deployed task definitions
and ALB target groups already point at them.

## Test resources, not mocks (contract §7)

`unittest.mock`, `MagicMock` and collaborator `monkeypatch` are not permitted
for anything that has a port. `tests/support/` is what you use instead:

| Kit | What it gives you |
| --- | --- |
| `service-kit/testing/foundry_testkit/db_seeder.py` | JSON fixtures in the backend's exact `{"target_class": "module:Class", "data": [...]}` block shape, written into a REAL database through the REAL repository |
| `service-kit/testing/foundry_testkit/aws_kit.py` | a live S3/SQS/Secrets Manager endpoint -- LocalStack from `service-kit/docker/compose/localstack.yml`, or `moto[server]` where no Docker daemon exists. Both are SERVICES on the wire, not patched objects |
| `service-kit/testing/foundry_testkit/cache_kit.py` | `{"backend": "redis", "entries": {...}}` loaded into a real Redis |
| `service-kit/testing/foundry_testkit/resources.py` | the one place that decides whether a real resource is reachable, and turns absence into a NAMED skip carrying the remedy |
| `service-kit/testing/foundry_testkit/real_profile.py` | the real-resource lifecycle: controller-only bring-up of the DECLARED resources, a per-worker database cloned from a migrated template, one engine per worker, `TRUNCATE` between tests |

A kit never falls back to a mock, and never skips silently. `tests/contract/`
runs each port's contract suite against the fake AND the real adapter, which is
what makes a fake substitutable rather than merely convenient.

## Verify

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements/service.lock
pip install --no-deps -e .
pip install --require-hashes -r requirements/test.lock
export PYTHONPATH=service-kit/vendor
ruff check src tests scripts service-kit
failed=0
for gate in service-kit/gates/check_*.py; do
  python "$gate" || [ "$?" -eq 3 ] || { echo "FAILED: $gate"; failed=1; }
done
[ "$failed" -eq 0 ]
pytest tests
bash scripts/check_service_iac.sh      # needs terraform or tofu on PATH
```

Exit code 3 from a gate means "skipped, with the reason printed"; CI treats it the same way.

## Deliberate scope boundaries

The worked example's DOMAIN is a demonstration -- a widget with a name -- and
is meant to be deleted. Its persistence is not: `SqlWidgetRepository`,
`SqlUnitOfWork` and the alembic tree are the shape a real service keeps, with
its own records and its own revisions.
No Kubernetes, no service mesh, no gRPC.
