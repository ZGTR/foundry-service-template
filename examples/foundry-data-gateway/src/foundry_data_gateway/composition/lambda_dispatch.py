"""Mounts a non-HTTP function's handler onto the SAME app process and the
SAME image.command the HTTP function uses (the Lambda dispatch change).

THE DEFECT THIS REPLACES. R.1 gave every non-HTTP function its own
`image.command: [..., -m, awslambdaric, <module>.handler]`, a SECOND Lambda
Runtime API client living beside the Lambda Web Adapter (LWA), which the
kit's Dockerfile installs unconditionally as an extension. Both `awslambdaric`
and LWA call `GET /2018-06-01/runtime/invocation/next` against the same
`AWS_LAMBDA_RUNTIME_API`. Measured under the Runtime Interface Emulator
(RIE) v1.37: with both present, every invoke answers in under a millisecond
with `produced bytes: 0` / a `null` body, and the real handler is NEVER
dispatched to -- confirmed by the side effect it should have produced (a
migration) never landing. The 200 status this produces is not evidence of
anything; ARM4 in the R.1 proof measured exactly this and its own wording,
"answered, with no conflict", never claimed correct content -- this module is
what makes that claim earnable.

THE FIX. LWA supports `AWS_LWA_PASS_THROUGH_PATH` (measured from the pinned
binary at aws-lambda-adapter/0.8.4, `strings` output, `public.ecr.aws/awsguru/
aws-lambda-adapter@sha256:e2653f74...`): when set, LWA forwards the RAW
invocation event, unmodified, as an HTTP POST to that path on the app it is
already proxying to -- no second event-translation shape, no second Runtime
API client. So a non-HTTP function's `image.command` becomes IDENTICAL to the
HTTP function's (both run the Dockerfile's default CMD; NEITHER declares an
explicit command), and only its `environment:` differs:

    AWS_LWA_PASS_THROUGH_PATH: /_lambda/dispatch   # this module's path
    FOUNDRY_FUNCTION: migrate                      # which _main module to run

MEASURED, RIE v1.37, adapter present, this exact mechanism, isolated
synthetic app: HTTP 200, body `{"applied": true, ...}`, a written marker file
proving the dispatched code actually ran -- see the Lambda dispatch change proof.

WHY THIS IMPORTS LAZILY, ONE MODULE, NEVER ALL OF THEM. Each `<x>_main.py`
is its own tiny composition root: it builds its own adapters at IMPORT time
(`config = ServiceConfig.from_environment()`, then whatever real adapter it
needs), unconditionally, the moment the module is imported. If this file
imported every non-HTTP function's module to build a lookup table, the HTTP
function's own process -- which never sets FOUNDRY_FUNCTION -- would still
pay for building all of them, and a non-HTTP function's process would build
adapters for functions it will never run. `importlib.import_module` at
mount time, keyed on `FOUNDRY_FUNCTION`, imports exactly the one module a
given container needs and none other.

WHY THIS DOES NOT TOUCH `<x>_main.py` AT ALL. Every one of those modules
already exports `handler(event, context) -> dict` and is independently
tested and independently runnable via `python -m <pkg>.composition.<x>_main`
for local/manual use. This module is a thin transport adapter in front of
an unchanged handler -- it does not re-implement, wrap in a try/except, or
duplicate any of that behaviour.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

from fastapi import FastAPI, Request, Response

#: Fixed, not configurable per service: the whole point is that the template
#: and every function agree on it without a service having to declare it
#: twice (once in serverless.yml's AWS_LWA_PASS_THROUGH_PATH, once here).
DISPATCH_PATH = "/_lambda/dispatch"

#: The name LWA's own readiness/pass-through machinery reserves; refusing to
#: also serve it as an application route would be a silent route collision.
_RESERVED_PATHS = frozenset({DISPATCH_PATH})


def mount_non_http_dispatch(
    app: FastAPI,
    package: str,
    *,
    foundry_function: str | None,
    pass_through_path: str | None,
) -> None:
    """No-op unless `foundry_function` is set -- `ServiceConfig` leaves it
    `None` for the HTTP function and for every non-Lambda deploy target, so
    this call is always safe to make from every service's `main.py`
    unconditionally, mirroring how this file already does not learn which
    deploy target it runs under.

    Takes both values as ARGUMENTS rather than reading `os.environ` itself:
    `config.py` is the one module in this package that reads the process
    environment (S18; `check_layering.py` rule 3; `test_layering.py`), and
    every other module receives what it needs through a constructor or
    function argument. `main.py` passes `config.foundry_function` and
    `config.aws_lwa_pass_through_path` here.

    Raises at IMPORT time, not at first invocation, if `foundry_function`
    is set without `pass_through_path`: a container that boots successfully
    but can never receive an invocation is a worse failure mode than one
    that refuses to boot at all, because a `502`/timeout at the first real
    invoke gives no indication that the container itself is the problem,
    whereas a boot-time refusal names the exact missing variable in the
    deploy log.
    """
    function_name = foundry_function
    if not function_name:
        return

    if not pass_through_path:
        raise RuntimeError(
            "FOUNDRY_FUNCTION is set (to "
            f"{function_name!r}) but AWS_LWA_PASS_THROUGH_PATH is not. "
            "Without it, the Lambda Web Adapter never forwards an "
            "invocation to this process, so the function would boot and "
            "then never answer a real invoke. Set AWS_LWA_PASS_THROUGH_PATH "
            f"to {DISPATCH_PATH!r} in this function's environment."
        )
    if pass_through_path != DISPATCH_PATH:
        raise RuntimeError(
            f"AWS_LWA_PASS_THROUGH_PATH is {pass_through_path!r}, but this "
            f"module only serves {DISPATCH_PATH!r}. A mismatch here is the "
            "same failure as leaving it unset: the adapter forwards to a "
            "path nothing answers."
        )

    module = importlib.import_module(f"{package}.composition.{function_name}_main")
    handler = module.handler

    @app.post(DISPATCH_PATH)
    async def _dispatch(request: Request) -> Response:
        body = await request.body()
        event: Any = json.loads(body) if body else {}
        result = handler(event, None)
        return Response(content=json.dumps(result), media_type="application/json")
