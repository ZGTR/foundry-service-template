"""Who is asking.

Boundary rule 2: a blackbox never accepts a tenant identifier as a plain string
from its caller's request body, because a string proves nothing about where it
came from. It accepts a ``CallerPrincipalV1``, and only the host's authentication
adapter holds the minter that can construct one.

What this buys: a route handler cannot fabricate a principal for another tenant by
passing a different string, because it has no way to make the object at all.

What it does NOT buy: it is not a cryptographic assertion. A host that mints a
principal from unvalidated input has an authentication bug, and no type can fix
that. The type moves the mistake to one auditable place; it does not remove it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class CallerPrincipalV1:
    """An immutable, host-minted statement of who is making a call."""

    org_id: str
    subject_id: str
    scopes: frozenset[str]
    issued_by: str
    attributes: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )

    def __post_init__(self) -> None:
        for name in ("org_id", "subject_id", "issued_by"):
            if not getattr(self, name):
                raise ValueError(f"CallerPrincipalV1.{name} must be non-empty")
        object.__setattr__(self, "scopes", frozenset(self.scopes))
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    def require(self, scope: str) -> None:
        """Raise ``Forbidden`` unless this principal carries ``scope``.

        ``Forbidden``, not ``Unauthorized``: this principal is already verified,
        so 401 told a read-only caller to authenticate when it had. It subclasses
        ``Unauthorized``, so a service that has not registered a 403 handler is
        unchanged rather than broken.
        """
        if scope not in self.scopes:
            from foundry_contracts.errors import Forbidden

            raise Forbidden(
                f"principal {self.subject_id} in org {self.org_id} lacks scope {scope!r}"
            )


class PrincipalMinter:
    """The only supported way to build a ``CallerPrincipalV1``.

    The host constructs exactly one of these inside its authentication adapter and
    does not export it. Application code receives principals; it cannot make them.
    """

    def __init__(self, *, issuer: str) -> None:
        if not issuer:
            raise ValueError("PrincipalMinter requires an issuer name")
        self._issuer = issuer

    def mint(
        self,
        *,
        org_id: str,
        subject_id: str,
        scopes: frozenset[str] | set[str] | list[str],
        attributes: Mapping[str, str] | None = None,
    ) -> CallerPrincipalV1:
        return CallerPrincipalV1(
            org_id=org_id,
            subject_id=subject_id,
            scopes=frozenset(scopes),
            issued_by=self._issuer,
            attributes=attributes or {},
        )
