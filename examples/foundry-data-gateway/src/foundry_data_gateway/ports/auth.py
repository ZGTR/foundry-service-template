"""The one seam where a wire identity becomes a `CallerPrincipalV1`.

Every route handler depends on this port, never on `foundry_contracts.PrincipalMinter`
directly -- the minter is held by exactly one adapter
(`adapters/fake/header_principal_auth.py`), so there is exactly one place in
the whole service that can mint a principal for another tenant, and it is
covered by `tests/unit/test_header_principal_auth.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from foundry_contracts.principal import CallerPrincipalV1


@runtime_checkable
class PrincipalAuth(Protocol):
    def authenticate(self, headers: Mapping[str, str]) -> CallerPrincipalV1:
        """Mint a principal from inbound request headers, or raise `Unauthorized`."""
        ...
