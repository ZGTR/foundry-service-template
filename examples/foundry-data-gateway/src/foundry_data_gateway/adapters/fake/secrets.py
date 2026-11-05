"""In-memory `SecretsPort` for tests. Never wired by `build_container()`."""

from __future__ import annotations

from foundry_data_gateway.ports import SecretNotFound


class FakeSecrets:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def get(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError:
            raise SecretNotFound(f"fake secret {name!r} not set") from None
