"""Proof that an effect happened.

Iron rule 4: every effect that costs money or leaves the process emits a receipt.
A receipt is written *after* the effect and is immutable; it is the only evidence
that survives a process death, so it carries enough to reconcile one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from foundry_contracts.canon import canonical_digest


@dataclass(frozen=True, slots=True)
class ReceiptV1:
    """An immutable record of one completed external effect."""

    receipt_id: str
    org_id: str
    kind: str
    occurred_at: datetime
    idempotency_key: str
    outcome: str
    cost_micros: int = 0
    detail: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    _OUTCOMES = ("succeeded", "refused", "failed", "possible")

    def __post_init__(self) -> None:
        if self.outcome not in self._OUTCOMES:
            raise ValueError(f"outcome must be one of {self._OUTCOMES}, got {self.outcome!r}")
        if self.occurred_at.tzinfo is None:
            raise ValueError("ReceiptV1.occurred_at must be timezone-aware")
        if self.cost_micros < 0:
            raise ValueError("ReceiptV1.cost_micros must not be negative")
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))

    @property
    def digest(self) -> str:
        """A stable content digest, for tamper-evidence and de-duplication."""
        return canonical_digest(
            {
                "receipt_id": self.receipt_id,
                "org_id": self.org_id,
                "kind": self.kind,
                "occurred_at": self.occurred_at.isoformat(),
                "idempotency_key": self.idempotency_key,
                "outcome": self.outcome,
                "cost_micros": self.cost_micros,
                "detail": dict(self.detail),
            }
        )
