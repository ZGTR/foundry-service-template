"""`InProcessEventBus` -- synchronous, same-process, NOT durable.

Legitimate for cache invalidation and anything cheap to lose on a crash (plan
section 4's decision rule). The one contract this adapter must uphold: a
failing subscriber never breaks the publisher, and never breaks its sibling
subscribers either. `tests/integration/test_in_process_event_bus.py` proves
this by registering two subscribers where the first always raises, and
asserting the second still runs and the publisher's `publish()` call still
returns normally.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from foundry_service_template.ports import Event, EventHandler

logger = logging.getLogger(__name__)


class InProcessEventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self.failure_count = 0

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        for handler in list(self._handlers[event.event_type]):
            try:
                handler(event)
            except Exception:  # noqa: BLE001 -- isolation is the whole point
                self.failure_count += 1
                logger.exception(
                    "subscriber for event_type=%s raised; isolated, publish continues",
                    event.event_type,
                )
