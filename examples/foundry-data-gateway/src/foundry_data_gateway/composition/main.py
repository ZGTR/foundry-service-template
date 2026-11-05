"""The process entrypoint. The ONLY module that runs at import time in the
container -- everything it calls (`ServiceConfig.from_environment()`,
`build_container()`, `create_app()`) takes its inputs explicitly.

This same entrypoint runs unmodified under both deploy targets:

* **ECS Fargate** -- this file is the container's `CMD`. `uvicorn` binds
  `PORT` (default 8080) and serves traffic directly behind the ALB.
* **Lambda (image package type)** -- the AWS Lambda Web Adapter, installed as
  a layer in `Dockerfile`, is itself the process the Lambda runtime invokes.
  It starts this exact command as a child process, forwards each Lambda
  invocation to `http://127.0.0.1:$PORT` as an ordinary HTTP request, and
  returns the HTTP response as the Lambda response.

This file never learns which target it is running under, and NOTHING IN THE
PROCESS DOES. That is stronger than it used to be, and the difference is worth
recording. `ServiceConfig` once carried a `runtime_mode` field derived from
`AWS_LAMBDA_FUNCTION_NAME`, and this docstring said in as many words that
nothing here branched on it -- it existed "for adapters/handlers that care".
No adapter or handler ever did, in this template or in any service emitted from
it, so the field was a knob whose only consumer was the sentence describing it.
Both it and the derivation are gone; the Lambda Web Adapter
is what makes the two targets identical, and it needs no cooperation from this
module. A future log formatter that genuinely wants the runtime can read
`AWS_LAMBDA_FUNCTION_NAME` where it is used, which is one reader rather than a
config surface an operator can set.
"""

from __future__ import annotations

import math

import uvicorn

from foundry_data_gateway.composition.app_factory import create_app
from foundry_data_gateway.composition.config import ServiceConfig
from foundry_data_gateway.composition.container import build_container
from foundry_data_gateway.composition.lambda_dispatch import mount_non_http_dispatch

config = ServiceConfig.from_environment()
app = create_app(build_container(config))

# A non-HTTP function sets FOUNDRY_FUNCTION and AWS_LWA_PASS_THROUGH_PATH; the
# HTTP function sets neither, so this call is a no-op for it. One image, one
# CMD, one runtime client (the Lambda Web Adapter) either way -- see
# lambda_dispatch's own docstring for why a second client (awslambdaric)
# alongside the adapter silently never reaches the handler.
mount_non_http_dispatch(
    app,
    "foundry_data_gateway",
    foundry_function=config.foundry_function,
    pass_through_path=config.aws_lwa_pass_through_path,
)


def run() -> None:
    # One ceiling for both waits on SIGTERM: uvicorn's wait for open connections
    # and the lifecycle's drain. Without it uvicorn waits on a stuck connection
    # indefinitely, and SHUTDOWN_GRACE_SECONDS bounds only the half after it.
    # Rounded UP: uvicorn takes whole seconds, and truncating 0.5 to 0 would
    # cancel every in-flight request the moment the signal lands.
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 -- container-internal bind
        port=config.port,
        timeout_graceful_shutdown=math.ceil(config.shutdown_grace_seconds),
    )


if __name__ == "__main__":
    run()
