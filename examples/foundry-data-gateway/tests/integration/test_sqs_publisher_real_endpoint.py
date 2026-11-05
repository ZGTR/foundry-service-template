"""`SqsPublisher` against a REAL SQS HTTP endpoint -- no mock, no patched SDK.

§7 says a queue is substituted by LocalStack SQS, not by a `MagicMock`. The
`aws_kit` fixture provides that: LocalStack from
`service-kit/docker/compose/localstack.yml`
when a Docker daemon answers, and `moto[server]` -- a standalone HTTP process
speaking the same API -- when it does not. Both are SERVICES the adapter reaches
over the network with real signing; neither is a patched object.

When neither is reachable the fixture raises a NAMED skip carrying the remedy.
That is not a pass, and this file says so in its own assertions: it records
which surface it ran against, so a report cannot claim production parity it did
not have.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from foundry_data_gateway.adapters.real import SqsPublisher  # COMMS-FLAVOUR-STRIP-LINE
from foundry_data_gateway.domain import Event


def _unique(name: str) -> str:
    """`name` with a per-call suffix. The LocalStack project is named after the checkout
    (`compose_project_name`), so a re-run after an interrupted one reuses the same stack and
    finds what that run created: a second `create_secret` under a fixed name raises
    ResourceExistsException, and a fixed queue can hand back the earlier run's message
    (measured 2026-10-09)."""
    return f"{name}-{uuid.uuid4().hex[:12]}"


def _event(name: str) -> Event:
    return Event(
        event_type="widget.created",
        org_id="org_real",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        payload={"name": name},
    )


# COMMS-FLAVOUR-STRIP:BEGIN: the SQS point-to-point publish path,
# no queue URL for comms services
def test_publish_puts_a_readable_message_on_a_real_queue(aws_kit) -> None:
    sqs = aws_kit.client("sqs")
    queue_url = sqs.create_queue(QueueName=_unique("widget-events"))["QueueUrl"]
    publisher = SqsPublisher(
        queue_url=queue_url, region_name=aws_kit.region, endpoint_url=aws_kit.endpoint_url
    )

    publisher.publish(_event("Published for real"))

    received = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=1)
    messages = received.get("Messages", [])
    assert len(messages) == 1, f"nothing arrived on {aws_kit.flavour}: {received}"
    body = json.loads(messages[0]["Body"])
    assert body["event_type"] == "widget.created"
    assert body["org_id"] == "org_real"
    assert body["payload"] == {"name": "Published for real"}
# COMMS-FLAVOUR-STRIP:END


def test_the_surface_that_ran_is_recorded_not_inferred(aws_kit) -> None:
    """A report that does not say WHICH surface ran cannot claim parity.

    LocalStack is the production-parity path; moto implements a subset, so a
    behaviour it gets wrong is a behaviour this suite will not catch. Recording
    the flavour is what keeps that difference visible instead of assumed.
    """
    assert aws_kit.flavour in {"localstack", "moto-server"}
    assert aws_kit.endpoint_url.startswith("http://")


def test_an_object_fixture_round_trips_through_real_s3(aws_kit, fixture_dir) -> None:
    """The §7 object-storage fixture shape, loaded into a real S3 surface."""
    fixture = json.loads((fixture_dir / "widget_objects.json").read_text())

    loaded = aws_kit.load_object_fixture(fixture)

    # HAND-WRITTEN literal, not `len(fixture["objects"])`: an expectation read
    # off the artifact under test is true for any fixture, including an empty one.
    assert loaded == 2
    s3 = aws_kit.client("s3")
    listing = s3.list_objects_v2(Bucket=fixture["bucket"])
    assert listing["KeyCount"] == 2
    first = s3.get_object(
        Bucket=fixture["bucket"], Key="widgets/org_seed/widget_seed_0001.json"
    )["Body"].read()
    assert json.loads(first)["widget_id"] == "widget_seed_0001"


def test_a_secret_round_trips_through_real_secrets_manager(aws_kit) -> None:
    """The credential path §7 names, against the real API rather than a fake."""
    from foundry_data_gateway.adapters.real import AwsSecretsManagerSecrets

    secrets_api = aws_kit.client("secretsmanager")
    name = _unique("foundry/widgets/db-password")
    secrets_api.create_secret(Name=name, SecretString="s3cr3t")

    adapter = AwsSecretsManagerSecrets(region=aws_kit.region, client=secrets_api)

    assert adapter.get(name) == "s3cr3t"
