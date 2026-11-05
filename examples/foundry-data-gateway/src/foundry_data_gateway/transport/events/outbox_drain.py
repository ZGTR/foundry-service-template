"""Scheduled entrypoint: relay everything the request path wrote to the
outbox but has not yet delivered. Wired as an EventBridge scheduled rule
(`infra/lambda`) or a periodic ECS task, never called from the request path.

Like every module under `handlers/`, this never imports `adapters/` -- the
composition root (`main.py`) binds the concrete `drain_outbox()` call (which
DOES know about the outbox adapter) to a zero-argument callable and hands
that in. This handler only knows "call the thing I was given."
"""

from __future__ import annotations

from collections.abc import Callable


def handle_scheduled_drain(drain: Callable[[], int]) -> int:
    """Invoke the composition-root-bound drain callable. Returns the count relayed."""
    return drain()
