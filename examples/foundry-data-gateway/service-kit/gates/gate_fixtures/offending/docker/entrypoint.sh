#!/bin/sh
# GATE_MUST_FLAG_THIS -- clause: the command forward.
#
# THE TOKEN IS NOT THE PROPERTY. This file contains `exec "$@"` TWICE and still
# discards every CMD: once commented out, once stranded after an unconditional
# exec that never returns. `grep -c 'exec "$@"'` reports 2 and reads as
# conforming, which is why the gate walks reachable lines instead.
set -eu

# exec "$@"

exec /srv/venv/bin/python -m service.main

exec "$@"
