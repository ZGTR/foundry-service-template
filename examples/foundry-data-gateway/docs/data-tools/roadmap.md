# Roadmap: from slice v1 to the full product

**Vision:** any business can make its data safely askable by agents in an afternoon.
It writes a tool pack, grants it to tenants, and every agent, whether its own,
foundry-agent or any MCP client, gets exactly the reviewed questions that tenant may ask.
Every answer leaves a receipt.

Each slice is end to end: front end, backend, data, identity, IaC and proof. Each one ends
with something a person can use, not a layer.

| Slice | Outcome | Exit criteria | State |
|---|---|---|---|
| **v1: governed read tools, two real products** | Shepherd operators and Alphazed parents ask questions in a console. Answers come from their own data through foundry-agent, with receipts and usage | 39 API + 24 UI e2e checks green locally against the real Shepherd and Alphazed schemas; unit, shape and integration gates green; IaC written and guarded | **Done locally** (see `slice-v1.md`) |
| v2: first production tenant | One tenant (Shepherd `org_*` on Shepherd's own DB) uses it in the Shepherd console on prod | Deployed via `data_tools.tf`; a read replica DSN in Secrets Manager; a real model through foundry-agent; prod smoke of tools, chat and receipts; rollback rehearsed | Owner gates (below) |
| v3: Alphazed in-app parent assistant | Parents ask "who needs help?" in the Amal app | Alphazed BFF calls `/v1/agent-definition` and foundry-agent; AR + EN rendering; account-state matrix (no children, multi-child, free plan) | Planned |
| v4: authoring and policy lifecycle | Data owners add tools without engineers | `kind: http` and `semantic` sources; a Toolbox `tools.yaml` importer; a policy version history; a pack CI action that runs the load rules and example questions as evals | Planned |
| v5: effects and approvals | Agents may propose writes | `readOnlyHint: false` tools only behind human approval, effect receipts and idempotency (per BB06's approval design) | Planned |
| v6: open-source release | `ZGTR/foundry-data-gateway` public | Its own repository with clean history; docs site; MCP registry entry; a hosted demo on synthetic data | Planned |

## To reach prod: what is manual (owner)

These can't be done from an agent session, by design:

1. **Repository.** Create `ZGTR/foundry-data-gateway` and move this directory there. The
   template's single-Dockerfile gate expects an emitted service to live in its own repo.
2. **Account and region.** Choose where it runs: Shepherd's account, or Alphazed's
   (`eu-west-1`).
3. **Read-only access.** Create read-only DB users with the column grants in
   `e2e/fixtures/seed_company_databases.py` (`READERS`) on a **read replica** of each
   product DB. Store `DATA_SOURCE_URLS` and the webhook key in Secrets Manager.
4. **Policy.** Write the production policy file. Which real orgs or parents get which
   toolsets is a product decision.
5. **Deploy.** Run `tofu apply` for `deploy/iac/app` with those ARNs, and deploy the image
   through the template's dispatch workflow. GitHub Actions is billing-blocked, so this
   needs the owner's local deploy path.
6. **foundry-agent.** Point its conversation install's `CONVERSATION_TOOL_WEBHOOK_URL` at
   the gateway, with the same key, and choose a real model (Bedrock).
7. **Product BFFs.** Shepherd console and Alphazed app call `/v1/agent-definition` and
   foundry-agent, as `console/bff.py` does.
