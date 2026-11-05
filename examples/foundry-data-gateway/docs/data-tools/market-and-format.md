# How to define a data tool: market study and the v1 format

**Decision:** a tool is declared in a YAML **tool pack** (`foundry.dev/tool-pack/v1`):
named SQL statements with typed parameters, declared output columns, and a declared
tenant scope. Who may call what lives in a separate **policy** file
(`foundry.dev/tool-policy/v1`). This follows the layout the market converged on (MCP
Toolbox's `sources` / `tools` / `toolsets`) and maps field for field onto MCP, so one
definition serves MCP clients, foundry-agent and REST.

Researched 2026-11-05. Vendor claims are marked; most sources are vendor pages.

## 1. What the market uses to define a tool

| Approach | Who | Definition unit | Tenant / row security | Fit for us |
|---|---|---|---|---|
| **Declarative SQL tools in YAML** | [Google MCP Toolbox for Databases](https://github.com/googleapis/mcp-toolbox) (OSS) | `sources` (a connection), `tools` (one SQL `statement` with typed `parameters`), `toolsets` (groups) | None built in: the tool runs as the connection's user | **Closest shape.** Uses positional `$1` parameters and no tenant concept |
| OpenAPI → tools | FastMCP `from_openapi`, AWS AgentCore Gateway, Azure APIM, Apigee, Kong | One tool per API operation | Whatever the API enforces | Fine for APIs. FastMCP's own docs prefer curated tools to one tool per endpoint |
| Semantic layer → tools | [dbt Semantic Layer MCP](https://www.paradime.io/blog/dbt-semantic-layer-vs-snowflake-semantic-views-a-complete-technical-comparison), Cube (YAML/JS model, MCP server), Snowflake Semantic Views | Metrics and dimensions; the agent composes a query | The warehouse's grants | Strong for analytics. Needs a semantic layer most product databases don't have |
| Warehouse-managed MCP | [Snowflake-managed MCP](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp), Databricks managed MCP with Unity Catalog row filters | SQL execution, search and analyst tools | Native row and column policies (vendor) | Locked to one platform. Not where Alphazed or Shepherd data lives (MySQL) |
| Data-permission MCP gateways | hoop.dev (OSS, MIT per vendor), MintMCP, Nexla, CData, DreamFactory | A proxied connection with RBAC, masking and audit | Gateway-side RBAC or identity passthrough (vendor) | Generic access, not curated questions. Mostly commercial |
| Code decorators | Arcade, Composio, FastMCP `@tool` | A Python function | Whatever the code does | Every tool is a deploy. Hard for a non-engineer to review |
| **MCP tool shape (the target)** | MCP spec | `name`, `description`, `inputSchema`, `outputSchema`, `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`) | Out of scope for the spec; annotations are hints, not controls | Every format above ends up rendered as this |

What the evidence says:

1. **Curated beats generated.** FastMCP's docs and code-mode defaults (Stainless,
   Cloudflare) move away from "one tool per endpoint or table". A tool should be a
   *question the business has approved*, not a table.
2. **Nobody in the open-source declarative format makes tenancy first-class.** Toolbox
   runs every statement as the source user. Warehouses solve it only inside their own
   platform. This is the gap a generic multi-tenant service has to close, and the one
   Shepherd (`org_id`) and Alphazed (`parent_id`) both need.
3. **Annotations are hints.** The MCP spec's `readOnlyHint` is not a control (secondary
   sources agree). Enforcement has to sit below the model: database grants, a read-only
   transaction, and RLS.
4. **YAML is the lingua franca** (Toolbox, dbt, Cube). A data owner can review it, CI can
   validate it, and IaC can ship it.

## 2. The v1 format

```yaml
apiVersion: foundry.dev/tool-pack/v1
pack: alphazed_learning                 # one pack per product / domain
sources:
  alqosh:
    kind: mysql                         # mysql | postgres
    connection: alqosh                  # key into DATA_SOURCE_URLS; never a credential
    scope: {strategy: bound_param, param: parent_id}   # or rls (postgres)
tools:
  needs_attention:
    source: alqosh
    description: Child and subject pairs whose average best score is below a threshold.
    parameters:
      below_score: {type: integer, minimum: 0, maximum: 100, default: 60}
    statement: |
      SELECT c.nickname, sj.label_text AS subject, ROUND(AVG(cbu.top_score),1) AS avg_top_score ...
      WHERE c.parent_id = :parent_id ... HAVING AVG(cbu.top_score) < :below_score
    returns: {nickname: string, subject: string, avg_top_score: number, bytes_played: integer}
    examples:
      - question: Which of my children need help, and in which subject?
        arguments: {below_score: 60}
toolsets:
  parent: [list_children, child_progress, needs_attention, next_achievements]
```

```yaml
apiVersion: foundry.dev/tool-policy/v1
tenants:
  7f3a2c1e-...: {pack: alphazed_learning, toolsets: [parent], max_rows: 50}
  org_acme:     {pack: shepherd_operations, toolsets: [operator], max_rows: 50}
```

| Field | Becomes | Enforced by |
|---|---|---|
| `parameters` | MCP `inputSchema`; the foundry-agent tool schema | `domain/tool_arguments.py`: types, bounds, enums and unknown keys refused |
| `returns` | MCP `outputSchema`; the only columns a row may carry | `application/call_data_tool.py`: an undeclared column refuses the call |
| `annotations` | MCP `annotations` | v1 loads read-only tools only, backed by a READ ONLY transaction and a SELECT-only DB user |
| `scope` | The tenant binding | The loader refuses a statement that never binds `:<param>`; the gateway binds it from the **verified** caller; a tool may not declare it as a parameter |
| `examples` | Catalogue and console prompts, and agent tool descriptions | Docs, evals, and the deterministic e2e model |
| `toolsets` + policy | What each tenant sees and may call | `domain/tool_policy.py`: default deny |

### Load-time rules: a pack that could leak does not start

All of these are in `domain/tool_pack.py` and tested in `tests/unit/test_tool_pack.py`:

- No tenant binding in the statement is refused ("would cross tenants").
- The scope param declared as a model-supplied parameter is refused.
- A bound name that isn't declared is refused, and so is a declared parameter that's never used.
- Anything but one `SELECT`/`WITH` is refused: no `;`, no DML or DDL keywords.
- Missing `returns`, unknown return types, or `readOnlyHint: false` are refused.
- `rls` on a non-Postgres source, an unknown source or toolset member, or the same tool name in two packs is refused.

## 3. Adding a tool, end to end (about 10 minutes)

1. Add an entry to the product's `packs/<product>.tools.yaml`, with at least one example question.
2. Grant it: add the tool to a toolset already granted to tenants, or a new one, in the policy.
3. Run `pytest tests/unit/test_tool_pack.py`. The shipped-packs test loads every pack
   with the same rules production uses.
4. Ship: IaC uploads the pack as a new S3 object version (`deploy/iac/app/data_tools.tf`).
   Tasks load it at the next rollout. No code change and no image rebuild.

## 4. Kept for later slices

- **`kind: http`** sources: one curated OpenAPI operation per tool, same parameters and
  returns, with the tenant passed as a header.
- **`kind: semantic`**: a dbt or Cube metric as a tool.
- **Import** of MCP Toolbox `tools.yaml`. The shapes match except `$1` → `:name` and the
  missing `scope`, which the importer must ask for.
- Write tools (`readOnlyHint: false`), only behind human approval and effect receipts.
  The BB06 design covers that path.
