#!/usr/bin/env sh
# Block until a TCP endpoint accepts a connection, then exec the rest of the
# command line. Generic across every service (§5): the host, port and timeout
# are arguments, so nothing here is service-specific.
#
#   wait-for.sh postgres:5432 -- alembic upgrade head
#   wait-for.sh localstack:4566 --timeout 60 -- python -m pytest tests/integration
#
# IT MUST END IN `exec ... "$@"`, for the same reason entrypoint.sh does: a
# wrapper that runs a hardcoded command DISCARDS the command it was given, and
# the discard is silent. `tests/shape/test_docker_shared.py` asserts the forward
# on every script in this folder that takes a command.
set -eu

TIMEOUT=30
TARGETS=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --) shift; break ;;
        *) TARGETS="${TARGETS} $1"; shift ;;
    esac
done

if [ -z "${TARGETS# }" ]; then
    echo "wait-for: no host:port given" >&2
    echo "usage: wait-for.sh <host:port> [<host:port>...] [--timeout N] -- <command>" >&2
    exit 2
fi

for target in ${TARGETS}; do
    host="${target%%:*}"
    port="${target##*:}"
    elapsed=0
    until "${WAIT_FOR_PYTHON:-python}" -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
except OSError:
    sys.exit(1)
finally:
    s.close()
" "${host}" "${port}" 2>/dev/null; do
        if [ "${elapsed}" -ge "${TIMEOUT}" ]; then
            echo "wait-for: ${target} still unreachable after ${TIMEOUT}s" >&2
            exit 1
        fi
        elapsed=$((elapsed + 1))
        sleep 1
    done
    echo "wait-for: ${target} is accepting connections" >&2
done

if [ "$#" -eq 0 ]; then
    exit 0
fi

exec "$@"
