#!/usr/bin/env sh
# The container HEALTHCHECK. Generic across every service: what differs is a
# PORT and a PATH supplied through the environment, never a forked copy of this
# script -- §5 of the module contract.
#
# It probes /health/ready, NOT /health/live. Those are different questions (see
# src/<pkg>/runtime/probes.py): liveness answers "is the process up", which a
# container healthcheck can already tell by the process existing. Readiness
# answers "can it serve", which is the only one worth an HTTP round trip here.
#
# EXIT CODES ARE THE CONTRACT: 0 healthy, 1 unhealthy. Docker reads nothing else.
set -eu

HEALTHCHECK_PORT="${HEALTHCHECK_PORT:-${PORT:-8080}}"
HEALTHCHECK_PATH="${HEALTHCHECK_PATH:-/health/ready}"
HEALTHCHECK_TIMEOUT="${HEALTHCHECK_TIMEOUT:-3}"
URL="http://127.0.0.1:${HEALTHCHECK_PORT}${HEALTHCHECK_PATH}"

if command -v curl >/dev/null 2>&1; then
    # --fail turns a 503 into a non-zero exit. Without it curl exits 0 on a 503
    # body it fetched successfully, and an unready container reads as healthy.
    exec curl --fail --silent --show-error --max-time "${HEALTHCHECK_TIMEOUT}" "${URL}"
fi

# No curl in a distroless-ish image: use the interpreter that is definitely there.
exec "${HEALTHCHECK_PYTHON:-python}" - "${URL}" "${HEALTHCHECK_TIMEOUT}" <<'PYEOF'
import sys
import urllib.request

url, timeout = sys.argv[1], float(sys.argv[2])
try:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        sys.exit(0 if response.status == 200 else 1)
except Exception as exc:  # noqa: BLE001 -- any failure to reach it is unhealthy
    print(f"healthcheck: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
