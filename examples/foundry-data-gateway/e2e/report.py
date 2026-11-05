"""Write <evidence>/REPORT.md from api-checks.json, ui-checks.json and the saved JSON."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> None:
    out = Path(sys.argv[1])
    api = (
        json.loads((out / "api-checks.json").read_text())
        if (out / "api-checks.json").exists()
        else []
    )
    ui = (
        json.loads((out / "ui-checks.json").read_text())
        if (out / "ui-checks.json").exists()
        else []
    )
    lines = [
        "# foundry-data-gateway slice v1: end-to-end evidence",
        "",
        f"Run {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}. Local: MySQL 8.4 (Shepherd and Alphazed schemas,",
        "synthetic rows), PostgreSQL 16 (gateway + foundry-agent), local workload IdP (ES256 JWTs),",
        "deterministic model (no provider, no spend), Chromium.",
        "",
        f"**API {sum(c['pass'] for c in api)}/{len(api)} · UI {sum(c['pass'] for c in ui)}/{len(ui)}**",
        "",
        "## API checks",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    lines += [f"| {c['check']} | {'PASS' if c['pass'] else 'FAIL'} |" for c in api]
    lines += ["", "## UI checks", "", "| Check | Result |", "|---|---|"]
    lines += [f"| {c['check']} | {'PASS' if c['pass'] else 'FAIL'} |" for c in ui]
    lines += [
        "",
        "## Conversations (via console BFF → foundry-agent → signed webhook → gateway)",
        "",
    ]
    for f in sorted((out / "api").glob("*-chat-*.json")):
        rec = json.loads(f.read_text())
        lines += [
            f"**{rec['request']['persona']}** asks: _{rec['request']['text']}_",
            "",
            f"> {rec['response'].get('reply', rec['response'])}",
            "",
        ]
    lines += ["## Screenshots", ""] + [
        f"![{p.stem}](ui/{p.name})" for p in sorted((out / "ui").glob("*.png"))
    ]
    lines += [
        "",
        "Every request/response is in `api/`; receipts and usage rows in `api/*-db-*.json`.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
