import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import auth

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ISSUER = "https://keycloak.famillelallier.net/realms/ea"


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    # Stands in for Keycloak's JWKS endpoint: every token verifies against KEY.
    signing_key = SimpleNamespace(key=KEY.public_key())
    fake = SimpleNamespace(get_signing_key_from_jwt=lambda _token: signing_key)
    monkeypatch.setattr(auth, "jwks_client", lambda: fake)


def token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "darkangel-api",
        "sub": "user-1",
        "iat": now,
        "exp": now + 300,
        "preferred_username": "nicolas",
        "realm_access": {"roles": ["ea-editor"]},
    } | overrides
    return jwt.encode(claims, KEY, algorithm="RS256")
