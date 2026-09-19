import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.core import auth
from app.main import app

client = TestClient(app)
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


def get_me(bearer: str | None = None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return client.get("/api/me", headers=headers)


def test_me_returns_the_caller():
    response = get_me(token())

    assert response.status_code == 200
    assert response.json() == {
        "sub": "user-1",
        "username": "nicolas",
        "email": None,
        "roles": ["ea-editor"],
    }


@pytest.mark.parametrize(
    "bearer",
    [
        None,
        token(aud="ea-api"),
        token(iss="https://evil.example/realms/ea"),
        token(exp=int(time.time()) - 3600),
        "not-a-jwt",
    ],
    ids=["missing", "wrong-audience", "wrong-issuer", "expired", "garbage"],
)
def test_me_rejects_bad_tokens(bearer):
    response = get_me(bearer)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_health_stays_public():
    assert client.get("/api/health").status_code == 200
