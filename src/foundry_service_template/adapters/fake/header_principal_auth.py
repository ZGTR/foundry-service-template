"""A FAKE `PrincipalAuth` that trusts three plain headers. Never production.

It lived under `adapters/real/` until 2026-09-04 and that was the defect. It
verifies no signature, so any caller can assert any org and any scope -- yet
being in the `real/` directory let it satisfy a "no fake adapter in the default
path" review by its LOCATION while contradicting it by its BEHAVIOUR. Real by
directory, fake by behaviour, is the worst arrangement of the two: the thing
that reads as evidence and the thing that is true point opposite ways.

It keeps its job of proving the SHAPE -- one minter, one adapter, principals
arrive at controllers already minted, a controller can never fabricate one --
and a service supplies a signature-verifying adapter behind the same port.
`build_container()` now refuses to wire this outside a local environment, so
forgetting that step fails at startup rather than at the first hostile request.
"""

from __future__ import annotations

from collections.abc import Mapping

from foundry_contracts.errors import Unauthorized
from foundry_contracts.principal import CallerPrincipalV1, PrincipalMinter


class HeaderPrincipalAuth:
    def __init__(self, minter: PrincipalMinter) -> None:
        self._minter = minter

    def authenticate(self, headers: Mapping[str, str]) -> CallerPrincipalV1:
        org_id = headers.get("x-org-id")
        subject_id = headers.get("x-subject-id")
        if not org_id or not subject_id:
            raise Unauthorized("missing x-org-id or x-subject-id header")
        raw_scopes = headers.get("x-scopes", "")
        scopes = {s.strip() for s in raw_scopes.split(",") if s.strip()}
        return self._minter.mint(org_id=org_id, subject_id=subject_id, scopes=scopes)
