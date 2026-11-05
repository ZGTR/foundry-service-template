"""AWS Secrets Manager behind `SecretsPort`.

Connects LAZILY, like every other real adapter here, so `build_container()`
stays network-free and `test_container_builds_with_zero_network_access` keeps
holding once a service wires this in.

Values are cached for the process lifetime. That is deliberate and it is a
trade-off worth naming: it keeps a hot path off the network, and it means a
rotated secret needs a new task rather than being picked up mid-process. The
alternative -- re-reading per call -- is the ambient-config bug pattern
`config.py` exists to rule out, one layer down.
"""

from __future__ import annotations

from foundry_data_gateway.ports import SecretNotFound


class AwsSecretsManagerSecrets:
    def __init__(self, *, region: str, client=None) -> None:
        self._region = region
        self._client = client
        self._cache: dict[str, str] = {}

    def _resolve_client(self):
        if self._client is None:
            import boto3  # imported here so building the container touches no network

            self._client = boto3.client("secretsmanager", region_name=self._region)
        return self._client

    def get(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]
        try:
            response = self._resolve_client().get_secret_value(SecretId=name)
        except Exception as exc:  # noqa: BLE001 -- re-raised with the NAME only
            raise SecretNotFound(f"secret {name!r} did not resolve in {self._region}") from exc
        value = response.get("SecretString")
        if value is None:
            raise SecretNotFound(f"secret {name!r} has no SecretString")
        self._cache[name] = value
        return value
