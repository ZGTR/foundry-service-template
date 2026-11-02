"""Observable fake for the service-owned transaction boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


class RecordingTransaction:
    def __init__(self) -> None:
        self.entries = 0

    @contextmanager
    def enter(self) -> Iterator[None]:
        self.entries += 1
        yield
