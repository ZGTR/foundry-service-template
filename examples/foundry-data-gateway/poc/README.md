# PoC: company data as governed agent tools (Alphazed-shaped, local, end to end)

`foundry-data-gateway` was emitted from this template (`scripts/new_service.sh`). It exposes
a company's database to agents as **curated, read-only tools**, with permissions and
guardrails, through two doors:

- the **foundry-agent** conversation API (signed record-tool webhook), and
- **MCP** (`POST /mcp`, JSON-RPC: `initialize`, `tools/list`, `tools/call`) for any MCP client.

```text
teacher ──JWT──▶ foundry-agent  ──stream──▶ model (stub; any OpenAI-compatible endpoint)
 (school-amal)   /v1/conversations            │ tool call: struggling_learners{below_score:60}
                    │ HMAC-signed webhook ◀───┘
                    ▼
             foundry-data-gateway ── policy: org grants + row cap (default deny)
                    │                 args checked against the tool's schema
                    │ SET LOCAL ROLE alphazed_reader; app.org_id = <signed owner org>
                    ▼
             PostgreSQL: az_subjects · az_content_bytes · az_learners · az_progress
                         row-level security by org · parent_email not grantable
                    │
                    └─▶ data_tool_receipts (one per call: allow/refuse, rule, rows, columns)
   rows ─▶ foundry-agent fences them as <untrusted_data> ─▶ model ─▶ answer + table card
```

## Run it

```bash
# once: this service's venv (repo README), and foundry-agent's (`uv sync --all-extras` there)
FOUNDRY_AGENT_DIR=/path/to/foundry-agent PG_URL=postgresql://postgres:postgres@127.0.0.1:5432 \
  poc/run_e2e.sh poc/proof/$(date +%F)
```

No Docker, no model key, no spend. It needs a local PostgreSQL 16 superuser URL, because
it creates two throwaway databases. The proof directory gets `REPORT.md`, every SSE
transcript, every request the model received, and the receipts table. Exit 0 only when every
check passes.

## What it proves (17 checks, `poc/proof/2026-11-05/REPORT.md`)

| Guardrail | Evidence |
|---|---|
| Agent answers from real rows | school-amal asks "who is struggling?" and gets 7 learner-subject pairs, all amal learners |
| Tenant isolation is enforced by the database | school-noor sees only noor learners; **negative control**: with RLS switched off, the same call returns amal learners |
| Per-org tool grants, default deny | school-noor's `struggling_learners` is refused (`tool_not_granted`); the model says why |
| The model cannot invent a tool | an invented `learner_emails` tool is refused by foundry-agent before the gateway is called; no receipt is written |
| Sensitive columns never leave | `alphazed_reader` cannot select `parent_email`; a row with an undeclared column is refused (unit test) |
| Tool output is data, not instructions | every result reaches the model inside `<untrusted_data source='tool:…'>` |
| Only the agent runtime can call the webhook | a forged HMAC gets 401, and no receipt is written |
| Audit | one receipt per call, allow and refuse, readable per org at `GET /v1/receipts` |
| Same policy over MCP | noor's `tools/list` shows only its 2 granted tools |

## The data

The data is **synthetic** (`scripts/seed_alphazed.py`): 6 subjects with Arabic titles, 24
content bytes, 2 schools with 10 learners, and progress scores. The tables mirror the
shape of Alphazed's content and learner data, but no production data is read or copied.
To point the gateway at real Alphazed data, give `alphazed_reader` SELECT on a read
replica's views and add the RLS policies. The tool SQL in `adapters/real/sql_data_access.py`
is the only part to adapt.

## Change made to foundry-agent

Before this PoC, a record tool's acknowledgement gave the model only `{"ok": true}`, so a
read tool's data could never reach it. `ToolAck` now carries an optional `result`. The
runtime fences it as untrusted data and refuses results over 16,000 characters rather than
truncating them. Webhooks without a `result` behave as before. Tests:
`tests/steps/conversation/test_record_tool_result.py` in foundry-agent.

## What it is not, yet

- **Not a real model.** `poc/stub_model.py` chooses tools by keyword. To use a real model,
  set foundry-agent's `MODEL_API_BASE_URL` and key to any OpenAI-compatible endpoint that
  streams tool calls.
- **MCP auth is local-only.** `/mcp` uses the template's `x-org-id` header auth
  (`SERVICE_ENV=local`). Before any deployment it needs an OAuth 2.1 resource server.
- **Policy is static.** It is loaded from `DATA_POLICIES_JSON` at startup, with no
  versioning. Receipts do not yet record a policy version.
- **Not deployed.** The template's Lambda and ECS paths are untouched and untested for
  this feature.
