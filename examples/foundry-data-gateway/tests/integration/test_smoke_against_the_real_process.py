"""The post-deploy smoke, with THIS service's `deploy/smoke.json`, passes against its real process.

`tests/shape/test_smoke_script.py` proves the script's verdicts against a stub
that answers whatever each test scripts. This proves the declaration asks the real
service the right questions: that `/health/ready` really answers 200 with
`{"healthy": true, "probes": [...]}` once the schema exists, and that `/widgets`
really answers 401 to a request carrying no credentials. If a route is renamed,
the readiness body changes shape, or the auth adapter stops refusing, the smoke
the deploy job runs would go red on the next release -- this goes red first.

The process is started the way the container starts it (`python -m ...main`),
over a SQLite file carrying the schema, with `SERVICE_ENV=local` so the header
adapter is the one wired. The denial arm sends NO headers, which that adapter
refuses exactly as the JWT adapter a deployed stage wires does.

The declaration's `transport` is `invoke`, because this service's deployed URL is
private and the deploy job asks the function itself. There is no function here, so
this test asks the same two routes over a socket: it copies the declaration with the
transport taken out, and changes nothing else. It proves the routes, not the
transport; `tests/shape/test_smoke_script.py` proves the transport against a stub.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from tests.support.engines import local_database_url
from tests.support.serving import free_port, start_service, wait_until_serving

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "service-kit" / "ci" / "smoke.py"
DECLARATION = ROOT / "deploy" / "smoke.json"


def test_the_smoke_passes_both_arms_against_the_real_process(tmp_path: Path) -> None:
    declared = json.loads(DECLARATION.read_text())
    # This test asks the real process over http whatever transport the deploy uses.
    # Which transport is declared is tests/shape/test_deploy_job_is_wired.py's to hold.
    declared.pop("transport", None)
    over_http = tmp_path / "smoke.json"
    over_http.write_text(json.dumps(declared))
    port = free_port()
    process = start_service(
        port, {"SERVICE_ENV": "local", "DATABASE_URL": local_database_url("smoke-")}
    )
    try:
        wait_until_serving(port, process, time.monotonic() + 60)
        result = subprocess.run(
            [
                sys.executable,
                str(SMOKE),
                f"http://127.0.0.1:{port}",
                "--spec",
                str(over_http),
                "--attempts",
                "3",
                "--interval",
                "1",
                "--timeout",
                "10",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        process.terminate()
        process.wait(timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS success: GET /health/ready -> 200 with the declared body" in result.stdout
    assert "PASS denial: GET /widgets -> 401 without credentials" in result.stdout
