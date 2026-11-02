"""Time as an injected dependency.

A blackbox that calls ``datetime.now()`` directly cannot be tested for lease
expiry, retry backoff or receipt ordering without sleeping. The clock is a port.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time, always timezone-aware and always UTC."""
        ...


class SystemClock:
    """The production clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A clock the test controls. Advance it explicitly; it never moves on its own."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)
        return self._now
