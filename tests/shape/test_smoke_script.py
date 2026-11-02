"""`service-kit/ci/smoke.py` goes red on every wrong answer and never passes on silence.

The deploy job's smoke used to be two `echo` lines under a step named "Smoke (one
success, one expected denial)", so every run printed a green tick beside a claim
nothing tested. These arms drive the real script, as a subprocess, against a stub
HTTP server whose answers each test scripts -- so every verdict the script can
reach is reached here with a known input, including the ones a live service would
only produce on a bad day (a protected route answering 200, a readiness that never
settles, no answer at all).

Each arm hands the script its own `--spec`, because the routes are a service's
declaration (`deploy/smoke.json`) and the stub is not this service.
`tests/integration/test_smoke_against_the_real_process.py` is the other half: the
script with THIS service's declaration against THIS service's real process.

The second half of this file drives the `invoke` transport the same way, against a
stub `aws` that is the only program on PATH. No function is invoked anywhere here.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.support.serving import free_port

SMOKE = Path(__file__).resolve().parents[2] / "service-kit" / "ci" / "smoke.py"

SPEC = {
    "success": {"method": "GET", "path": "/ready", "status": 200, "expect": {"healthy": True}},
    "denial": {"method": "GET", "path": "/things", "status": 401},
}
READY = (200, {"healthy": True, "probes": []})
DENIED = (401, {"detail": "missing credentials"})

#: A route's scripted answers, consumed in order; the last one repeats.
Script = list[tuple[int, object] | tuple[int, object, dict[str, str]]]


class _StubServer:
    def __init__(self, routes: dict[str, Script]) -> None:
        self.routes = routes
        self.hits: dict[str, int] = {}
        self.requests: list[tuple[str, str, bytes]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                server.requests.append((self.command, self.path, self.rfile.read(length)))
                answers = server.routes.get(self.path) or [(404, {"detail": "Not Found"})]
                seen = server.hits.get(self.path, 0)
                server.hits[self.path] = seen + 1
                answer = answers[min(seen, len(answers) - 1)]
                status, body = answer[0], answer[1]
                headers = answer[2] if len(answer) == 3 else {}
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _answer  # noqa: N815 -- the stdlib's names

            def log_message(self, *_args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self) -> _StubServer:
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def serve() -> Iterator:
    servers: list[_StubServer] = []

    def start(routes: dict[str, Script]) -> _StubServer:
        server = _StubServer(routes).__enter__()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.__exit__()


@pytest.fixture
def spec(tmp_path: Path) -> Path:
    path = tmp_path / "smoke.json"
    path.write_text(json.dumps(SPEC))
    return path


def smoke(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SMOKE), *args, "--attempts", "3", "--interval", "0", "--timeout", "5"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_both_arms_hold_against_a_service_that_answers_correctly(serve, spec: Path) -> None:
    """The positive control every red arm below is read against."""
    stub = serve({"/ready": [READY], "/things": [DENIED]})
    result = smoke(stub.base, "--spec", str(spec))
    assert result.returncode == 0, result.stderr
    assert "PASS success" in result.stdout
    assert "PASS denial" in result.stdout
    assert "2 arm(s), 2 executed" in result.stdout
    assert stub.hits == {"/ready": 1, "/things": 1}, "each arm must hit its route once"


def test_a_protected_route_that_answers_200_without_credentials_is_red(serve, spec) -> None:
    stub = serve({"/ready": [READY], "/things": [(200, {"items": []})]})
    result = smoke(stub.base, "--spec", str(spec))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "not protected" in result.stderr


@pytest.mark.parametrize("status", [403, 404])
def test_a_denial_with_any_status_but_the_declared_one_is_red(serve, spec, status: int) -> None:
    """404 included on purpose: a renamed route must not turn the arm into a probe of nothing."""
    stub = serve({"/ready": [READY], "/things": [(status, {"detail": "x"})]})
    result = smoke(stub.base, "--spec", str(spec))
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"answered {status}" in result.stderr


def test_a_redirect_is_not_followed_to_a_green_page(serve, spec) -> None:
    """Following the 302 would land on a healthy 200 and read as a pass."""
    moved = (302, {}, {"location": "/elsewhere"})
    stub = serve({"/ready": [moved], "/elsewhere": [READY], "/things": [DENIED]})
    result = smoke(stub.base, "--spec", str(spec))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "answered 302" in result.stderr
    assert "/elsewhere" not in stub.hits


def test_a_success_status_without_the_declared_body_is_red(serve, spec) -> None:
    stub = serve({"/ready": [(200, {"healthy": False})], "/things": [DENIED]})
    result = smoke(stub.base, "--spec", str(spec))
    assert result.returncode == 1, result.stdout + result.stderr
    assert '{"healthy": true}' in result.stderr


def test_a_readiness_that_never_settles_is_red_after_every_retry(serve, spec) -> None:
    stub = serve({"/ready": [(503, {"healthy": False})], "/things": [DENIED]})
    result = smoke(stub.base, "--spec", str(spec))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "answered 503" in result.stderr
    assert stub.hits["/ready"] == 3, "every attempt must be spent before a verdict"


def test_a_cold_start_is_retried_until_it_answers(serve, spec) -> None:
    cold = (503, {"healthy": False})
    stub = serve({"/ready": [cold, cold, READY], "/things": [DENIED]})
    result = smoke(stub.base, "--spec", str(spec))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "retrying" in result.stdout
    assert stub.hits["/ready"] == 3


def test_a_post_denial_sends_the_declared_method_and_body(serve, tmp_path: Path) -> None:
    """A protected route that only accepts POST must be asked with POST, or the
    framework answers 405 before auth runs and the arm measures routing instead."""
    body = {"subject": "probe"}
    declared = {
        **SPEC,
        "denial": {"method": "POST", "path": "/things", "status": 403, "json": body},
    }
    path = tmp_path / "post.json"
    path.write_text(json.dumps(declared))
    stub = serve({"/ready": [READY], "/things": [(403, {"detail": "no"})]})
    result = smoke(stub.base, "--spec", str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("POST", "/things", json.dumps(body).encode()) in stub.requests


def test_an_unreachable_url_refuses_and_never_passes(spec) -> None:
    result = smoke(f"http://127.0.0.1:{free_port()}", "--spec", str(spec))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no answer" in result.stderr


@pytest.mark.parametrize("args", [[], [""], ["   "]])
def test_no_base_url_refuses(args: list[str]) -> None:
    result = smoke(*args)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no base URL" in result.stderr


@pytest.mark.parametrize("url", ["not-a-url", "ftp://example.com", "https://"])
def test_a_malformed_base_url_refuses(url: str) -> None:
    result = smoke(url)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "not an http(s) URL" in result.stderr


def test_a_missing_declaration_refuses_rather_than_guessing_routes(serve, tmp_path) -> None:
    stub = serve({"/ready": [READY], "/things": [DENIED]})
    result = smoke(stub.base, "--spec", str(tmp_path / "absent.json"))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no smoke declaration" in result.stderr
    assert stub.hits == {}, "nothing may be requested without a declaration"


@pytest.mark.parametrize(
    ("arm", "field", "value", "says"),
    [
        ("denial", "status", 200, "denial.status"),
        ("success", "status", 401, "success.status"),
        ("success", "path", "ready", "success.path"),
        ("denial", "method", "TRACE", "denial.method"),
        ("denial", "expect", {"detail": "x"}, "denial.expect"),
    ],
)
def test_a_malformed_declaration_refuses(serve, tmp_path, arm, field, value, says) -> None:
    declared = {**SPEC, arm: {**SPEC[arm], field: value}}
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(declared))
    stub = serve({"/ready": [READY], "/things": [DENIED]})
    result = smoke(stub.base, "--spec", str(path))
    assert result.returncode == 2, result.stdout + result.stderr
    assert says in result.stderr


# --- the invoke transport ----------------------------------------------------------------
#
# A service with private ingress has no URL a GitHub-hosted runner can reach, so its
# `deploy/smoke.json` declares `"transport": "invoke"` and the same two arms go through
# `aws lambda invoke` with the event a load balancer would hand the function. These arms
# drive the real script against a stub `aws` that is the only program on PATH: it records
# its arguments and the event it was handed, and answers what each test scripts.

FUNCTION = "foundry-thing-dev-api"
BASE = "https://thing.svc.example.com"

AWS_STUB = """#!{python}
import json, os, pathlib, sys, time
stub = pathlib.Path(os.environ["STUB_DIR"])
argv = sys.argv[1:]
with open(stub / "calls.log", "a") as log:
    log.write(json.dumps(argv) + "\\n")
payload = argv[argv.index("--payload") + 1]
assert payload.startswith("fileb://"), payload
event = json.loads(pathlib.Path(payload[len("fileb://"):]).read_text())
with open(stub / "events.log", "a") as log:
    log.write(json.dumps(event) + "\\n")
answers = json.loads((stub / "answers.json").read_text())[event["path"]]
counter = stub / ("hits" + event["path"].replace("/", "."))
seen = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(seen + 1))
answer = answers[min(seen, len(answers) - 1)]
time.sleep(answer.get("sleep", 0))
if "payload" in answer:
    pathlib.Path(argv[-1]).write_text(answer["payload"])
if "payload_hex" in answer:
    pathlib.Path(argv[-1]).write_bytes(bytes.fromhex(answer["payload_hex"]))
described = {{"StatusCode": 200, "ExecutedVersion": "$LATEST"}}
# `stdout_hex` and `stderr_hex` are bytes, written as they are: not all bytes are text.
out = answer.get("stdout", json.dumps(described)).encode()
err = answer.get("stderr", "").encode()
sys.stdout.buffer.write(bytes.fromhex(answer["stdout_hex"]) if "stdout_hex" in answer else out)
sys.stderr.buffer.write(bytes.fromhex(answer["stderr_hex"]) if "stderr_hex" in answer else err)
sys.exit(answer.get("exit", 0))
"""


def answered(status: object, body: object = None, **more: object) -> dict:
    """What the stub CLI does for one call: the function answered like a load balancer target."""
    response = {"statusCode": status, "body": json.dumps(body if body is not None else {}), **more}
    return {"payload": json.dumps(response)}


LAMBDA_READY = answered(200, {"healthy": True, "probes": []})
LAMBDA_DENIED = answered(401, {"detail": "missing credentials"})


class _StubCli:
    def __init__(
        self, tmp_path: Path, answers: dict[str, list[dict]], installed: bool = True
    ) -> None:
        self.dir = tmp_path / "stub"
        self.bin = tmp_path / "bin"
        self.dir.mkdir(parents=True)
        self.bin.mkdir()
        (self.dir / "answers.json").write_text(json.dumps(answers))
        if installed:
            aws = self.bin / "aws"
            aws.write_text(AWS_STUB.format(python=sys.executable))
            aws.chmod(0o755)

    def run(self, spec: Path, *args: str, function: str | None = FUNCTION, timeout: str = "30"):
        """`timeout` bounds one CLI call. It is generous because the stub is a Python
        process: on a loaded machine one that had not started inside the bound was
        counted as no answer, before it could record that it had been called."""
        named = ["--function-name", function] if function is not None else []
        return subprocess.run(
            [sys.executable, str(SMOKE), BASE, "--spec", str(spec), *named, *args,
             "--attempts", "3", "--interval", "0", "--timeout", timeout],
            capture_output=True,
            text=True,
            timeout=60,
            # The stub is the only program on PATH: a call to anything else fails the run.
            env={"PATH": str(self.bin), "STUB_DIR": str(self.dir)},
        )  # fmt: skip

    def _lines(self, name: str) -> list:
        log = self.dir / name
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def calls(self) -> list[list[str]]:
        return self._lines("calls.log")

    def events(self) -> list[dict]:
        return self._lines("events.log")

    def hits(self, path: str) -> int:
        return sum(event["path"] == path for event in self.events())


@pytest.fixture
def invoke_spec(tmp_path: Path) -> Path:
    path = tmp_path / "invoke.json"
    path.write_text(json.dumps({**SPEC, "transport": "invoke"}))
    return path


def test_invoke_both_arms_hold_and_each_is_one_call_with_a_load_balancers_event(
    tmp_path: Path, invoke_spec: Path
) -> None:
    """The positive control every red invoke arm below is read against."""
    cli = _StubCli(tmp_path, {"/ready": [LAMBDA_READY], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "smoke: PASS success: GET /ready -> 200 with the declared body" in result.stdout
    assert "smoke: PASS denial: GET /things -> 401 without credentials" in result.stdout
    # The last line says which transport ran and what a green one did not exercise.
    assert result.stdout.rstrip().splitlines()[-1] == (
        f"smoke: 2 arm(s), 2 judged through lambda invoke of {FUNCTION}. "
        f"NOT exercised: anything in front of the function at {BASE} -- the listener "
        "rule, the target group and its permission to invoke, the security groups, "
        "DNS and TLS"
    )
    calls = cli.calls()
    assert len(calls) == 2, "each arm must invoke the function once"
    for call in calls:
        # An argument list, the function named, the event from a file, the answer to a file.
        assert call[:5] == ["lambda", "invoke", "--function-name", FUNCTION, "--payload"]
        assert call[6:8] == ["--output", "json"]
        assert len(call) == 9
    ready, things = cli.events()
    assert ready == {
        "requestContext": {
            "elb": {
                "targetGroupArn": (
                    "arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/smoke/0000000000000000"
                )
            }
        },
        "httpMethod": "GET",
        "path": "/ready",
        "queryStringParameters": {},
        "headers": {"accept": "application/json", "host": "thing.svc.example.com"},
        "body": "",
        "isBase64Encoded": False,
    }
    # The denial arm carries no credentials: the same headers and nothing more.
    assert things == {**ready, "path": "/things"}


def test_invoke_a_protected_route_that_answers_200_without_credentials_is_red(
    tmp_path: Path, invoke_spec: Path
) -> None:
    cli = _StubCli(tmp_path, {"/ready": [LAMBDA_READY], "/things": [answered(200, {"items": []})]})
    result = cli.run(invoke_spec)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "not protected" in result.stderr


def test_invoke_a_success_route_with_the_wrong_status_is_red(
    tmp_path: Path, invoke_spec: Path
) -> None:
    cli = _StubCli(
        tmp_path, {"/ready": [answered(404, {"detail": "Not Found"})], "/things": [LAMBDA_DENIED]}
    )
    result = cli.run(invoke_spec)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "GET /ready answered 404, expected 200" in result.stderr
    assert cli.hits("/ready") == 1, "a 4xx is an answer and is not retried"


def test_invoke_a_success_status_without_the_declared_body_is_red(
    tmp_path: Path, invoke_spec: Path
) -> None:
    cli = _StubCli(
        tmp_path, {"/ready": [answered(200, {"healthy": False})], "/things": [LAMBDA_DENIED]}
    )
    result = cli.run(invoke_spec)
    assert result.returncode == 1, result.stdout + result.stderr
    assert '{"healthy": true}' in result.stderr


def test_invoke_a_base64_body_is_decoded_before_it_is_judged(
    tmp_path: Path, invoke_spec: Path
) -> None:
    encoded = base64.b64encode(json.dumps({"healthy": True}).encode()).decode()
    ready = {"payload": json.dumps({"statusCode": 200, "body": encoded, "isBase64Encoded": True})}
    cli = _StubCli(tmp_path, {"/ready": [ready], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS success: GET /ready -> 200 with the declared body" in result.stdout


def test_invoke_a_response_with_no_body_is_judged_on_its_status(
    tmp_path: Path, invoke_spec: Path
) -> None:
    """A load balancer response may carry no body at all; a denial is judged by status alone."""
    bare = {"payload": json.dumps({"statusCode": 401})}
    null = {"payload": json.dumps({"statusCode": 401, "body": None})}
    for denied in (bare, null):
        cli = _StubCli(tmp_path / str(id(denied)), {"/ready": [LAMBDA_READY], "/things": [denied]})
        result = cli.run(invoke_spec)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS denial: GET /things -> 401 without credentials" in result.stdout


def test_invoke_a_success_with_no_body_is_a_finding_about_the_body(
    tmp_path: Path, invoke_spec: Path
) -> None:
    """No body is an empty one: the declared key is missing, and the script says so."""
    bare = {"payload": json.dumps({"statusCode": 200})}
    cli = _StubCli(tmp_path, {"/ready": [bare], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'GET /ready answered 200 without {"healthy": true}: ' in result.stderr
    assert "Traceback" not in result.stderr


#: The second is base64 with two characters after it that are not: a lenient decoder
#: drops them and reads a healthy body out of an answer that is malformed.
NOT_BASE64 = ["not base64!", base64.b64encode(b'{"healthy": true}').decode() + "!!"]


@pytest.mark.parametrize("body", NOT_BASE64)
def test_invoke_a_body_that_claims_base64_and_is_not_is_a_502(
    tmp_path: Path, invoke_spec: Path, body: str
) -> None:
    ready = {"payload": json.dumps({"statusCode": 200, "body": body, "isBase64Encoded": True})}
    cli = _StubCli(tmp_path, {"/ready": [ready], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "GET /ready answered 502, expected 200: the function's body is not the base64 it says it is"
        in (result.stderr)
    )


def test_invoke_a_function_error_is_a_502_and_is_retried_like_one(
    tmp_path: Path, invoke_spec: Path
) -> None:
    """What a load balancer answers when its target fails; a cold start can fail the same way."""
    crashed = {
        "stdout": json.dumps(
            {"StatusCode": 200, "FunctionError": "Unhandled", "ExecutedVersion": "$LATEST"}
        ),
        "payload": json.dumps({"errorMessage": "Task timed out after 30.03 seconds"}),
    }
    cli = _StubCli(tmp_path, {"/ready": [crashed], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "GET /ready answered 502, expected 200: the function failed (Unhandled): "
        '{"errorMessage": "Task timed out after 30.03 seconds"}'
    ) in result.stderr
    assert cli.hits("/ready") == 3, "every attempt must be spent before a verdict"


def test_invoke_a_cold_start_is_retried_until_the_function_answers(
    tmp_path: Path, invoke_spec: Path
) -> None:
    crashed = {
        "stdout": json.dumps({"StatusCode": 200, "FunctionError": "Unhandled"}),
        "payload": json.dumps({"errorMessage": "init failed"}),
    }
    ready = [crashed, answered(503, {"healthy": False}), LAMBDA_READY]
    cli = _StubCli(tmp_path, {"/ready": ready, "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        f"smoke: lambda invoke {FUNCTION} GET /ready attempt 1/3 -> 502; retrying in 0.0s"
        in result.stdout
    )
    assert (
        f"smoke: lambda invoke {FUNCTION} GET /ready attempt 2/3 -> 503; retrying in 0.0s"
        in result.stdout
    )
    assert cli.hits("/ready") == 3


@pytest.mark.parametrize(
    "payload",
    [
        '"hello"',  # JSON, not an object
        "not json at all",
        json.dumps({"message": "ok"}),  # an object with no statusCode
        json.dumps({"statusCode": "200", "body": "{}"}),  # the status as a string
        json.dumps({"statusCode": True, "body": "{}"}),
        json.dumps({"statusCode": 99, "body": "{}"}),  # not an HTTP status
        json.dumps({"statusCode": 600, "body": "{}"}),
        json.dumps({"statusCode": 200, "body": {"healthy": True}}),  # the body not a string
    ],
)
def test_invoke_an_answer_that_is_not_a_load_balancer_response_is_a_502(
    tmp_path: Path, invoke_spec: Path, payload: str
) -> None:
    cli = _StubCli(tmp_path, {"/ready": [{"payload": payload}], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "GET /ready answered 502, expected 200: "
        "the function's answer is not a load balancer response: " + payload
    ) in result.stderr


def test_invoke_a_cli_that_exits_non_zero_is_no_answer_and_never_a_pass(
    tmp_path: Path, invoke_spec: Path
) -> None:
    denied = {
        "exit": 254,
        "stderr": "An error occurred (AccessDeniedException) when calling the Invoke operation\n",
    }
    cli = _StubCli(tmp_path, {"/ready": [denied], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 2, result.stdout + result.stderr
    assert (
        "smoke: REFUSED success: no answer from GET /ready: aws lambda invoke exited 254: "
        "An error occurred (AccessDeniedException) when calling the Invoke operation"
    ) in result.stderr
    assert cli.hits("/ready") == 3, "no answer is retried like a connection failure"
    assert "PASS success" not in result.stdout


def test_invoke_without_the_cli_on_path_is_no_answer(tmp_path: Path, invoke_spec: Path) -> None:
    cli = _StubCli(tmp_path, {}, installed=False)
    result = cli.run(invoke_spec)
    assert result.returncode == 2, result.stdout + result.stderr
    assert (
        "no answer from GET /ready: aws lambda invoke did not complete: FileNotFoundError"
        in result.stderr
    )
    assert "PASS" not in result.stdout


def test_invoke_a_cli_that_does_not_come_back_is_no_answer(
    tmp_path: Path, invoke_spec: Path
) -> None:
    cli = _StubCli(tmp_path, {"/ready": [{**LAMBDA_READY, "sleep": 5}], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec, timeout="0.5")
    assert result.returncode == 2, result.stdout + result.stderr
    assert (
        "no answer from GET /ready: aws lambda invoke did not complete: TimeoutExpired"
        in result.stderr
    )


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (
            {"stdout": "not json", "payload": json.dumps({"statusCode": 200, "body": "{}"})},
            "JSONDecodeError",
        ),
        (
            {"stdout": '"a string"', "payload": json.dumps({"statusCode": 200, "body": "{}"})},
            "not an object",
        ),
        ({}, "FileNotFoundError"),  # exit 0, and no answer file was written
    ],
)
def test_invoke_a_cli_answer_that_cannot_be_read_is_no_answer(
    tmp_path: Path, invoke_spec: Path, answer: dict, why: str
) -> None:
    """Exit 0 from the CLI is not the function's answer. With nothing to read, nothing answered."""
    cli = _StubCli(tmp_path, {"/ready": [answer], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no answer from GET /ready: aws lambda invoke gave no readable answer" in result.stderr
    assert why in result.stderr


#: Two bytes that are not UTF-8.
NOT_TEXT = "fffe"


def test_invoke_cli_output_that_is_not_text_is_no_answer_and_is_retried(
    tmp_path: Path, invoke_spec: Path
) -> None:
    """Bytes on the CLI's stdout that do not decode are an answer that cannot be read,
    with the same exit code and the same retries as any other."""
    unreadable = {
        "stdout_hex": NOT_TEXT,
        "payload": json.dumps({"statusCode": 200, "body": "{}"}),
    }
    cli = _StubCli(tmp_path, {"/ready": [unreadable], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no answer from GET /ready: aws lambda invoke gave no readable answer" in result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert cli.hits("/ready") == 3, "no answer is retried like a connection failure"
    assert result.stdout.rstrip().splitlines()[-1].startswith("smoke: 2 arm(s), 2 judged")


def test_invoke_a_failed_cli_whose_stderr_is_not_text_is_still_no_answer(
    tmp_path: Path, invoke_spec: Path
) -> None:
    failed = {"exit": 254, "stderr_hex": NOT_TEXT}
    cli = _StubCli(tmp_path, {"/ready": [failed], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no answer from GET /ready: aws lambda invoke exited 254" in result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert cli.hits("/ready") == 3


def test_invoke_stderr_that_is_not_text_beside_a_good_answer_is_not_a_failure(
    tmp_path: Path, invoke_spec: Path
) -> None:
    """The control for the two above: the bytes alone do not turn a run red."""
    noisy = {**LAMBDA_READY, "stderr_hex": NOT_TEXT}
    cli = _StubCli(tmp_path, {"/ready": [noisy], "/things": [LAMBDA_DENIED]})
    result = cli.run(invoke_spec)
    assert result.returncode == 0, result.stdout + result.stderr


def test_invoke_an_answer_file_that_is_not_text_is_no_answer(
    tmp_path: Path, invoke_spec: Path
) -> None:
    """The answer file is read strictly. One byte that is not UTF-8, inside a header the
    judge never looks at, is an answer that cannot be read; read with replacement, the
    same file would be the 200 the arm expects."""
    readable = json.loads(LAMBDA_READY["payload"])
    readable["headers"] = {"x-note": "marker"}
    text = json.dumps(readable).encode()
    damaged = text.replace(b"marker", b"mar\xffker")
    assert damaged != text
    # The control: the same file without the byte is a pass.
    control = _StubCli(
        tmp_path / "control", {"/ready": [{"payload_hex": text.hex()}], "/things": [LAMBDA_DENIED]}
    )
    assert control.run(invoke_spec).returncode == 0
    cli = _StubCli(
        tmp_path / "damaged",
        {"/ready": [{"payload_hex": damaged.hex()}], "/things": [LAMBDA_DENIED]},
    )
    result = cli.run(invoke_spec)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no answer from GET /ready: aws lambda invoke gave no readable answer" in result.stderr
    assert "UnicodeDecodeError" in result.stderr
    assert "PASS success" not in result.stdout
    assert cli.hits("/ready") == 3, "no answer is retried like a connection failure"


def test_invoke_with_no_cli_does_not_say_an_arm_was_invoked(
    tmp_path: Path, invoke_spec: Path
) -> None:
    """Both arms are judged, as no answer; nothing was executed through the CLI."""
    cli = _StubCli(tmp_path, {}, installed=False)
    result = cli.run(invoke_spec)
    assert result.returncode == 2, result.stdout + result.stderr
    last = result.stdout.rstrip().splitlines()[-1]
    assert last.startswith(f"smoke: 2 arm(s), 2 judged through lambda invoke of {FUNCTION}.")
    assert "executed" not in last, last


def test_invoke_a_post_denial_carries_the_declared_method_body_and_content_type(
    tmp_path: Path,
) -> None:
    body = {"subject": "probe"}
    declared = {
        **SPEC,
        "transport": "invoke",
        "denial": {"method": "POST", "path": "/things", "status": 403, "json": body},
    }
    path = tmp_path / "post.json"
    path.write_text(json.dumps(declared))
    cli = _StubCli(
        tmp_path, {"/ready": [LAMBDA_READY], "/things": [answered(403, {"detail": "no"})]}
    )
    result = cli.run(path)
    assert result.returncode == 0, result.stdout + result.stderr
    event = cli.events()[1]
    assert (event["httpMethod"], event["path"], event["body"]) == (
        "POST",
        "/things",
        json.dumps(body),
    )
    assert event["headers"] == {
        "accept": "application/json",
        "host": "thing.svc.example.com",
        "content-type": "application/json",
    }
    assert event["isBase64Encoded"] is False


def test_invoke_a_query_string_goes_in_its_own_field_as_it_was_written(tmp_path: Path) -> None:
    declared = {**SPEC, "transport": "invoke"}
    declared["success"] = {**SPEC["success"], "path": "/ready?deep=1&name=a%20b&flag"}
    path = tmp_path / "query.json"
    path.write_text(json.dumps(declared))
    cli = _StubCli(tmp_path, {"/ready": [LAMBDA_READY], "/things": [LAMBDA_DENIED]})
    result = cli.run(path)
    assert result.returncode == 0, result.stdout + result.stderr
    event = cli.events()[0]
    assert event["path"] == "/ready"
    # A load balancer hands the function each value still URL-encoded.
    assert event["queryStringParameters"] == {"deep": "1", "name": "a%20b", "flag": ""}


def test_invoke_without_a_function_name_refuses_before_any_call(
    tmp_path: Path, invoke_spec: Path
) -> None:
    cli = _StubCli(tmp_path, {"/ready": [LAMBDA_READY], "/things": [LAMBDA_DENIED]})
    for function in (None, "", "  "):
        result = cli.run(invoke_spec, function=function)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "transport 'invoke' and no --function-name was given" in result.stderr
    assert cli.calls() == [], "nothing may be invoked without a function to name"


@pytest.mark.parametrize("transport", ["grpc", "", "HTTP", None, ["invoke"]])
def test_an_unknown_transport_refuses(serve, tmp_path: Path, transport: object) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({**SPEC, "transport": transport}))
    stub = serve({"/ready": [READY], "/things": [DENIED]})
    result = smoke(stub.base, "--spec", str(path))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "'transport' must be one of ['http', 'invoke']" in result.stderr
    assert stub.hits == {}, "nothing may be requested under a transport nobody declared"


def test_the_http_transport_declared_by_name_is_the_default_and_ignores_the_function_name(
    serve, tmp_path: Path
) -> None:
    """The workflow names the function for every service; one that declares http ignores it."""
    path = tmp_path / "http.json"
    path.write_text(json.dumps({**SPEC, "transport": "http"}))
    stub = serve({"/ready": [READY], "/things": [DENIED]})
    cli = _StubCli(tmp_path, {"/ready": [answered(500)], "/things": [answered(500)]})
    result = subprocess.run(
        [sys.executable, str(SMOKE), stub.base, "--spec", str(path), "--function-name", FUNCTION,
         "--attempts", "3", "--interval", "0", "--timeout", "5"],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": str(cli.bin), "STUB_DIR": str(cli.dir)},
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        result.stdout.rstrip().splitlines()[-1]
        == f"smoke: 2 arm(s), 2 executed against {stub.base}"
    )
    assert stub.hits == {"/ready": 1, "/things": 1}
    assert cli.calls() == [], "the http transport must not call the CLI"


def test_a_declaration_with_no_transport_is_asked_over_http(serve, spec: Path) -> None:
    stub = serve({"/ready": [READY], "/things": [DENIED]})
    result = smoke(stub.base, "--spec", str(spec))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        result.stdout.rstrip().splitlines()[-1]
        == f"smoke: 2 arm(s), 2 executed against {stub.base}"
    )
