# foundry-data-gateway: architecture

**What it is:** a generic, multi-tenant service that turns any business's database into a
small set of reviewed, read-only tools for agents. Permissions, guardrails and receipts
are enforced by the service, not by the model. Any product adopts it by writing a tool
pack and a policy. Alphazed and Shepherd are the first two packs.

```text
                     ┌──────────────────────── foundry-data-gateway ────────────────────────┐
 any MCP client ──JWT──▶ POST /mcp ─────────┐                                                 │
 product backend ─JWT──▶ /v1/tools, :call ──┤   McpEndpoint / SignedToolWebhook (protocols)   │
 foundry-agent ──HMAC──▶ /v1/tool-webhook ──┘          │                                      │
                     │                                 ▼                                      │
                     │    CallDataTool:  decide ─▶ bind ─▶ read ─▶ shape ─▶ receipt ─▶ publish │
                     │                    │         │       │        │        │          │    │
                     │             ToolPolicy  arguments  DataSource  returns  Receipt   data_tool.called
                     │             (default    (types,   (tenant     (declared Store     │    │
                     │              deny)       bounds)   bound,      columns)  (PG)     ▼    │
                     │                                    row cap,                UsageProjection
                     │                                    READ ONLY)              (subscriber)│
                     └────────────────────────────────────┬──────────────────────────────────┘
                                                          ▼
                     the business's own database: read-only user, column grants, (RLS on PG)
```

## Design principles, and where each one lives

| Principle | How it shows up | Files |
|---|---|---|
| **SRP** | One reason to change per unit. The policy decides, arguments validate, a source reads, the use case only sequences, views shape, protocol adapters frame, controllers route | `domain/tool_policy.py`, `domain/tool_arguments.py`, `adapters/real/sql_sources.py`, `application/call_data_tool.py`, `transport/http/views/data_tool_views.py`, `transport/http/data_tool_protocols.py`, `controllers/data_tools_controller.py` |
| **DRY** | One pack entry drives the MCP tool, the REST catalogue, the foundry-agent tool and the console. `AgentDefinition` *generates* the agent from the catalogue, so offered and enforced tools can't drift (e2e check `dry: …`). The shared row-cap wrapper and tenant binding live in `SqlSource` | `application/data_tool_catalogue.py`, `domain/tool_pack.py` (`input_schema`, `output_schema`) |
| **IoC / DI** | Use cases depend on ports (`CatalogueLoader`, `SourceRegistry`, `ReceiptStore`, `UsageStore`, `Publisher`, `Clock`). The composition root wires adapters by configuration: directory or S3 catalogue, in-process or outbox bus. Tests swap in-memory fakes, with no mocks | `ports/data_tools.py`, `composition/container.py::build_data_tools`, `build_catalogue`, `adapters/fake/data_tools.py` |
| **MVC** | Model: domain entities (`Tool`, `ToolPack`, `ToolPolicy`). View: `DataToolView` renders a tool or result per door. Controller: route, authenticate, delegate, present; every handler is 12 lines or fewer (template gate) | `domain/`, `transport/http/views/`, `transport/http/controllers/` |
| **PubSub** | Every call, allowed or refused, publishes `data_tool.called` exactly once per call id. `UsageProjection` subscribes and maintains the per-tenant usage read model. Metering, billing and alerts are added as more subscribers, never as more code in the call path. Locally the bus is in-process; deployed, the template's outbox/SQS path delivers to the same handler | `domain/events/data_tool_events.py`, `application/usage_projection.py` |
| **OOP** | `SqlSource` holds the shared read. `MySqlSource` (bound param, READ ONLY) and `PostgresRlsSource` (adds `SET LOCAL ROLE`, `app.<param>`) override one hook. `SqlSourceRegistry` picks a class by `(kind, strategy)`, so a new dialect is a new subclass (open/closed) | `adapters/real/sql_sources.py` |
| **IaC** | OpenTofu: a versioned, encrypted, private S3 catalogue with pack objects uploaded from the repo; least-privilege IAM (the task reads only the catalogue prefix; the execution role reads only two named secrets); ECS env and secrets wiring. A shape test proves every injected variable is a real config field | `deploy/iac/app/data_tools.tf`, `tests/shape/test_data_tools_iac.py` |

## Defence in depth: one leak needs five failures

1. **Identity:** the tenant comes from a verified JWT (`custom:org_id`) or from the
   conversation owner foundry-agent signs (HMAC). It never comes from the model's
   arguments, and a tool may not declare the scope param.
2. **Policy:** default deny per tenant, then toolsets.
3. **Statement:** reviewed SQL; the loader refuses one without the tenant binding.
4. **Database:** a SELECT-only user with table and column grants (`parents.email`
   unreachable), a READ ONLY transaction, and RLS on Postgres. The e2e negative
   control shows RLS stops a careless statement.
5. **Output:** only declared columns leave, over the row cap is refused (never
   truncated), and foundry-agent fences results as untrusted data before the model sees them.

## Interfaces

| Door | Auth | Tenant | Contract |
|---|---|---|---|
| `POST /mcp` | JWT, audience `data-gateway` | principal | MCP `initialize`, `tools/list` (input/output schema, annotations), `tools/call` (`structuredContent`) |
| `POST /v1/tool-webhook` | HMAC (foundry-agent record-tool contract) | signed conversation owner | `{result, artifact}`; foundry-agent returns `result` to the model, fenced |
| `GET /v1/tools`, `POST /v1/tools/{name}:call` | JWT | principal | REST catalogue and call |
| `GET /v1/agent-definition` | JWT | principal | A `foundry-agent-conversation/1` bundle plus digest, built from the granted tools |
| `GET /v1/receipts`, `GET /v1/usage` | JWT | principal | The org's own audit trail and usage |

OpenAPI: `contracts/openapi/v1.json` (exported and drift-checked by the template gate).
