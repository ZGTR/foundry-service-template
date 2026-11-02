"""Canonical JSON and content digests.

Two structurally identical payloads must produce identical bytes, or every
idempotency key, receipt digest and cache hit built on top of them is unstable.
Key order, separator whitespace and non-ASCII escaping are all pinned here.

Moved into the platform rather than imported from a consumer: a digest function
is a shared *contract*, and a blackbox that imported it from an application would
make that application a dependency of every other consumer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import rfc8785


def canonical_json(payload: Any) -> str:
    """Serialise ``payload`` so that equal values always produce equal strings."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_fallback,
    )


def canonical_digest(payload: Any) -> str:
    """Return the SHA-256 hex digest of ``payload``'s canonical form."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def jcs_canonical_json(payload: Any) -> str:
    """Serialize I-JSON data using RFC 8785 JSON Canonicalization Scheme bytes."""
    return rfc8785.dumps(payload).decode("utf-8")


def jcs_canonical_digest(payload: Any) -> str:
    """Return the SHA-256 digest of RFC 8785 canonical UTF-8 bytes."""
    return hashlib.sha256(rfc8785.dumps(payload)).hexdigest()


def _fallback(value: Any) -> Any:
    for attr in ("isoformat", "to_dict"):
        method = getattr(value, attr, None)
        if callable(method):
            return method()
    if hasattr(value, "__dataclass_fields__"):
        import dataclasses

        return dataclasses.asdict(value)
    raise TypeError(f"{type(value).__name__} is not canonically serialisable")
