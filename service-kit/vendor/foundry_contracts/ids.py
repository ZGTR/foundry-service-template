"""Identifier construction.

Two kinds, and confusing them is the bug this module exists to prevent:

``new_id``     is random. Use it when the thing being named has no prior identity.
``derive_id``  is a pure function of its inputs. Use it when re-running the same
               logical operation must produce the same name, which is what makes
               an idempotency key work across a retry in a different process.
"""

from __future__ import annotations

import uuid

from foundry_contracts.canon import canonical_digest


def new_id(prefix: str) -> str:
    """Return a fresh random identifier, e.g. ``run_9f2c...``."""
    _require_prefix(prefix)
    return f"{prefix}_{uuid.uuid4().hex}"


def derive_id(prefix: str, *parts: object) -> str:
    """Return the identifier implied by ``parts``.

    Deterministic across processes and machines: the same parts always yield the
    same id, so a retry after an ambiguous failure addresses the same row.
    """
    _require_prefix(prefix)
    if not parts:
        raise ValueError("derive_id requires at least one part")
    return f"{prefix}_{canonical_digest(list(parts))[:32]}"


def _require_prefix(prefix: str) -> None:
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError(f"invalid id prefix: {prefix!r}")
