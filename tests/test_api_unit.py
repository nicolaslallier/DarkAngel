from fastapi.testclient import TestClient

from darkangel.api import VERSION, app, create_app, health_payload, hello_payload


class TestPureFunctions:
    def test_health_payload_is_ok(self) -> None:
        assert health_payload() == {"status": "ok"}

    def test_hello_payload_message(self) -> None:
        assert hello_payload() == {"message": "Hello, World!"}

    def test_health_payload_immutable(self) -> None:
        first = health_payload()
        second = health_payload()
        assert first is not second
        assert first == second

    def test_hello_payload_immutable(self) -> None:
        first = hello_payload()
        second = hello_payload()
        assert first is not second
        assert first == second


class TestCreateApp:
    def test_returns_fastapi_instance(self) -> None:
        from fastapi import FastAPI

        new_app = create_app()
        assert isinstance(new_app, FastAPI)

    def test_title_and_version(self) -> None:
        new_app = create_app()
        assert new_app.title == "DarkAngel API"
        assert new_app.version == VERSION

    def test_module_level_app_is_configured(self) -> None:
        assert app.title == "DarkAngel API"
        assert app.version == VERSION

    def test_routes_registered(self) -> None:
        paths = {r.path for r in app.routes}
        assert "/" in paths
        assert "/health" in paths

    def test_openapi_schema_served(self) -> None:
        client = TestClient(app)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        assert "paths" in resp.json()
