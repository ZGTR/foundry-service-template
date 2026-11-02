#!/bin/sh
set -eu
exec curl --fail --silent "http://127.0.0.1:${PORT:-8080}/health/ready"
