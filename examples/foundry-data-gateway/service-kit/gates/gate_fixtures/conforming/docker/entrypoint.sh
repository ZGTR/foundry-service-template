#!/bin/sh
# Conforming: an explicit command WINS, and the default is the last exec.
# Worded unlike the template's entrypoint on purpose -- a gate that only stays
# quiet on phrasing it recognises is matching an idiom, not the rule.
set -eu

if [ "$#" -ne 0 ]; then
  exec "$@"
fi

exec /srv/venv/bin/python -m service.main
