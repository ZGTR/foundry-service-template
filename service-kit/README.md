# service-kit

Owned by the service template, `` in
`the monorepo` (its own repository, now archived, until 2026-10-09). Do not edit here — run `python service-kit/sync.py`.

Everything under this folder is byte-identical in every Foundry service. That
is the whole point: a gate, a Dockerfile or a test kit that differs per service
cannot be reasoned about once, and this estate has already paid for the
alternative — 94 vendor-drift findings across ten repositories, accumulated
because nothing said who owned the copies.

## What is in here

| Path | What it is |
|---|---|
| `docker/` | The generic `Dockerfile`, `entrypoint.sh`, `healthcheck.sh`, `wait-for.sh`, and the `compose/` resource fragments |
| `gates/` | The §9 contract gates, `gate_common.py`, `gate_exit_codes.py` and their fixture tree |
| `ci/` | `verify.sh` and its `arm.sh` accumulator (the human's one command), plus what the workflow's deploy job calls: `smoke.py` (one success, one expected denial against the deployed service, over HTTP or through `aws lambda invoke` for a service with private ingress; routes and transport declared by the service in `deploy/smoke.json`), `lambda_release.sh` (record the running digest, read the stack's `ApiUrl`, roll back to the recorded digest) and `deploy_env.py` (read the variables the service declares in `deploy/deploy_env.json` from SSM or Secrets Manager before the push, in declaration order so an id may name an earlier `ssm` variable as `{NAME}`, and hand them only to the deploy and the rollback) |
| `testing/foundry_testkit/` | The seeding, AWS and cache kits, imported as `foundry_testkit` |
| `vendor/` | Vendored `platform` packages, copied from `platform/packages/*` |
| `sync.py` | Replaces this folder with the template's, at a revision |
| `MANIFEST.json` | `template_revision`, a sha256 per kit file, and the placements |

## What is NOT in here, and why

The kit holds what is the same everywhere. A service's own measurements and
declarations stay outside it, because a kit that carried them would either
overwrite a service's numbers at sync time or force every service to share one:

- `scripts/mock_budget.json`, `scripts/test_first_exemptions.yaml`,
  `scripts/prove_seeded_tests.py` — ratchets and mutation lists are per service.
- `tests/support/environment.py` and the service's fixture blocks — they know
  this service's aggregate.
- `deploy/` — which resources this service runs (`deploy/compose.yml`) and how it
  ships (`deploy/iac/`), what the post-deploy smoke asks it (`deploy/smoke.json`), and
  where each deploy-time secret or parameter lives (`deploy/deploy_env.json`). One
  folder, so a reader looking for any of them finds all.
- `migrations/` — this service's schema history.

## Placements

`.github/workflows/ci.yml` is kit-owned but cannot live in here, because GitHub
reads workflows from `.github/workflows/` and nowhere else. The manifest records
it under `placements`, `sync.py` rewrites it, and `check_kit_drift.py` hashes it
exactly as if it sat inside the folder.

## Syncing

```bash
python service-kit/sync.py --revision <monorepo-sha>          # from the monorepo
python service-kit/sync.py --from <monorepo>/.  # a local checkout
# A template sha from before 2026-10-09 is translated through the import's commit map.
python service-kit/gates/check_kit_drift.py                    # prove the kit is unforked
```

`sync.py` replaces the folder wholesale rather than merging, which is the only
way a file the template DELETED leaves a service. It refuses to run while
`service-kit/` has uncommitted changes: those are either a local experiment it
would destroy, or a hand edit that belongs in the template.

## The hole this does not close

An author who edits a kit file here and re-runs `sync.py --emit-manifest` gets a
green `check_kit_drift.py`, because both sides of the comparison then come from
the same edited tree. Nothing inside a service can close that — the missing
party is the template. The estate gate `check_service_kit_parity.py` compares
each service's manifest file map against the template's own manifest at the
declared `template_revision`, hash for hash, and that is what catches a
deliberate re-hash. This gate catches the accident.
