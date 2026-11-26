#!/usr/bin/env bash
# Release checks: this repository's full checks. scripts/promote.sh runs this file, from the commit being
# promoted, in a fresh worktree next to the checkout, and records a green run as a receipt for that tree.
set -euo pipefail
# Mirrors .github/workflows/ci.yml jobs gate, iac and build. Steps that need a Docker daemon run last: the
# seeded mutation proof and the full suite (REQUIRE_REAL_RESOURCES=1 starts postgres, localstack and redis
# through docker compose) and the image build. The OpenAPI contract is compared against the release branch,
# the base this tree is promoted onto.
export PYTHONPATH=service-kit/vendor REQUIRE_REAL_RESOURCES=1   # the gate job's env

# gate: install exactly as CI does (Python 3.12, hash-locked). The venv is .venv, as in CI: the kit's
# folder-structure gate ignores .venv and refuses any other new top-level directory, which is also why
# oasdiff goes to a temporary directory (CI's RUNNER_TEMP).
py=$(command -v python3.12 || command -v python3)
"$py" -m venv .venv
. .venv/bin/activate
pip install -q --require-hashes -r requirements/service.lock
pip install -q --no-deps -e .
pip install -q --require-hashes -r requirements/test.lock
pip install -q ruff

ruff check src tests scripts service-kit
for gate in layering mvc_separation size_limits mock_budget docker_shared lifecycle orm_standard \
  seeded_tests test_first pydantic_boundaries folder_structure class_density untimed_waits kit_drift \
  vendor_digests; do
  echo "== gate: $gate"
  python "service-kit/gates/check_$gate.py" || [ "$?" -eq 3 ]
done

tools=$(mktemp -d)
bash service-kit/ci/openapi_breaking.sh install "$tools"
rel=$(git ls-remote --heads origin main master | sed 's#.*refs/heads/##' | head -1)
PATH="$tools:$PATH" BASE_REF="$rel" bash service-kit/ci/openapi_breaking.sh compare || [ "$?" -eq 3 ]

python service-kit/gates/check_service_gates.py

# iac
bash scripts/check_service_iac.sh

docker info >/dev/null 2>&1 || { echo "UNPROVEN: the remaining steps need a Docker daemon"; exit 3; }

python scripts/prove_seeded_tests.py

# Test (full suite, void-check the count). No -q: the count is parsed from pytest's summary lines.
log=$(mktemp)
collected=$(pytest tests --collect-only | grep -oE '[0-9]+ tests? collected' | grep -oE '^[0-9]+' || true)
if [ -z "$collected" ] || [ "$collected" -lt 1 ]; then echo "gate: collected '$collected' tests"; exit 1; fi
pytest tests --tb=short | tee "$log"
passed=$(grep -oE '[0-9]+ passed' "$log" | tail -1 | grep -oE '^[0-9]+' || true)
[ -n "$passed" ] || { echo "gate: no passing summary line -- treating as failure"; exit 1; }
echo "gate: $passed passed of $collected collected"

# build (no push)
SERVICE_IMAGE="foundry-service-template:release-check" docker compose -f deploy/compose.yml build service
