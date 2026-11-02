"""The fake for `ports.auth.PrincipalAuth`."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_contracts.principal import CallerPrincipalV1, PrincipalMinter


class FakePrincipalAuth:
    """Mints a fixed or header-independent principal. No token parsing at all."""

    def __init__(self, principal: CallerPrincipalV1 | None = None) -> None:
        self._principal = principal or PrincipalMinter(issuer="fake").mint(
            org_id="org_test",
            subject_id="user_test",
            scopes={"widgets:read", "widgets:write"},
        )

    def authenticate(self, headers: Mapping[str, str]) -> CallerPrincipalV1:
        return self._principal
