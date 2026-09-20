"""Regression — PR #10, Keycloak authentication against realm `ea`.

Every one of these token shapes must come back 401 with a `WWW-Authenticate`
header, and `/api/health` must stay reachable without a token at all, because
the container healthcheck calls it.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import ISSUER, KEY, token

client = TestClient(app)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def hs256_signed_with_rsa_public_key() -> str:
    """Algorithm confusion: sign with HS256, using the RSA public key's PEM
    bytes as the HMAC secret. `auth.py` pins `algorithms=["RS256"]`, so this
    is rejected only because HS256 is never an accepted algorithm — widening
    that list to include it would let this token verify, since the public
    key is not secret.

    Built by hand rather than with `jwt.encode(..., algorithm="HS256")`:
    PyJWT's encoder itself refuses to use a key that looks like an RSA/PEM
    key as an HMAC secret, which the *verifying* side (`jwt.decode`) does not
    guard against — the actual point of this test.
    """
    public_pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": ISSUER,
        "aud": "darkangel-api",
        "sub": "user-1",
        "iat": now,
        "exp": now + 300,
    }
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    signature = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


@pytest.mark.parametrize(
    "bearer",
    [
        None,
        token(aud="ea-api"),
        token(iss="https://evil.example/realms/ea"),
        token(exp=int(time.time()) - 3600),
        "not-a-jwt",
        hs256_signed_with_rsa_public_key(),
    ],
    ids=["missing", "wrong-audience", "wrong-issuer", "expired", "garbage", "algorithm-confusion"],
)
def test_me_rejects_bad_tokens(bearer):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    response = client.get("/api/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_health_stays_public():
    assert client.get("/api/health").status_code == 200
