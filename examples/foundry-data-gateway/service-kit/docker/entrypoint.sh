#!/usr/bin/env sh
# The container's ENTRYPOINT. Detects which runtime it is under and always
# execs the SAME command -- `$SERVICE_PACKAGE.composition.main` -- because the
# whole point of "one image, two runtimes" (Dockerfile's docstring) is that
# nothing here forks the launch path. What differs is only:
#
#   * whether the Lambda Web Adapter extension is the thing that started
#     this script (Lambda invokes the adapter, not this script, first) --
#     that is entirely outside this script's control and requires no branch
#     here;
#   * the RUNTIME_MODE value this script exports for ServiceConfig to read,
#     used only for things like log formatting, never to change what runs.
#
# `tests/shape/test_entrypoint.py` drives this file directly under both
# conditions and asserts the detection + export, with `python` replaced by a
# stub so the test never needs a real interpreter or app boot.
set -eu

RUNTIME_PYTHON="${ENTRYPOINT_PYTHON:-python}"

# WHICH SERVICE THIS IS, and it does NOT default. This file is kit-owned and
# byte-identical in every service (contract §5), so a package name written here
# is wrong in every service but one -- which is exactly what it was until the
# kit moved: this script named `foundry_service_template` outright, so a synced
# service would have tried to run the template's package and died on
# ModuleNotFoundError at container start. The Dockerfile exports
# SERVICE_PACKAGE from its required build arg; refusing here rather than
# guessing keeps the failure at the moment the image is BUILT wrong instead of
# the moment it is deployed.
if [ -z "${SERVICE_PACKAGE:-}" ]; then
    echo "entrypoint: SERVICE_PACKAGE is unset. The image must be built with --build-arg SERVICE_PACKAGE=<the package under src/>, which the Dockerfile exports as an environment variable for this script." >&2
    exit 1
fi

if [ -n "${AWS_LAMBDA_FUNCTION_NAME:-}" ]; then
    echo "entrypoint: AWS_LAMBDA_FUNCTION_NAME=${AWS_LAMBDA_FUNCTION_NAME} -- running under Lambda; the Lambda Web Adapter forwards invocations to this process over HTTP" >&2
    export RUNTIME_MODE="lambda"
else
    echo "entrypoint: no Lambda runtime detected -- running as an ordinary ECS/local HTTP service" >&2
    export RUNTIME_MODE="${RUNTIME_MODE:-http}"
fi

# AN EXPLICIT COMMAND WINS. Without this branch the image has exactly one launch
# path and every `image.command` (serverless) or `command:` (ECS task definition)
# is DISCARDED: the CMD arrives here as "$@", and a script that never names "$@"
# throws it away.
#
# That is not hypothetical. This file and an emitted service copy were produced
# from the same template. That copy shipped a `drainOutbox` Lambda
# whose `image.command` was
#   [/opt/venv/bin/python, -c, "from ...handlers.outbox_drain import handle_scheduled_drain"]
# and which was recorded, in `deploy/iac/serverless.yml` and in the workflow-ledger
# blackbox proof, as "imports the symbol and exits". It did neither. The
# arguments landed here and were dropped, so what ran 1440 times a day was the
# HTTP API server. Measured again on 2026-09-05 against this file, with a stub
# interpreter: all three probe cells -- no arguments, an `-m` command, an exact
# `-c` argv -- printed the identical `-m "$SERVICE_PACKAGE".composition.main`, unchanged. Nothing in this
# repository declares a container CMD today, so the defect was latent rather
# than live here; the fix is prophylactic and belongs in every copy, because the
# next service or scheduled one-shot to declare one would inherit the swallow
# silently (discovered during review).
#
# RUNTIME_MODE is exported above, so a one-shot command inherits the same runtime
# detection the service does.
if [ "$#" -gt 0 ]; then
  echo "entrypoint: explicit command given -- running it instead of the HTTP service: $*" >&2
  exec "$@"
fi

exec "${RUNTIME_PYTHON}" -m "${SERVICE_PACKAGE}.composition.main"
