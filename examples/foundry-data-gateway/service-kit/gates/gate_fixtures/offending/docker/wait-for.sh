#!/bin/sh
# CORRECT, deliberately: see the Dockerfile's note. Its silence is load-bearing.
set -eu
TARGET="$1"
shift
until nc -z "${TARGET%%:*}" "${TARGET##*:}"; do
  sleep 1
done
exec "$@"
