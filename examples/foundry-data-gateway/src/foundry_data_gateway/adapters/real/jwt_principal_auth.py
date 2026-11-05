"""A signature-verifying `PrincipalAuth`. The production counterpart to the fake.

`adapters/fake/header_principal_auth.py` trusts three headers, which is fine
locally and catastrophic anywhere else. This adapter verifies a signed token
against a JWKS and mints the principal from CLAIMS, so a caller can no longer
assert an org or a scope it was not granted.

Four refusals are structural rather than configurable, because each is a known
way to turn a verifier into a rubber stamp:

* the algorithm comes from an ALLOW-LIST supplied at construction, never from
  the token. A token that names its own algorithm can name ``none``, and a
  verifier that obeys it accepts anything;
* the key is chosen by ``kid`` from the JWKS and nowhere else. A token that
  carries its own ``jwk``/``x5c`` is offering the key it was signed with, which
  proves only that the attacker can sign;
* ``issuer`` and ``audience`` are required and always checked. Signature-valid
  but issued for a different audience is the classic confused-deputy token; and
* ``exp``/``nbf`` are enforced by PyJWT and not disabled.

The JWKS is fetched lazily and cached, so building the container touches no
network -- the same discipline as every other real adapter here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_contracts.errors import Unauthorized
from foundry_contracts.principal import CallerPrincipalV1, PrincipalMinter

#: Asymmetric only. A shared secret cannot distinguish "the issuer signed this"
#: from "someone who can verify it signed it", which is the whole property a
#: gateway needs from a token it did not mint.
DEFAULT_ALGORITHMS: tuple[str, ...] = ("RS256", "RS512", "ES256", "ES512")


class JwksKeyResolver:
    """Fetches and caches a JWKS. Separated so tests can supply keys directly."""

    def __init__(self, jwks_uri: str, *, fetch=None) -> None:
        self._jwks_uri = jwks_uri
        self._fetch = fetch
        self._client = None

    def _resolve_client(self):
        if self._client is None:
            import jwt  # imported here so container construction stays offline

            self._client = jwt.PyJWKClient(self._jwks_uri)
        return self._client

    def signing_key_for(self, token: str):
        if self._fetch is not None:
            return self._fetch(token)
        return self._resolve_client().get_signing_key_from_jwt(token).key


class JwtPrincipalAuth:
    def __init__(
        self,
        minter: PrincipalMinter,
        key_resolver: JwksKeyResolver,
        *,
        issuer: str,
        audience: str,
        algorithms: Sequence[str] = DEFAULT_ALGORITHMS,
        org_claim: str = "custom:org_id",
        subject_claim: str = "sub",
        scope_claim: str = "scope",
    ) -> None:
        if not issuer or not audience:
            raise ValueError("JwtPrincipalAuth requires both issuer and audience")
        algorithms = tuple(algorithms)
        if not algorithms or any(a.lower() == "none" for a in algorithms):
            raise ValueError("algorithms must be a non-empty allow-list and may not include 'none'")
        self._minter = minter
        self._keys = key_resolver
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._org_claim = org_claim
        self._subject_claim = subject_claim
        self._scope_claim = scope_claim

    def authenticate(self, headers: Mapping[str, str]) -> CallerPrincipalV1:
        import jwt

        token = self._bearer_token(headers)
        try:
            key = self._keys.signing_key_for(token)
        except Exception as exc:  # noqa: BLE001 -- never leak resolver internals to a caller
            raise Unauthorized("token signing key did not resolve") from exc

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self._algorithms),
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iss", "aud", self._subject_claim]},
            )
        except jwt.InvalidTokenError as exc:
            # The REASON stays in the log, never in the response: distinguishing
            # "expired" from "wrong audience" for an unauthenticated caller is a
            # free oracle for probing the boundary.
            raise Unauthorized("token failed verification") from exc

        org_id = claims.get(self._org_claim)
        subject_id = claims.get(self._subject_claim)
        if not org_id or not subject_id:
            raise Unauthorized(
                f"verified token is missing {self._org_claim!r} or {self._subject_claim!r}"
            )
        return self._minter.mint(
            org_id=str(org_id),
            subject_id=str(subject_id),
            scopes=self._scopes(claims),
        )

    def _scopes(self, claims: Mapping[str, object]) -> set[str]:
        raw = claims.get(self._scope_claim, "")
        if isinstance(raw, str):
            return {s.strip() for s in raw.replace(",", " ").split() if s.strip()}
        if isinstance(raw, (list, tuple, set)):
            return {str(s).strip() for s in raw if str(s).strip()}
        return set()

    @staticmethod
    def _bearer_token(headers: Mapping[str, str]) -> str:
        raw = headers.get("authorization") or headers.get("Authorization") or ""
        scheme, _, token = raw.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise Unauthorized("missing or malformed Authorization: Bearer header")
        return token.strip()
