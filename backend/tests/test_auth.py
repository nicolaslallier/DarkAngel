import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


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
