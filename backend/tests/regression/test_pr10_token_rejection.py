"""Regression — PR #10, Keycloak authentication against realm `ea`.

Every one of these token shapes must come back 401 with a `WWW-Authenticate`
header, and `/api/health` must stay reachable without a token at all, because
the container healthcheck calls it.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


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
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    response = client.get("/api/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_health_stays_public():
    assert client.get("/api/health").status_code == 200
