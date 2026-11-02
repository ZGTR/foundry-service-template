from __future__ import annotations

from datetime import UTC, datetime

import pytest
from foundry_contracts.clock import FrozenClock
from foundry_contracts.errors import Unauthorized
from foundry_contracts.principal import PrincipalMinter

from foundry_service_template.adapters.fake import InMemoryWidgetRepository, RecordingPublisher
from foundry_service_template.application import (
    CreateWidget,
    GetWidget,
    ListWidgets,
    WidgetUseCases,
)
from foundry_service_template.domain import WIDGET_CREATED, InvalidWidgetName, WidgetNotFound

MINTER = PrincipalMinter(issuer="test")


def _principal(**scopes_kw):
    return MINTER.mint(
        org_id="org_1",
        subject_id="user_1",
        scopes=scopes_kw.get("scopes", {"widgets:read", "widgets:write"}),
    )


def _service():
    """The three use cases wired over FAKES, never mocks (contract §7).

    `InMemoryWidgetRepository` and `RecordingPublisher` are the `adapters/fake/`
    implementations of two real ports, and `tests/contract/` runs the same
    contract suite against them and against the SQLite/in-process real adapters
    -- so substituting them here is a measured claim, not a convenience.
    """
    repo = InMemoryWidgetRepository()
    publisher = RecordingPublisher()
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    use_cases = WidgetUseCases(
        create=CreateWidget(repository=repo, publisher=publisher, clock=clock),
        get=GetWidget(repository=repo),
        list=ListWidgets(repository=repo),
    )
    return use_cases, repo, publisher, clock


def test_create_widget_persists_and_publishes() -> None:
    service, repo, publisher, clock = _service()
    principal = _principal()

    widget = service.create.execute(principal, name="Sprocket")

    assert widget.name == "Sprocket"
    assert widget.org_id == "org_1"
    assert repo.get("org_1", widget.widget_id) == widget
    assert len(publisher.published) == 1
    assert publisher.published[0].event_type == WIDGET_CREATED
    assert publisher.published[0].payload["widget_id"] == widget.widget_id


def test_create_rejects_empty_name() -> None:
    service, _repo, publisher, _clock = _service()
    principal = _principal()

    with pytest.raises(InvalidWidgetName):
        service.create.execute(principal, name="   ")

    assert publisher.published == []


def test_create_rejects_name_over_max_length() -> None:
    service, _repo, _publisher, _clock = _service()
    principal = _principal()

    with pytest.raises(InvalidWidgetName):
        service.create.execute(principal, name="x" * 101)


def test_create_accepts_name_at_max_length() -> None:
    service, _repo, _publisher, _clock = _service()
    principal = _principal()

    widget = service.create.execute(principal, name="x" * 100)

    assert len(widget.name) == 100


def test_create_without_write_scope_is_unauthorized() -> None:
    service, _repo, _publisher, _clock = _service()
    principal = _principal(scopes={"widgets:read"})

    with pytest.raises(Unauthorized):
        service.create.execute(principal, name="Sprocket")


def test_get_missing_widget_raises_not_found() -> None:
    service, _repo, _publisher, _clock = _service()
    principal = _principal()

    with pytest.raises(WidgetNotFound):
        service.get.execute(principal, widget_id="widget_does_not_exist")


def test_get_scopes_by_org() -> None:
    service, _repo, _publisher, _clock = _service()
    owner = _principal()
    widget = service.create.execute(owner, name="Sprocket")

    other_org = MINTER.mint(org_id="org_2", subject_id="user_2", scopes={"widgets:read"})
    with pytest.raises(WidgetNotFound):
        service.get.execute(other_org, widget_id=widget.widget_id)


def test_list_returns_only_this_org_oldest_first() -> None:
    service, _repo, _publisher, clock = _service()
    principal = _principal()
    first = service.create.execute(principal, name="First")
    clock.advance(1)
    second = service.create.execute(principal, name="Second")

    other = MINTER.mint(org_id="org_2", subject_id="user_2", scopes={"widgets:write"})
    service.create.execute(other, name="Other org's widget")

    widgets = service.list.execute(principal)

    assert [w.widget_id for w in widgets] == [first.widget_id, second.widget_id]
