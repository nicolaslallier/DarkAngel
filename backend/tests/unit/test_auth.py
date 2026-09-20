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
