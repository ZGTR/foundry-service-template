"""`SqsPublisher` -- asynchronous, at-least-once, cross-service fan-out.

`boto3` is an optional dependency (`pyproject.toml`'s `[project.optional-dependencies].aws`),
so the *import* of this module must succeed even when `boto3` is not
installed -- only *constructing* `SqsPublisher` requires it. That is the
import guard: `tests/unit/test_sqs_event_bus_import_guard.py` proves the
module imports with `boto3` absent from `sys.modules`, and that instantiating
it in that state raises `ConfigurationError` rather than `ImportError` leaking
out of a vendor SDK.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from foundry_contracts.errors import ConfigurationError

from foundry_service_template.ports import Event

if TYPE_CHECKING:
    import boto3  # noqa: F401 -- type-checking only, never imported at runtime here

try:
    import boto3 as _boto3
except ImportError:  # pragma: no cover -- exercised via the import-guard test
    _boto3 = None


class SqsPublisher:
    def __init__(
        self,
        *,
        queue_url: str,
        region_name: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        """`endpoint_url` points the SDK at a LOCAL AWS surface.

        It exists so a test can exercise this adapter against a real HTTP
        endpoint -- LocalStack from `service-kit/docker/compose/localstack.yml`, or
        `moto[server]` where there is no Docker daemon -- rather than patching
        the module's `_boto3` handle. §7's rule is that a collaborator with a
        port is substituted by a fake or a real resource, and a vendor SDK
        handle is neither: patching it asserts something about the patch.
        Production leaves this None and boto3 resolves the real endpoint.
        """
        if _boto3 is None:
            raise ConfigurationError(
                "SqsPublisher requires the 'aws' extra: "
                "pip install 'foundry-service-template[aws]'"
            )
        if not queue_url:
            raise ConfigurationError("SqsPublisher requires queue_url")
        self._queue_url = queue_url
        self._region_name = region_name
        self._endpoint_url = endpoint_url
        self._client: Any | None = None

    def publish(self, event: Event) -> None:
        if self._client is None:
            self._client = _boto3.client(
                "sqs", region_name=self._region_name, endpoint_url=self._endpoint_url
            )
        self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(
                {
                    "event_type": event.event_type,
                    "org_id": event.org_id,
                    "occurred_at": event.occurred_at.isoformat(),
                    "payload": dict(event.payload),
                }
            ),
            MessageAttributes={
                "event_type": {"DataType": "String", "StringValue": event.event_type},
            },
        )
