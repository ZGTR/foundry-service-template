#!/bin/sh
set -eu
TARGET="$1"
shift
until nc -z "${TARGET%%:*}" "${TARGET##*:}"; do
  sleep 1
done
exec "$@"
