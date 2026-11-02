"""Attribute redaction.

A span attribute is arbitrary caller data, and the caller can accidentally hand
it raw prompt or completion text -- the exact bytes a tracing backend must never
persist by default. Redaction is a port so the host decides its own policy; the
in-package default is conservative rather than permissive.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Redactor(Protocol):
    def redact(self, key: str, value: object) -> object:
        """Return the value to actually store for ``key``, possibly masked."""
        ...


_DEFAULT_DENY_KEYS = frozenset(
    {
        "prompt",
        "completion",
        "raw_prompt",
        "raw_completion",
        "input_text",
        "output_text",
        "system_prompt",
    }
)


class DenyListRedactor:
    """Masks a fixed set of keys unless the key is explicitly allow-listed.

    The deny list is the default policy: prompt/completion-shaped keys never
    reach an adapter unredacted. Opting a key back in is a constructor-time,
    per-instance decision -- never implicit, never per-call.
    """

    def __init__(
        self,
        *,
        deny_keys: frozenset[str] | None = None,
        allow_keys: frozenset[str] = frozenset(),
        mask: str = "<redacted>",
    ) -> None:
        self._deny = frozenset(deny_keys) if deny_keys is not None else _DEFAULT_DENY_KEYS
        self._allow = frozenset(allow_keys)
        self._mask = mask

    def redact(self, key: str, value: object) -> object:
        if key in self._deny and key not in self._allow:
            return self._mask
        return value
