"""Endpoint-level tests for GET /capabilities (Feature 1.3).

Every test builds a ``TestClient`` through ``client_scope`` so the store
dependency is overridden only for that test and removed on exit - the
overrides map never leaks between tests, so no live Neo4j instance is
required.
"""

from neo4j.exceptions import GqlError

from tests.capabilities_fakes import client_scope


class TestCapabilitiesContract:
    """The route must respond exactly as Feature 1.3 section 4 prescribes."""

    def test_returns_empty_list_when_no_rows(self) -> None:
        with client_scope([]) as client:
            response = client.get("/capabilities")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_capabilities_as_objects(self) -> None:
        rows = [
            {
                "id": "CAP-L1-CORP-001",
                "name": "Services Corporatifs",
                "description": "...",
            },
            {"id": "CAP-L2-ITG", "name": "IT Governance", "description": "..."},
        ]
        with client_scope(rows) as client:
            response = client.get("/capabilities")
        assert response.status_code == 200
        body = response.json()
        assert [c["id"] for c in body] == ["CAP-L1-CORP-001", "CAP-L2-ITG"]
        # Each item adheres strictly to the Feature 1.3 schema (no "level").
        for c in body:
            assert set(c.keys()) == {"id", "name", "description"}

    def test_database_failure_returns_500(self) -> None:
        with client_scope(run_error=GqlError("cannot reach Neo4j")) as client:
            response = client.get("/capabilities")
        assert response.status_code == 500
        assert "Neo4j" in response.json()["detail"]


class TestCapabilitiesOpenAPI:
    """The endpoint is discoverable in the OpenAPI schema (Feature 1.3 section 5.4)."""

    def test_declared_in_openapi_schema(self) -> None:
        with client_scope([]) as client:
            schema = client.get("/openapi.json")
        assert schema.status_code == 200
        assert "/capabilities" in schema.json()["paths"]
