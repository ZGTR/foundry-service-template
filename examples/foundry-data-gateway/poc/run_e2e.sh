#!/usr/bin/env bash
# PoC: Alphazed-shaped data -> foundry-data-gateway -> foundry-agent conversation, all local.
#
#   FOUNDRY_AGENT_DIR=../../../foundry-agent PG_URL=postgresql://postgres:postgres@127.0.0.1:5432 \
#     poc/run_e2e.sh [proof-dir]
#
# Needs: a PostgreSQL 16 you can create databases on (PG_URL, superuser), this service's
# .venv (README), and foundry-agent's .venv (`uv sync --all-extras` there). No Docker,
# no model key: the model is poc/stub_model.py. Exit status is the driver's.
set -euo pipefail
cd "$(dirname "$0")/.."
GW=$PWD
FA=$(cd "${FOUNDRY_AGENT_DIR:?set FOUNDRY_AGENT_DIR to a foundry-agent checkout}" && pwd)
PG=${PG_URL:-postgresql://postgres:postgres@127.0.0.1:5432}
OUT=$(mkdir -p "${1:-.local/proof/$(date +%Y%m%d-%H%M%S)}" && cd "${1:-.local/proof/$(date +%Y%m%d-%H%M%S)}" && pwd)
RUN=$(mktemp -d)
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$RUN"; }
trap cleanup EXIT
wait_for() { for _ in $(seq 1 150); do curl -fs "$1" >/dev/null 2>&1 && return 0; sleep 0.1; done; echo "not up: $1"; exit 1; }

for db in data_gateway_poc agent_runtime_poc; do
  psql "$PG/postgres" -q -c "DROP DATABASE IF EXISTS $db" -c "CREATE DATABASE $db"
done
GW_DB="$PG/data_gateway_poc"; FA_DB="$PG/agent_runtime_poc"

# 1. Gateway: schema, synthetic Alphazed data, policy, webhook key.
head -c 32 /dev/urandom | base64 >"$RUN/webhook-secret"
export PYTHONPATH="$GW/service-kit/vendor"
export DATABASE_URL="${GW_DB/postgresql:/postgresql+psycopg:}" SERVICE_ENV=local
.venv/bin/python -m alembic upgrade head
.venv/bin/python scripts/seed_alphazed.py
DATA_POLICIES_JSON='{"school-amal":{"tools":["list_subjects","subject_progress","struggling_learners"],"max_rows":50},
                     "school-noor":{"tools":["list_subjects","subject_progress"],"max_rows":50}}' \
TOOL_WEBHOOK_SECRET_FILE="$RUN/webhook-secret" PORT=18110 \
  .venv/bin/python -m foundry_data_gateway.composition.main >"$OUT/gateway.log" 2>&1 & PIDS+=($!)
unset PYTHONPATH DATABASE_URL SERVICE_ENV

# 2. Identity (two teachers, two schools) and the stub model.
"$FA/.venv/bin/python" "$FA/scripts/local_workload_idp.py" --port 18199 --out-dir "$RUN/tokens" \
  --grant "teacher-amal@school-amal=agent-chat" --grant "teacher-noor@school-noor=agent-chat" \
  --scope conversation:read --scope conversation:write >"$RUN/idp.log" 2>&1 & PIDS+=($!)
"$FA/.venv/bin/python" poc/stub_model.py --port 18190 --log "$OUT/stub-model.jsonl" & PIDS+=($!)
: >"$OUT/stub-model.jsonl"
wait_for http://127.0.0.1:18199/jwks
for _ in $(seq 1 50); do [ -f "$RUN/tokens/idp.json" ] && break; sleep 0.1; done
ISSUER=$("$FA/.venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["issuer"])' "$RUN/tokens/idp.json")

# 3. foundry-agent step API (conversations), its record tools delivered to the gateway.
(
  cd "$FA"
  export PYTHONPATH="$FA/src:$FA" SERVICE_ENV=local PORT=18183 RECEIPT_STORE_BACKEND=postgres \
    RECEIPT_STORE_DATABASE_URL="$FA_DB" AUTH_JWKS_URI=http://127.0.0.1:18199/jwks AUTH_AUDIENCE=agent-chat \
    PRINCIPAL_ISSUER="$ISSUER" MODEL_API_BASE_URL=http://127.0.0.1:18190/v1 \
    MODEL_PRICES_JSON='{"poc-data-model":{"input_micros_per_1k_tokens":1000,"output_micros_per_1k_tokens":5000}}' \
    CONVERSATION_TOOL_WEBHOOK_URL=http://127.0.0.1:18110/v1/tool-webhook \
    CONVERSATION_TOOL_WEBHOOK_SECRET_FILE="$RUN/webhook-secret"
  .venv/bin/python -m foundry_agent_runtime.composition.migrate_receipt_store >/dev/null
  exec .venv/bin/python -m uvicorn foundry_agent_runtime.composition.main:app --host 127.0.0.1 --port 18183 \
    --log-level warning
) >"$OUT/runtime.log" 2>&1 & PIDS+=($!)
wait_for http://127.0.0.1:18110/health/ready
wait_for http://127.0.0.1:18183/health/live

# 4. Scenarios, checks and report.
"$FA/.venv/bin/python" poc/drive.py --out "$OUT" --runtime http://127.0.0.1:18183 \
  --gateway http://127.0.0.1:18110 --tokens "$RUN/tokens" --db "$GW_DB" \
  --model-log "$OUT/stub-model.jsonl" --secret-file "$RUN/webhook-secret"
