"""Post-deploy smoke: one success and one expected denial against a deployed service.

    python3 service-kit/ci/smoke.py <base_url> [--spec deploy/smoke.json]
                                    [--function-name NAME]
                                    [--attempts N] [--interval S] [--timeout S]

Kit-owned because `.github/workflows/ci.yml` is a kit placement and calls it: a
workflow that is byte-identical in every service can only call a script that is
byte-identical in every service. Stdlib only, because the deploy job that runs it
installs nothing.

THE ROUTES ARE THE SERVICE'S, THE MECHANISM IS THE KIT'S. Which route proves the
service is up, which one must refuse an anonymous caller, and with what status, are
facts about one service -- the template's `/widgets` does not exist in
memory-service, whose readiness body is not the template's either. They live in
`deploy/smoke.json`, which is service-owned exactly like the rest of `deploy/`, so a
sync never overwrites them and a service never edits this file. A missing or
malformed declaration is a REFUSAL: defaulting to the template's routes would smoke
a route the service may not have and report the 404 as the service's fault.

    {
      "success": {"method": "GET", "path": "/health/ready", "status": 200,
                  "expect": {"healthy": true}},
      "denial":  {"method": "GET", "path": "/widgets", "status": 401}
    }

`expect` (success only) is a subset of the top-level JSON body the route must carry.
`json` (either arm) is a request body, for a protected route that only accepts POST.

TWO TRANSPORTS, AND THE SERVICE DECLARES WHICH. An optional top-level `"transport"`
in the same file is `"http"` (the default) or `"invoke"`; anything else is a refusal.

    http    asks `<base_url><path>` over a socket. It exercises everything a caller
            crosses: the name, TLS, the listener rule, the function.
    invoke  asks the FUNCTION, through `aws lambda invoke`, with the event a load
            balancer would hand it. It needs `--function-name` and the `aws` CLI.

`invoke` exists because a service with private ingress has a URL that is a name in
the estate's private zone, which a GitHub-hosted runner cannot resolve: over `http`
its smoke got no answer on every deploy, and every deploy rolled back. The same two
arms run and the same two judges read them. What a green `invoke` smoke does NOT
show is anything in front of the function -- the listener rule, the target group
and its permission to invoke, the security groups, DNS and TLS -- and its last line
says so every time, so it cannot be read as proof of them.

The workflow passes `--function-name` to every service, because the workflow is
byte-identical everywhere; a service that declares `http` ignores it.

How an `invoke` answer becomes a reply the judges read:

    the function's own `statusCode` and `body`   that status and body (a body marked
                                                  `isBase64Encoded` is decoded first)
    a `FunctionError`, or an answer that is not   502 with the reason: what a load
    a load balancer response                      balancer answers for such a target
    the CLI is missing, exits non-zero, does not  no answer
    come back in `--timeout`, or cannot be read

TWO ARMS, AND THE SECOND IS THE ONE THAT EARNS THE NAME. A smoke that checks only
the positive path passes on a service whose auth adapter was never wired, so a 2xx
on the denial route is the loudest finding this script can report. Any other status
than the declared one is a finding too -- a 404 there means the route was renamed
and the arm has become a probe of nothing.

Readiness rather than liveness is what the template declares: liveness never touches
a dependency, so it stays green on a deployment that cannot reach its database,
which is exactly the deployment this step exists to roll back.

Redirects are NOT followed. A route that 302s to a page answering 200 would
otherwise read as a success on the redirect target's terms.

EXIT CODES, the same vocabulary as `service-kit/gates/gate_exit_codes.py`:

    0  both arms held
    1  finding  -- the service answered, and the answer was wrong
    2  refusal  -- no base URL, a malformed one, no usable declaration, `invoke`
                   with no function named, or no answer after every retry. A
                   connection failure is never a pass: it is a smoke that measured
                   nothing, and the workflow treats it as red.

Cold start: a transport failure or a 5xx is retried up to `--attempts` times,
`--interval` seconds apart, each request bounded by `--timeout`. A 2xx, 3xx or 4xx is
an answer and is never retried. Both transports retry the same way.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PASS = 0
FINDING = 1
REFUSAL = 2

DEFAULT_SPEC = Path(__file__).resolve().parents[2] / "deploy" / "smoke.json"
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
TRANSPORTS = ("http", "invoke")

#: What makes an event a load balancer's. The adapter in front of the app reads
#: `requestContext.elb` to choose the event's shape and never calls the ARN, so
#: it names no real target group: the account is zeros.
TARGET_GROUP_ARN = (
    "arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/smoke/0000000000000000"
)


class SpecError(ValueError):
    """`deploy/smoke.json` is missing or does not declare both arms."""


@dataclass(frozen=True)
class Probe:
    method: str
    path: str
    status: int
    expect: dict
    body: bytes | None


@dataclass(frozen=True)
class Reply:
    """What one request came back with. `status` is None when nothing answered."""

    status: int | None
    body: str
    error: str = ""


def _probe(raw: object, arm: str, statuses: range) -> Probe:
    if not isinstance(raw, dict):
        raise SpecError(f"'{arm}' must be an object")
    method = raw.get("method", "GET")
    path = raw.get("path")
    status = raw.get("status")
    expect = raw.get("expect", {})
    if method not in METHODS:
        raise SpecError(f"'{arm}.method' must be one of {sorted(METHODS)}, not {method!r}")
    if not isinstance(path, str) or not path.startswith("/"):
        raise SpecError(f"'{arm}.path' must be an absolute path, not {path!r}")
    if not isinstance(status, int) or isinstance(status, bool) or status not in statuses:
        raise SpecError(f"'{arm}.status' must be in {statuses.start}-{statuses.stop - 1}")
    if not isinstance(expect, dict):
        raise SpecError(f"'{arm}.expect' must be an object")
    body = json.dumps(raw["json"]).encode() if "json" in raw else None
    return Probe(method, path, status, expect, body)


def load_spec(path: Path) -> tuple[Probe, Probe, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SpecError(f"no smoke declaration at {path}") from exc
    except ValueError as exc:
        raise SpecError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SpecError(f"{path} must hold an object")
    success = _probe(raw.get("success"), "success", range(200, 300))
    denial = _probe(raw.get("denial"), "denial", range(400, 500))
    if denial.expect:
        raise SpecError("'denial.expect' is not supported; a denial is judged by status alone")
    transport = raw.get("transport", "http")
    if transport not in TRANSPORTS:
        raise SpecError(f"'transport' must be one of {list(TRANSPORTS)}, not {transport!r}")
    return success, denial, transport


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url: str, probe: Probe, timeout: float) -> Reply:
    headers = {"accept": "application/json"}
    if probe.body is not None:
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=probe.body, headers=headers, method=probe.method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return Reply(response.status, response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return Reply(exc.code, exc.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError) as exc:
        return Reply(None, "", f"{type(exc).__name__}: {exc}")


def alb_event(host: str, probe: Probe) -> dict:
    """The event a load balancer hands a Lambda target for this probe, with no credentials."""
    path, _, query = probe.path.partition("?")
    parameters = {}
    for pair in query.split("&"):
        if pair:
            # A load balancer hands each value over as it was written, still URL-encoded.
            key, _, value = pair.partition("=")
            parameters[key] = value
    headers = {"accept": "application/json", "host": host}
    if probe.body is not None:
        headers["content-type"] = "application/json"
    return {
        "requestContext": {"elb": {"targetGroupArn": TARGET_GROUP_ARN}},
        "httpMethod": probe.method,
        "path": path,
        "queryStringParameters": parameters,
        "headers": headers,
        "body": probe.body.decode("utf-8") if probe.body is not None else "",
        "isBase64Encoded": False,
    }


def reply_from(payload: str, function_error: object) -> Reply:
    """Read what the function returned the way a load balancer would."""
    if function_error:
        return Reply(502, f"the function failed ({function_error}): {payload}")
    try:
        answer = json.loads(payload)
    except ValueError:
        answer = None
    status = answer.get("statusCode") if isinstance(answer, dict) else None
    body = answer.get("body") if isinstance(answer, dict) else None
    if (
        not isinstance(status, int)
        or not 100 <= status <= 599
        or not (body is None or isinstance(body, str))
    ):
        return Reply(502, f"the function's answer is not a load balancer response: {payload}")
    body = body or ""
    if answer.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body, validate=True).decode("utf-8", "replace")
        except ValueError:
            return Reply(502, "the function's body is not the base64 it says it is")
    return Reply(status, body)


def invoke(function: str, host: str, probe: Probe, timeout: float) -> Reply:
    """Ask the function itself, through the CLI the deploy job already relies on."""
    with tempfile.TemporaryDirectory(prefix="smoke-") as scratch:
        event = Path(scratch) / "event.json"
        answer = Path(scratch) / "answer.json"
        event.write_text(json.dumps(alb_event(host, probe)), encoding="utf-8")
        # An argument list, never a shell string: the function's name and the path
        # are data. `fileb://` hands the event over as the bytes in the file.
        command = [
            "aws", "lambda", "invoke",
            "--function-name", function,
            "--payload", f"fileb://{event}",
            "--output", "json",
            str(answer),
        ]  # fmt: skip
        try:
            # `errors="replace"`: bytes that are not text are an answer that cannot
            # be read, which the lines below already call no answer. Without it the
            # decode raises inside this call and the script dies with exit 1.
            done = subprocess.run(
                command,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Reply(
                None, "", f"aws lambda invoke did not complete: {type(exc).__name__}: {exc}"
            )
        if done.returncode != 0:
            return Reply(
                None, "", f"aws lambda invoke exited {done.returncode}: {done.stderr.strip()[:200]}"
            )
        # Exit 0 is the CLI's, not the function's: a function that raised still exits
        # 0 here and says so in `FunctionError`. With nothing to read, nothing answered.
        try:
            described = json.loads(done.stdout)
            payload = answer.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            return Reply(
                None, "", f"aws lambda invoke gave no readable answer: {type(exc).__name__}: {exc}"
            )
        if not isinstance(described, dict):
            return Reply(
                None,
                "",
                "aws lambda invoke gave no readable answer: what it printed is not an object: "
                + done.stdout.strip()[:200],
            )
        return reply_from(payload, described.get("FunctionError"))


def fetch_settled(ask: Callable[[], Reply], target: str, attempts: int, interval: float) -> Reply:
    """Retry what a cold start produces (no answer, or a 5xx); return the last reply."""
    reply = Reply(None, "", "no attempt made")
    for attempt in range(1, attempts + 1):
        reply = ask()
        settled = reply.status is not None and reply.status < 500
        if settled or attempt == attempts:
            return reply
        seen = reply.status if reply.status is not None else reply.error
        print(f"smoke: {target} attempt {attempt}/{attempts} -> {seen}; retrying in {interval}s")
        time.sleep(interval)
    return reply


def _json_object(body: str) -> dict | None:
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def judge_success(probe: Probe, reply: Reply) -> tuple[int, str]:
    route = f"{probe.method} {probe.path}"
    if reply.status is None:
        return REFUSAL, f"no answer from {route}: {reply.error}"
    if reply.status != probe.status:
        return FINDING, (
            f"{route} answered {reply.status}, expected {probe.status}: {reply.body[:200]}"
        )
    if probe.expect:
        data = _json_object(reply.body)
        missing = {
            key: value
            for key, value in probe.expect.items()
            if data is None or data.get(key) != value
        }
        if missing:
            return FINDING, (
                f"{route} answered {reply.status} without {json.dumps(missing)}: {reply.body[:200]}"
            )
    return PASS, f"{route} -> {reply.status}" + (" with the declared body" if probe.expect else "")


def judge_denial(probe: Probe, reply: Reply) -> tuple[int, str]:
    route = f"{probe.method} {probe.path}"
    if reply.status is None:
        return REFUSAL, f"no answer from {route}: {reply.error}"
    if 200 <= reply.status < 300:
        return FINDING, (
            f"{route} answered {reply.status} with NO credentials: the protected "
            "route is not protected, so auth is missing or unwired in this deployment"
        )
    if reply.status != probe.status:
        return FINDING, (
            f"{route} answered {reply.status} with no credentials, expected "
            f"{probe.status}: {reply.body[:200]}"
        )
    return PASS, f"{route} -> {probe.status} without credentials"


def validate_base_url(raw: str) -> str | None:
    parsed = urllib.parse.urlparse(raw.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return raw.strip().rstrip("/")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url", nargs="?", default="")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--function-name", default="")
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    if not args.base_url.strip():
        print("smoke: REFUSED: no base URL given -- nothing to smoke", file=sys.stderr)
        return REFUSAL
    base = validate_base_url(args.base_url)
    if base is None:
        print(f"smoke: REFUSED: {args.base_url!r} is not an http(s) URL", file=sys.stderr)
        return REFUSAL
    if args.attempts < 1:
        print("smoke: REFUSED: --attempts must be at least 1", file=sys.stderr)
        return REFUSAL
    try:
        success, denial, transport = load_spec(args.spec)
    except SpecError as exc:
        print(f"smoke: REFUSED: {exc}", file=sys.stderr)
        return REFUSAL
    function = args.function_name.strip()
    if transport == "invoke" and not function:
        print(
            f"smoke: REFUSED: {args.spec} declares transport 'invoke' and no --function-name "
            "was given -- nothing to invoke",
            file=sys.stderr,
        )
        return REFUSAL
    host = urllib.parse.urlparse(base).netloc

    arms = (("success", success, judge_success), ("denial", denial, judge_denial))
    codes = []
    for name, probe, judge in arms:
        if transport == "invoke":
            target = f"lambda invoke {function} {probe.method} {probe.path}"
            reply = fetch_settled(
                lambda probe=probe: invoke(function, host, probe, args.timeout),
                target,
                args.attempts,
                args.interval,
            )
        else:
            url = base + probe.path
            reply = fetch_settled(
                lambda url=url, probe=probe: fetch(url, probe, args.timeout),
                url,
                args.attempts,
                args.interval,
            )
        code, message = judge(probe, reply)
        codes.append(code)
        verdict = {PASS: "PASS", FINDING: "FINDING", REFUSAL: "REFUSED"}[code]
        stream = sys.stdout if code == PASS else sys.stderr
        print(f"smoke: {verdict} {name}: {message}", file=stream)

    if transport == "invoke":
        print(
            # "judged", not "executed": with no CLI on PATH both arms are judged as
            # no answer and nothing was invoked.
            f"smoke: {len(arms)} arm(s), {len(codes)} judged through lambda invoke of "
            f"{function}. NOT exercised: anything in front of the function at {base} -- "
            "the listener rule, the target group and its permission to invoke, the "
            "security groups, DNS and TLS"
        )
    else:
        print(f"smoke: {len(arms)} arm(s), {len(codes)} executed against {base}")
    if FINDING in codes:
        return FINDING
    if REFUSAL in codes:
        return REFUSAL
    return PASS


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
