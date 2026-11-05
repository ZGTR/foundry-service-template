"""The CONFORMING half: the same two collaborators, driven by fakes.

Deliberately different wording from the real suite -- a gate that only stays
quiet on phrasing it recognises is matching an idiom, not a rule. If this file
is ever flagged, widen the matcher; do not reword this file.
"""


class DictStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], object] = {}

    def put(self, tenant: str, key: str, value: object) -> None:
        self._rows[(tenant, key)] = value

    def fetch(self, tenant: str, key: str) -> object | None:
        return self._rows.get((tenant, key))


class CapturingRelay:
    def __init__(self) -> None:
        self.relayed: list[object] = []

    def publish(self, event: object) -> None:
        self.relayed.append(event)


def test_absent_row_reads_as_none() -> None:
    assert DictStore().fetch("tenant", "absent") is None


def test_relay_captures_in_order() -> None:
    relay = CapturingRelay()
    relay.publish("first")
    relay.publish("second")
    assert relay.relayed == ["first", "second"]
