"""Every refusal path, not just the happy one.

A verifier suite that proves only "a valid token is accepted" proves almost
nothing: a function returning a principal unconditionally passes it. The
assertions that carry weight are the five refusals, and `alg=none` and
`unknown key` in particular are the two ways a verifier silently becomes a
rubber stamp.
"""

from __future__ import annotations

import datetime as dt

import pytest

jwt = pytest.importorskip("jwt", reason="PyJWT not installed")
pytest.importorskip("cryptography", reason="cryptography not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from foundry_contracts.errors import Unauthorized  # noqa: E402
from foundry_contracts.principal import PrincipalMinter  # noqa: E402

from foundry_service_template.adapters.real.jwt_principal_auth import (  # noqa: E402
    JwksKeyResolver,
    JwtPrincipalAuth,
)

ISSUER = "https://cognito-idp.eu-west-1.amazonaws.com/pool"
AUDIENCE = "llm-gateway"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_pem, key.public_key()


SIGNING_KEY, PUBLIC_KEY = _keypair()
OTHER_SIGNING_KEY, OTHER_PUBLIC_KEY = _keypair()


def _token(*, key=SIGNING_KEY, alg="RS256", **overrides) -> str:
    now = dt.datetime.now(tz=dt.UTC)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "custom:org_id": "org-abc",
        "scope": "widgets:read widgets:write",
        "exp": now + dt.timedelta(minutes=5),
        "iat": now,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm=alg)


def _auth(public_key=None) -> JwtPrincipalAuth:
    resolver = JwksKeyResolver(
        "https://example.invalid/jwks.json", fetch=lambda _token: public_key or PUBLIC_KEY
    )
    return JwtPrincipalAuth(
        PrincipalMinter(issuer="test"), resolver, issuer=ISSUER, audience=AUDIENCE
    )


def _headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# The direction that must SUCCEED. Without it, a verifier that refuses
# everything would pass every test below.
# --------------------------------------------------------------------------


def test_a_valid_token_mints_the_principal_from_claims() -> None:
    principal = _auth().authenticate(_headers(_token()))
    assert principal.org_id == "org-abc"
    assert principal.subject_id == "user-123"
    assert principal.scopes == {"widgets:read", "widgets:write"}


def test_scopes_accept_a_list_claim_as_well_as_a_space_delimited_string() -> None:
    principal = _auth().authenticate(_headers(_token(scope=["a:read", "b:write"])))
    assert principal.scopes == {"a:read", "b:write"}


# --------------------------------------------------------------------------
# The five refusals.
# --------------------------------------------------------------------------


def test_an_expired_token_is_refused() -> None:
    past = dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=1)
    with pytest.raises(Unauthorized):
        _auth().authenticate(_headers(_token(exp=past)))


def test_a_wrong_issuer_is_refused() -> None:
    with pytest.raises(Unauthorized):
        _auth().authenticate(_headers(_token(iss="https://evil.example/pool")))


def test_a_wrong_audience_is_refused() -> None:
    """Signature-valid but issued for someone else — the confused-deputy token."""
    with pytest.raises(Unauthorized):
        _auth().authenticate(_headers(_token(aud="some-other-service")))


def test_an_unsigned_alg_none_token_is_refused() -> None:
    """The single most important assertion in this file.

    A verifier that takes its algorithm from the token accepts a token the
    attacker wrote. PyJWT refuses to encode alg=none with a key, so the token is
    assembled the way an attacker would: header and payload, empty signature.
    """
    import base64
    import json

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    now = dt.datetime.now(tz=dt.UTC)
    payload = b64(
        json.dumps(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "attacker",
                "custom:org_id": "org-victim",
                "exp": int((now + dt.timedelta(minutes=5)).timestamp()),
            }
        ).encode()
    )
    forged = b".".join([header, payload, b""]).decode()

    with pytest.raises(Unauthorized):
        _auth().authenticate(_headers(forged))


def test_a_token_signed_by_an_unknown_key_is_refused() -> None:
    with pytest.raises(Unauthorized):
        _auth().authenticate(_headers(_token(key=OTHER_SIGNING_KEY)))


# --------------------------------------------------------------------------
# Construction-time refusals: the allow-list cannot be disarmed.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("algorithms", [(), ("none",), ("RS256", "none")])
def test_the_algorithm_allow_list_may_not_be_empty_or_contain_none(algorithms) -> None:
    resolver = JwksKeyResolver("https://example.invalid/jwks.json", fetch=lambda _t: PUBLIC_KEY)
    with pytest.raises(ValueError):
        JwtPrincipalAuth(
            PrincipalMinter(issuer="test"),
            resolver,
            issuer=ISSUER,
            audience=AUDIENCE,
            algorithms=algorithms,
        )


@pytest.mark.parametrize(("issuer", "audience"), [("", AUDIENCE), (ISSUER, "")])
def test_issuer_and_audience_are_both_required(issuer: str, audience: str) -> None:
    resolver = JwksKeyResolver("https://example.invalid/jwks.json", fetch=lambda _t: PUBLIC_KEY)
    with pytest.raises(ValueError):
        JwtPrincipalAuth(PrincipalMinter(issuer="test"), resolver, issuer=issuer, audience=audience)


# --------------------------------------------------------------------------
# Header handling and the offline-construction guarantee.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"authorization": ""}, {"authorization": "Basic abc"}, {"authorization": "Bearer "}],
)
def test_a_missing_or_malformed_authorization_header_is_refused(headers) -> None:
    with pytest.raises(Unauthorized):
        _auth().authenticate(headers)


def test_the_resolver_builds_without_touching_the_network() -> None:
    resolver = JwksKeyResolver("https://example.invalid/jwks.json")
    assert resolver._client is None


def test_the_refusal_message_does_not_say_WHY() -> None:
    """An unauthenticated caller must not be told expired-vs-wrong-audience."""
    past = dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=1)
    with pytest.raises(Unauthorized) as expired:
        _auth().authenticate(_headers(_token(exp=past)))
    with pytest.raises(Unauthorized) as wrong_aud:
        _auth().authenticate(_headers(_token(aud="other")))
    assert str(expired.value) == str(wrong_aud.value)
