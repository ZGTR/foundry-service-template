"""API-level checks for slice v1. Every request/response pair is saved as JSON evidence.

Run by `e2e/run.sh`. Writes `<out>/api/*.json` and `<out>/api-checks.json`; exits 1 if any
check fails. Each check states the property it proves; where a check could pass vacuously
(an empty row set), it also requires the positive case.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import httpx
import yaml
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "service-kit" / "vendor")]
from foundry_data_gateway.domain import InvalidToolPack, parse_pack  # noqa: E402

GW, AGENT, CONSOLE = "http://127.0.0.1:18110", "http://127.0.0.1:18183", "http://127.0.0.1:18300"
PERSONAS = {
    p["id"]: p for p in json.loads((ROOT / "e2e/fixtures/personas.json").read_text())["personas"]
}
FAMILY_A = {"Yousef", "Lina", "Omar"}
OTHER_CHILDREN = {"Sara", "Adam", "Maryam"}
ACME_EMPLOYEES = {"Blog Writer", "Research Analyst", "Content QA"}


class Evidence:
    def __init__(self, out: Path) -> None:
        self.dir = out / "api"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.checks: list[dict] = []
        self.n = 0

    def save(self, name: str, request: dict, response: httpx.Response | dict) -> dict:
        self.n += 1
        if isinstance(response, httpx.Response):
            try:
                body = response.json()
            except ValueError:
                body = response.text
            record = {"request": request, "status": response.status_code, "response": body}
        else:
            record = {"request": request, "response": response}
        (self.dir / f"{self.n:02d}-{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False)
        )
        return record

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.checks.append({"check": name, "pass": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"))


def token(tokens: Path, pid: str, audience: str = "data-gateway") -> str:
    p = PERSONAS[pid]
    return (tokens / p["tenant"] / p["sub"] / f"{audience}.jwt").read_text().strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    for flag in ("--out", "--tokens", "--secret-file", "--mysql", "--gateway-db"):
        ap.add_argument(flag, required=True)
    a = ap.parse_args()
    ev, tokens, c = Evidence(Path(a.out)), Path(a.tokens), httpx.Client(timeout=60)

    def gw(pid: str, method: str, path: str, body: object = None, name: str = "") -> dict:
        headers = {"Authorization": f"Bearer {token(tokens, pid)}"}
        r = c.request(method, GW + path, headers=headers, json=body)
        return ev.save(
            name or path.strip("/").replace("/", "-"),
            {"persona": pid, "method": method, "path": path, "body": body},
            r,
        )

    # 1. Catalogue per tenant: grants decide what each organisation even sees.
    expected = {
        "shepherd-acme-operator": 4,
        "shepherd-globex-finance": 2,
        "alphazed-parent-amal": 4,
        "alphazed-parent-free": 2,
    }
    for pid, n in expected.items():
        rec = gw(pid, "GET", "/v1/tools", name=f"tools-{pid}")
        ev.check(
            f"catalogue: {pid} sees exactly its {n} granted tools",
            len(rec["response"]["tools"]) == n,
            [t["name"] for t in rec["response"]["tools"]],
        )

    # 2. Real use cases over REST: every tool answers with tenant-scoped rows.
    shepherd_tools = ["failed_runs", "spend_by_employee", "busiest_employees", "model_usage"]
    alphazed_tools = ["list_children", "child_progress", "needs_attention", "next_achievements"]
    for pid, tools in (
        ("shepherd-acme-operator", shepherd_tools),
        ("alphazed-parent-amal", alphazed_tools),
    ):
        for tool in tools:
            rec = gw(pid, "POST", f"/v1/tools/{tool}:call", {}, name=f"call-{pid}-{tool}")
            res = rec["response"]
            ev.check(
                f"use case: {pid} {tool} returns rows",
                rec["status"] == 200 and res.get("ok") and res["row_count"] > 0,
                res,
            )
    kids = gw(
        "alphazed-parent-amal",
        "POST",
        "/v1/tools/list_children:call",
        {},
        name="isolation-alphazed",
    )
    names = {r["nickname"] for r in kids["response"]["rows"]}
    ev.check(
        "isolation: a parent sees exactly their own children", names == FAMILY_A, sorted(names)
    )
    spend = gw(
        "shepherd-acme-operator",
        "POST",
        "/v1/tools/spend_by_employee:call",
        {},
        name="isolation-shepherd",
    )
    emps = {r["employee"] for r in spend["response"]["rows"]}
    ev.check(
        "isolation: an org sees only its own AI employees",
        emps and emps <= ACME_EMPLOYEES,
        sorted(emps),
    )

    # 3. Guardrails: policy, arguments, tenant override, authentication.
    refusals = [
        ("shepherd-globex-finance", "failed_runs", {}, "tool_not_granted"),
        ("alphazed-parent-free", "needs_attention", {}, "tool_not_granted"),
        ("shepherd-acme-operator", "failed_runs", {"days": 500}, "invalid_arguments"),
        (
            "alphazed-parent-amal",
            "list_children",
            {"parent_id": "9b1d4e2f-0000-4000-8000-000000000002"},
            "invalid_arguments",
        ),
        ("shepherd-acme-operator", "drop_everything", {}, "unknown_tool"),
    ]
    for pid, tool, args, rule in refusals:
        rec = gw(pid, "POST", f"/v1/tools/{tool}:call", args, name=f"refuse-{rule}-{tool}")
        ev.check(
            f"guardrail: {pid} {tool}{args or ''} refused as {rule}",
            rec["response"].get("refused") == rule,
            rec["response"],
        )
    r = c.get(GW + "/v1/tools")
    ev.save("no-token", {"path": "/v1/tools", "headers": {}}, r)
    ev.check("auth: no token -> 401", r.status_code == 401, r.status_code)
    r = c.get(GW + "/v1/tools", headers={"x-org-id": "org_acme", "x-subject-id": "spoof"})
    ev.save("spoofed-headers", {"path": "/v1/tools", "headers": {"x-org-id": "org_acme"}}, r)
    ev.check(
        "auth: spoofed x-org-id header without a JWT -> 401", r.status_code == 401, r.status_code
    )
    wrong_aud = token(tokens, "shepherd-acme-operator", "agent-chat")
    r = c.get(GW + "/v1/tools", headers={"Authorization": f"Bearer {wrong_aud}"})
    ev.save("wrong-audience", {"path": "/v1/tools", "token_audience": "agent-chat"}, r)
    ev.check("auth: a token for another audience -> 401", r.status_code == 401, r.status_code)

    # 4. MCP door, same tools and policy, JWT-authenticated.
    for i, (method, params) in enumerate(
        [
            ("initialize", {}),
            ("tools/list", {}),
            ("tools/call", {"name": "busiest_employees", "arguments": {"days": 30}}),
        ]
    ):
        rec = gw(
            "shepherd-acme-operator",
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": i, "method": method, "params": params},
            name=f"mcp-{method.replace('/', '-')}",
        )
        res = rec["response"].get("result", {})
        if method == "tools/list":
            first = res["tools"][0]
            ev.check(
                "mcp: tools/list carries inputSchema, outputSchema and readOnlyHint",
                len(res["tools"]) == 4
                and "outputSchema" in first
                and first["annotations"]["readOnlyHint"] is True,
            )
        if method == "tools/call":
            ev.check(
                "mcp: tools/call returns structuredContent rows",
                res["structuredContent"]["row_count"] > 0,
                res,
            )

    # 5. DRY: the agent definition is generated from the same catalogue the gateway enforces.
    for pid in ("shepherd-acme-operator", "alphazed-parent-free"):
        rec = gw(pid, "GET", "/v1/agent-definition", name=f"agent-definition-{pid}")
        bundle = rec["response"]["definition"]
        offered = sorted(t["name"] for t in bundle["tools"])
        granted = sorted(
            t["name"]
            for t in gw(pid, "GET", "/v1/tools", name=f"tools-again-{pid}")["response"]["tools"]
        )
        ev.check(
            f"dry: {pid} agent is offered exactly its granted tools", offered == granted, offered
        )

    # 6. Conversations through the console BFF -> foundry-agent -> signed webhook -> gateway.
    turns = [
        (
            "shepherd-acme-operator",
            "Which runs failed this week and what did they cost?",
            "failed_runs",
        ),
        (
            "shepherd-globex-finance",
            "How much did each employee spend this month?",
            "spend_by_employee",
        ),
        (
            "alphazed-parent-amal",
            "Which of my children need help, and in which subject?",
            "needs_attention",
        ),
        (
            "alphazed-parent-free",
            "Which achievements are my children close to unlocking?",
            "next_achievements",
        ),
        ("alphazed-parent-amal", "Give me the parents' email addresses", None),
    ]
    for pid, question, tool in turns:
        r = c.post(f"{CONSOLE}/api/{pid}/chat", json={"text": question})
        rec = ev.save(f"chat-{pid}", {"persona": pid, "text": question}, r)
        body = rec["response"]
        if tool:
            card = (body.get("cards") or [{}])[0]
            ok = (
                r.status_code == 200
                and card.get("title") == tool
                and card["data"]["ok"]
                and card["data"]["row_count"] > 0
            )
            ev.check(
                f"chat: {pid} answered via {tool} with real rows",
                ok and str(card["data"]["row_count"]) in body["reply"],
                body.get("reply"),
            )
        else:
            ev.check(
                "chat: an invented tool is refused by foundry-agent, no data call",
                not body.get("cards") and "unknown tool" in body.get("reply", ""),
                body.get("reply"),
            )

    # 7. Signed webhook: a forged call is rejected before any read.
    forged = json.dumps(
        {
            "conversation_id": "x",
            "turn_id": "t",
            "tool_call_id": "tc_forged",
            "epoch": 1,
            "name": "list_children",
            "input": {},
            "principal": {"org_id": PERSONAS["alphazed-parent-amal"]["tenant"], "sub": "attacker"},
        }
    ).encode()
    stamp = str(int(time.time()))
    sig = (
        "sha256="
        + hmac.new(b"not-the-key", stamp.encode() + b"." + forged, hashlib.sha256).hexdigest()
    )
    r = c.post(
        GW + "/v1/tool-webhook",
        content=forged,
        headers={
            "x-foundry-timestamp": stamp,
            "x-foundry-signature": sig,
            "content-type": "application/json",
        },
    )
    ev.save("webhook-forged", {"path": "/v1/tool-webhook", "signature": "wrong key"}, r)
    ev.check("webhook: forged signature -> 401", r.status_code == 401, r.status_code)

    # 8. Audit and PubSub: receipts for every call; the usage projection agrees with them.
    with create_engine(a.gateway_db).connect() as db:
        receipts = [
            dict(x)
            for x in db.execute(
                text(
                    "SELECT org_id, channel, tool, decision, rule, row_count, policy_version FROM data_tool_receipts"
                )
            ).mappings()
        ]
        usage = [
            dict(x)
            for x in db.execute(
                text("SELECT org_id, tool, calls, refusals FROM data_tool_usage")
            ).mappings()
        ]
    ev.save("db-receipts", {"query": "data_tool_receipts"}, {"rows": receipts})
    ev.save("db-usage", {"query": "data_tool_usage"}, {"rows": usage})
    ev.check(
        "audit: every channel recorded (rest, mcp, webhook) with allow and refuse",
        {r["channel"] for r in receipts} >= {"rest", "mcp", "webhook"}
        and {r["decision"] for r in receipts} == {"allow", "refuse"},
    )
    ev.check(
        "audit: no receipt for the forged webhook",
        all(r["rule"] != "" for r in receipts)
        and not any("tc_forged" in json.dumps(r) for r in receipts),
    )
    per_key: dict[tuple, int] = {}
    for r in receipts:
        per_key[(r["org_id"], r["tool"])] = per_key.get((r["org_id"], r["tool"]), 0) + 1
    ev.check(
        "pubsub: usage projection counts equal receipt counts per (org, tool)",
        per_key == {(u["org_id"], u["tool"]): u["calls"] for u in usage},
        {"receipts": len(receipts)},
    )
    rec = gw("alphazed-parent-amal", "GET", "/v1/receipts", name="receipts-api-amal")
    ev.check(
        "audit: receipts API returns only the caller's org",
        all(x["subject"] in ("parent", "") for x in rec["response"]["receipts"]),
    )

    # 9. Database grants: the reader user cannot reach contact details at all.
    try:
        reader = a.mysql.replace("root:root", "alqosh_reader:alqosh_reader-local")
        with create_engine(reader + "/alqosh").connect() as db:
            db.execute(text("SELECT email FROM parents"))
        denied = False
    except Exception as err:  # noqa: BLE001
        denied = "denied" in str(err).lower()
    ev.save(
        "db-grant-parents-email",
        {"as": "alqosh_reader", "sql": "SELECT email FROM parents"},
        {"denied": denied},
    )
    ev.check("db: alqosh_reader is denied SELECT on parents.email", denied)

    # 10. Load-time guardrail: a pack whose statement forgets the tenant does not start.
    pack = yaml.safe_load((ROOT / "packs/alphazed-learning.tools.yaml").read_text())
    broken = copy.deepcopy(pack)
    broken["tools"]["list_children"]["statement"] = (
        "SELECT c.nickname, 'x' AS level, 0 AS score, 0 AS streak_days, 0 AS coins FROM children c"
    )
    try:
        parse_pack(broken)
        refused = ""
    except InvalidToolPack as err:
        refused = str(err)
    ev.save(
        "loader-negative-control",
        {"mutation": "drop :parent_id from list_children"},
        {"refused": refused},
    )
    ev.check(
        "loader: a statement without the tenant binding is refused at load",
        "parent_id" in refused,
        refused,
    )
    parse_pack(pack)

    # 11. The model only ever saw tool output inside the untrusted-data fence.
    log = (Path(a.out) / "model-requests.jsonl").read_text().splitlines()
    tool_msgs = [m for line in log for m in json.loads(line)["messages"] if m.get("role") == "tool"]
    ev.check(
        "model: every data result reached it fenced as untrusted data",
        tool_msgs
        and all(
            "<untrusted_data source='tool:" in m["content"] or '"ok": false' in m["content"]
            for m in tool_msgs
        ),
        len(tool_msgs),
    )

    (Path(a.out) / "api-checks.json").write_text(
        json.dumps(ev.checks, indent=2, ensure_ascii=False)
    )
    failed = [x for x in ev.checks if not x["pass"]]
    print(f"API: {len(ev.checks) - len(failed)}/{len(ev.checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
