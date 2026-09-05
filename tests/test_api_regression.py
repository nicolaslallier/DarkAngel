from fastapi.testclient import TestClient

from darkangel.api import app

client = TestClient(app)


class TestRegression:
    def test_root_contract_unchanged(self) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == {"message": "Hello, World!"}
        assert "message" in resp.text
        assert "Hello, World!" in resp.text

    def test_health_contract_unchanged(self) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == {"status": "ok"}

    def test_root_and_health_shape_stable(self) -> None:
        root = client.get("/").json()
        health = client.get("/health").json()
        assert set(root.keys()) == {"message"}
        assert set(health.keys()) == {"status"}

    def test_unknown_route_is_404(self) -> None:
        resp = client.get("/nope")
        assert resp.status_code == 404

