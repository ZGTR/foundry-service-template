from __future__ import annotations

import pytest
from foundry_contracts.errors import Unauthorized
from foundry_contracts.principal import PrincipalMinter

from foundry_data_gateway.adapters.fake import HeaderPrincipalAuth


def _auth() -> HeaderPrincipalAuth:
    return HeaderPrincipalAuth(PrincipalMinter(issuer="test-issuer"))


def test_mints_principal_from_headers() -> None:
    principal = _auth().authenticate(
        {"x-org-id": "org_1", "x-subject-id": "user_1", "x-scopes": "widgets:read, widgets:write"}
    )

    assert principal.org_id == "org_1"
    assert principal.subject_id == "user_1"
    assert principal.scopes == frozenset({"widgets:read", "widgets:write"})
    assert principal.issued_by == "test-issuer"


def test_missing_org_header_is_unauthorized() -> None:
    with pytest.raises(Unauthorized):
        _auth().authenticate({"x-subject-id": "user_1"})


def test_missing_subject_header_is_unauthorized() -> None:
    with pytest.raises(Unauthorized):
        _auth().authenticate({"x-org-id": "org_1"})


def test_missing_scopes_header_yields_no_scopes() -> None:
    principal = _auth().authenticate({"x-org-id": "org_1", "x-subject-id": "user_1"})

    assert principal.scopes == frozenset()
