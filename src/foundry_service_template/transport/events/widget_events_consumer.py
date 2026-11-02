"""Illustrative SQS consumer for `widget.created` events published by
`SqsPublisher`. Deliberately trivial -- the point is the entrypoint shape
(parse the queue record, extract the event), not a real projection.
"""

from __future__ import annotations

import json
from typing import Any


def handle_sqs_record(record: dict[str, Any]) -> dict[str, object]:
    body = json.loads(record["body"])
    return {"event_type": body["event_type"], "org_id": body["org_id"], "payload": body["payload"]}
