"""Non-HTTP inbound entrypoints: queue consumers and scheduled jobs.

Same rule as `http/controllers/`: parse the inbound record, call the thing you
were given, done. These never import `adapters` -- the composition root binds
the concrete call (which DOES know about the outbox adapter) to a plain
callable and hands it in.
"""

from __future__ import annotations

from foundry_data_gateway.transport.events.outbox_drain import handle_scheduled_drain
from foundry_data_gateway.transport.events.widget_events_consumer import handle_sqs_record

__all__ = ["handle_scheduled_drain", "handle_sqs_record"]
