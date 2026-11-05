# Slice v1: governed read tools for two real products

**Goal:** prove the whole path once, on the long-term architecture, for both first
customers. A Shepherd operator and an Alphazed parent each sign in to a console, ask a
question in plain words, and get an answer computed from **their own** rows. Every call is
policy-checked, tenant-scoped, receipted and published as an event.

## Scope

| Layer | In v1 |
|---|---|
| Front end | `console/static/index.html`: persona switcher, granted-tool catalogue with example questions, chat with data tables, receipts and usage panels. Desktop and mobile |
| BFF | `console/bff.py`: the reference pattern a product backend copies (catalogue → agent definition → foundry-agent turn → audit) |
| Agent runtime | foundry-agent conversations (step API), with a record-tool `result` returned to the model (foundry-agent change on branch `claude/sharp-faraday-u88njo`) |
| Gateway | Packs, policy, MCP, webhook, REST, receipts, `data_tool.called` + usage projection, JWT auth |
| Data | MySQL 8.4 with Shepherd's and Alphazed's **real table and column names** and synthetic rows; SELECT-only reader users with column grants |
| Identity | Local workload IdP: ES256 JWTs per persona, audiences `data-gateway` and `agent-chat` |
| IaC | `deploy/iac/app/data_tools.tf`: S3 catalogue, IAM, ECS secrets |
| Model | Deterministic, OpenAI-compatible (`e2e/deterministic_model.py`). Swap `MODEL_API_BASE_URL` for a real one |

## Real use cases covered

| Product | Persona (tenant) | Questions | Tools |
|---|---|---|---|
| Shepherd | Acme operator (`org_acme`, toolset `operator`) | Which runs failed this week and what did they cost? Who is busiest? Spend per employee? Which models, and the cache hit rate? | `failed_runs`, `busiest_employees`, `spend_by_employee`, `model_usage` over `playbook_runs`, `node_executions`, `employees` |
| Shepherd | Globex finance (`org_globex`, `finance`) | Spend and model usage only. `failed_runs` is refused (`tool_not_granted`) | `spend_by_employee`, `model_usage` |
| Alphazed | Parent of Yousef, Lina and Omar (`parent`) | Which children need help, and in which subject? Progress per subject? Streaks? Next achievements? | `list_children`, `child_progress`, `needs_attention`, `next_achievements` over `children`, `content_byte_user`, `content_bytes`, `chapters`, `subjects`, streaks, achievements |
| Alphazed | Free-plan parent (`parent_free`) | Children and achievements only. `needs_attention` is refused | `list_children`, `next_achievements` |

Schema provenance: Shepherd from `services/shepherd-backend/src/shepherd/models/entities.py`
and `playbooks/models.py`. Alphazed from recorded prod fixtures
(`foundry-mobileapps/lib/data/__fixtures__/prod/`) and the content-schema plan.
`content_byte_user(status, top_score)` is verified; the streak and achievement tables are
inferred from fixture fields. Values like model ids, terminal reasons and per-call costs
come from Shepherd's `local-seed/runs`.

## Acceptance criteria and evidence

Run with `FOUNDRY_AGENT_DIR=… e2e/run.sh docs/evidence/slice-v1`. The evidence directory
holds every request and response as JSON (`api/`), screenshots (`ui/`), the check lists
and `REPORT.md`.

| # | Criterion | Check |
|---|---|---|
| A1 | Each tenant sees exactly its granted tools | `catalogue: …` (API) and `ui: … lists N granted tools` |
| A2 | Every real use case answers from real rows | `use case: …` (8 tools) and `chat: … answered via <tool>` |
| A3 | Tenants are isolated | A parent sees exactly their children; an org sees only its employees; the UI never shows another family's child |
| A4 | Guardrails refuse with a named rule and read nothing | `tool_not_granted`, `invalid_arguments` (bounds, tenant-override attempt), `unknown_tool`, invented tool refused by foundry-agent |
| A5 | Only verified callers | No token, spoofed `x-org-id`, wrong audience and forged webhook all get 401 |
| A6 | One definition everywhere | The agent is offered exactly the granted tools; MCP `tools/list` carries input and output schema plus annotations |
| A7 | Audit and PubSub agree | Receipts across REST, MCP and webhook, allow and refuse; usage projection counts equal receipt counts |
| A8 | Database is the last line | `alqosh_reader` is denied `parents.email`; RLS negative control in `tests/integration/test_sql_sources_real.py` |
| A9 | A leaking pack cannot load | Dropping `:parent_id` from a statement is refused at load |
| A10 | The model only sees fenced data | Every tool result in `model-requests.jsonl` is inside `<untrusted_data>` |
| A11 | Works on desktop and phone | Screenshots at 1440 and 390 px; no horizontal scroll at 390 |

## Not in v1 (stated, not hidden)

- No production deployment. `docs/data-tools/roadmap.md` lists the owner-only steps.
- No real model, so the answer wording is deterministic. The tool choice and data path are real.
- Lambda (`serverless.yml`) wiring for data tools; only the ECS path is in IaC.
  `tofu validate` was not run in this environment (OpenTofu download not permitted). HCL
  parses, and the shape test checks every injected variable has a reader.
- Rows are synthetic. No production data was read or copied.
