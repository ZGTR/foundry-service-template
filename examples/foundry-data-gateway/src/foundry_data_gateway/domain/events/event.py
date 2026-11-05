"""The immutable fact type every publisher carries and every consumer receives."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable fact this service published."""

    event_type: str
    org_id: str
    occurred_at: datetime
    payload: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.event_type:
            raise ValueError("Event.event_type must be non-empty")
        if self.occurred_at.tzinfo is None:
            raise ValueError("Event.occurred_at must be timezone-aware")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


EventHandler = Callable[[Event], None]
