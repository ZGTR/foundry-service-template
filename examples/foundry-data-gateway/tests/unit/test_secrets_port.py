"""The secrets port's contract, proven against every adapter that claims it.

One test body, every adapter: a port is only real if the implementations agree
about it, and the cheapest way for them to drift is a contract asserted against
only one of them.
"""

from __future__ import annotations

import pytest

from foundry_data_gateway.adapters.fake import FakeSecrets
from foundry_data_gateway.adapters.real.aws_secrets_manager import AwsSecretsManagerSecrets
from foundry_data_gateway.ports import SecretNotFound, SecretsPort


class _StubSecretsManagerClient:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values
        self.calls = 0

    def get_secret_value(self, SecretId: str) -> dict[str, str]:  # noqa: N803 -- boto3's name
        self.calls += 1
        if SecretId not in self._values:
            raise KeyError(SecretId)
        return {"SecretString": self._values[SecretId]}


def _adapters() -> list[SecretsPort]:
    return [
        FakeSecrets({"db-password": "s3cret"}),
        AwsSecretsManagerSecrets(
            region="eu-west-1", client=_StubSecretsManagerClient({"db-password": "s3cret"})
        ),
    ]


@pytest.mark.parametrize("secrets", _adapters(), ids=lambda a: type(a).__name__)
def test_every_adapter_satisfies_the_port(secrets: SecretsPort) -> None:
    assert isinstance(secrets, SecretsPort)
    assert secrets.get("db-password") == "s3cret"


@pytest.mark.parametrize("secrets", _adapters(), ids=lambda a: type(a).__name__)
def test_a_missing_secret_raises_rather_than_returning_none(secrets: SecretsPort) -> None:
    with pytest.raises(SecretNotFound):
        secrets.get("absent")


@pytest.mark.parametrize("secrets", _adapters(), ids=lambda a: type(a).__name__)
def test_the_error_names_the_secret_but_never_its_value(secrets: SecretsPort) -> None:
    with pytest.raises(SecretNotFound) as excinfo:
        secrets.get("absent")
    assert "absent" in str(excinfo.value)
    assert "s3cret" not in str(excinfo.value)


def test_aws_adapter_builds_without_touching_the_network() -> None:
    """Lazy client, like every other real adapter -- build_container() stays offline."""
    adapter = AwsSecretsManagerSecrets(region="eu-west-1")
    assert adapter._client is None


def test_aws_adapter_caches_so_a_hot_path_stays_off_the_network() -> None:
    client = _StubSecretsManagerClient({"db-password": "s3cret"})
    adapter = AwsSecretsManagerSecrets(region="eu-west-1", client=client)
    assert adapter.get("db-password") == "s3cret"
    assert adapter.get("db-password") == "s3cret"
    assert client.calls == 1
