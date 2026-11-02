# `docker/` — the shared containerisation folder

§5 of the service module contract (README.md, "Service module contract"). **Every Foundry
service repository carries this folder, and the generic files are byte-identical
across all of them.** A value that differs between services arrives as a build
argument or an environment variable. Forking one of these scripts to change a
value is the defect this folder exists to prevent — five hand-maintained copies
that agree today are five copies that will disagree silently.

| File | What it is |
| --- | --- |
| `Dockerfile` | The service image. One image, two runtimes (ECS and Lambda). Base pinned by digest; dependencies installed with `--require-hashes` against `requirements/service.lock`; explicit per-path `COPY`; runs as numeric UID `65532`. |
| `entrypoint.sh` | The container `ENTRYPOINT`. Detects the runtime, exports `RUNTIME_MODE`, then **ends in `exec … "$@"`** so an explicit container `CMD` is honoured. |
| `healthcheck.sh` | The container `HEALTHCHECK`. Probes `/health/ready` and exits 0/1. |
| `wait-for.sh` | Blocks until a TCP endpoint accepts, then `exec "$@"`. |
| `compose/localstack.yml` | S3 / SQS / Secrets Manager for TDD. |
| `compose/postgres.yml` | The service's real database. |
| `compose/service.yml` | The service itself, built from this folder. |

## `exec … "$@"` is the whole point of two of these scripts

A wrapper that ends in a hardcoded command **discards the command it was given,
silently, at exit 0**. That is not hypothetical in this estate: a service emitted
from this template shipped a `drainOutbox` Lambda whose `image.command` was
`[python, -c, "…outbox_drain…"]`, recorded in its IaC and in a blackbox proof as
*"imports the symbol and exits"*. It did neither — the arguments landed in an
entrypoint that never named `"$@"` and were dropped, so what ran 1440 times a day
was the HTTP API server.

`scripts/check_docker_shared.py` and `tests/shape/test_entrypoint.py` both assert
the forward, and the monorepo's `scripts/check_entrypoint_forwards_cmd.py`
enforces it across every copy in the estate.

## Why there is exactly one Dockerfile

`tests/shape/test_single_dockerfile.py` asserts it. `an older backend service`
carried `Dockerfile` and `Dockerfile.lambda`; they drifted — different base image,
one installing the unpinned `requirements.txt` instead of the hash-checked lock,
one running as root — and nothing detected it, because nobody diffs two
Dockerfiles by hand on every change. One image serving both targets makes the
drift structurally impossible rather than a convention someone has to remember.

## Local use

Through the service's own compose entry point, which `include:`s these
fragments. Naming a fragment directly also works, but the entry point is the
path that stays correct when the kit moves — these commands read
`docker/compose/...` until 2026-09-18, a location the fragments left when they
became kit-owned, so every one of them was a copy-paste that could not run.

```bash
# the service alone
docker compose -f deploy/compose.yml up --build service

# integration dependencies
docker compose -f deploy/compose.yml up -d localstack
docker compose -f deploy/compose.yml up -d postgres
```

Where there is no Docker daemon, `service-kit/testing/foundry_testkit/` runs the same suites against
native servers instead — `moto[server]` for the AWS surface, a real local
PostgreSQL/Redis, a real SQLite file. Those are **services**, not mocks, so §7
holds either way; the kits report which path they took, and a NAMED skip when
neither is reachable.
