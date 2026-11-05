#!/usr/bin/env bash
# Slice v1, end to end on one machine: two real company schemas (Shepherd, Alphazed) in
# MySQL, the gateway, foundry-agent conversations, a deterministic model, the console UI.
#
#   FOUNDRY_AGENT_DIR=/path/to/foundry-agent e2e/run.sh [evidence-dir]
#
# Needs: Docker (MySQL 8.4), a local PostgreSQL 16 superuser URL (PG_URL), this service's
# .venv, foundry-agent's .venv (`uv sync --all-extras`), Node with Playwright + Chromium.
# No model key, no spend, no cloud. Exit 0 only when the API and UI checks all pass.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
FA=$(cd "${FOUNDRY_AGENT_DIR:?set FOUNDRY_AGENT_DIR to a foundry-agent checkout}" && pwd)
PG=${PG_URL:-postgresql://postgres:postgres@127.0.0.1:5432}
MYSQL_PORT=${MYSQL_PORT:-33306}
OUT=${1:-.local/evidence/$(date +%Y%m%d-%H%M%S)}
mkdir -p "$OUT" && OUT=$(cd "$OUT" && pwd)
RUN=$(mktemp -d)
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$RUN"; }
trap cleanup EXIT
wait_for() { for _ in $(seq 1 200); do curl -fs "$1" >/dev/null 2>&1 && return 0; sleep 0.1; done; echo "not up: $1"; exit 1; }
PY=$ROOT/.venv/bin/python
for port in 18110 18183 18190 18199 18300; do
  if curl -s -o /dev/null "http://127.0.0.1:$port/"; then echo "port $port is already in use; stop it first" >&2; exit 2; fi
done

# 1. The companies' own databases (MySQL, as both products run it) ----------------------
if ! docker exec fdg-mysql mysqladmin -uroot -proot ping --silent >/dev/null 2>&1; then
  docker rm -f fdg-mysql >/dev/null 2>&1 || true
  docker run -d --name fdg-mysql -e MYSQL_ROOT_PASSWORD=root -p "127.0.0.1:$MYSQL_PORT:3306" mysql:8.4 >/dev/null
  for _ in $(seq 1 90); do docker exec fdg-mysql mysqladmin -uroot -proot ping --silent >/dev/null 2>&1 && break; sleep 2; done
  sleep 3
fi
$PY e2e/fixtures/seed_company_databases.py "mysql+pymysql://root:root@127.0.0.1:$MYSQL_PORT" | tee "$OUT/seed.txt"

# 2. Identity: one local workload IdP; every persona gets a token per audience -----------
GRANTS=$($PY -c 'import json
for p in json.load(open("e2e/fixtures/personas.json"))["personas"]:
    print("--grant", p["sub"] + "@" + p["tenant"] + "=agent-chat,data-gateway")')
# shellcheck disable=SC2086
"$FA/.venv/bin/python" "$FA/scripts/local_workload_idp.py" --port 18199 --out-dir "$RUN/tokens" $GRANTS \
  --scope conversation:read --scope conversation:write >"$RUN/idp.log" 2>&1 & PIDS+=($!)
wait_for http://127.0.0.1:18199/jwks
for _ in $(seq 1 50); do [ -f "$RUN/tokens/idp.json" ] && break; sleep 0.1; done
ISSUER=$($PY -c 'import json,sys; print(json.load(open(sys.argv[1]))["issuer"])' "$RUN/tokens/idp.json")

# 3. The gateway: its own Postgres for receipts/usage, JWT auth, the two packs ----------
for db in fdg_gateway fdg_agent; do psql "$PG/postgres" -q -c "DROP DATABASE IF EXISTS $db" -c "CREATE DATABASE $db"; done
head -c 32 /dev/urandom | base64 >"$RUN/webhook-secret"
(
  export PYTHONPATH="$ROOT/service-kit/vendor" SERVICE_ENV=local PORT=18110 \
    DATABASE_URL="${PG/postgresql:/postgresql+psycopg:}/fdg_gateway" \
    AUTH_JWKS_URI=http://127.0.0.1:18199/jwks AUTH_AUDIENCE=data-gateway PRINCIPAL_ISSUER="$ISSUER" \
    TOOL_PACKS_DIR=packs TOOL_POLICY_FILE=e2e/fixtures/policy.yaml TOOL_WEBHOOK_SECRET_FILE="$RUN/webhook-secret" \
    AGENT_MODEL_ID=deterministic-model \
    DATA_SOURCE_URLS="{\"alqosh\": \"mysql+pymysql://alqosh_reader:alqosh_reader-local@127.0.0.1:$MYSQL_PORT/alqosh?charset=utf8mb4\", \"shepherd\": \"mysql+pymysql://shepherd_reader:shepherd_reader-local@127.0.0.1:$MYSQL_PORT/shepherd?charset=utf8mb4\"}"
  $PY -m alembic upgrade head
  exec $PY -m foundry_data_gateway.composition.main
) >"$OUT/gateway.log" 2>&1 & PIDS+=($!)

# 4. The model (deterministic) and foundry-agent's conversation API ----------------------
: >"$OUT/model-requests.jsonl"
"$FA/.venv/bin/python" e2e/deterministic_model.py --port 18190 --log "$OUT/model-requests.jsonl" & PIDS+=($!)
(
  cd "$FA"
  export PYTHONPATH="$FA/src:$FA" SERVICE_ENV=local PORT=18183 RECEIPT_STORE_BACKEND=postgres \
    RECEIPT_STORE_DATABASE_URL="$PG/fdg_agent" AUTH_JWKS_URI=http://127.0.0.1:18199/jwks AUTH_AUDIENCE=agent-chat \
    PRINCIPAL_ISSUER="$ISSUER" MODEL_API_BASE_URL=http://127.0.0.1:18190/v1 \
    MODEL_PRICES_JSON='{"deterministic-model":{"input_micros_per_1k_tokens":1000,"output_micros_per_1k_tokens":5000}}' \
    CONVERSATION_TOOL_WEBHOOK_URL=http://127.0.0.1:18110/v1/tool-webhook \
    CONVERSATION_TOOL_WEBHOOK_SECRET_FILE="$RUN/webhook-secret"
  .venv/bin/python -m foundry_agent_runtime.composition.migrate_receipt_store >/dev/null
  exec .venv/bin/python -m uvicorn foundry_agent_runtime.composition.main:app --host 127.0.0.1 --port 18183 --log-level warning
) >"$OUT/foundry-agent.log" 2>&1 & PIDS+=($!)

# 5. The console (BFF + page) ----------------------------------------------------------
GATEWAY_URL=http://127.0.0.1:18110 AGENT_URL=http://127.0.0.1:18183 TOKENS_DIR="$RUN/tokens" \
  PERSONAS_FILE=e2e/fixtures/personas.json \
  $PY -m uvicorn console.bff:app --host 127.0.0.1 --port 18300 --log-level warning >"$OUT/console.log" 2>&1 & PIDS+=($!)

wait_for http://127.0.0.1:18110/health/ready
wait_for http://127.0.0.1:18183/health/live
wait_for http://127.0.0.1:18300/api/personas

# 6. Verify: API (JSON evidence) then UI (screenshots), then the report ------------------
set +e
$PY e2e/verify_api.py --out "$OUT" --tokens "$RUN/tokens" --secret-file "$RUN/webhook-secret" \
  --mysql "mysql+pymysql://root:root@127.0.0.1:$MYSQL_PORT" --gateway-db "${PG/postgresql:/postgresql+psycopg:}/fdg_gateway"
API=$?
node e2e/verify_ui.mjs "$OUT"
UI=$?
$PY e2e/report.py "$OUT"
echo "api=$API ui=$UI evidence=$OUT"
[ "$API" = 0 ] && [ "$UI" = 0 ]
