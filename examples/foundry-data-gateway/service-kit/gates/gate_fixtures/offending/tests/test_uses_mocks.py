# GATE_MUST_FLAG_THIS -- clause: check_mock_budget. Two mock uses against
# collaborators that BOTH have ports and BOTH have fakes:
#   * a MagicMock standing in for a repository, and
#   * a monkeypatch.setattr replacing a publisher's method.
# Neither proves anything about the real adapter, because `MagicMock` agrees
# with whatever the test asserts.
from unittest.mock import MagicMock


def test_create_with_a_mocked_repository() -> None:
    repository = MagicMock()
    repository.get.return_value = None
    assert repository.get("org", "id") is None


def test_publisher_patched_on_the_object(monkeypatch) -> None:
    class Publisher:
        def publish(self, event: object) -> None:
            raise AssertionError("unreachable")

    publisher = Publisher()
    monkeypatch.setattr(publisher, "publish", lambda event: None)
    publisher.publish(object())
