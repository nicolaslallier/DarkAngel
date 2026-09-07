"""Endpoint-level tests for GET /capabilities (Feature 1.3).

Every test builds a ``TestClient`` through ``client_scope`` so the store
dependency is overridden only for that test and removed on exit - the
overrides map never leaks between tests, so no live Neo4j instance is
required.
"""

from neo4j.exceptions import GqlError

from tests.capabilities_fakes import client_graph_scope, client_scope


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


class TestCreateCapabilityEndpoint:
    """POST /capabilities (Feature 1.4, REQ-1.4.1 / REQ-1.4.2 / BR-01)."""

    def test_create_returns_201_with_echoed_fields(self) -> None:
        with client_graph_scope() as client:
            response = client.post(
                "/capabilities",
                json={"id": "NEW", "name": "n", "description": "d", "level": "1.0"},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "NEW"
        assert body["name"] == "n"
        assert body["description"] == "d"
        assert body["level"] == "1.0"

    def test_duplicate_id_returns_409(self) -> None:
        with client_graph_scope({"DUP": "existing"}) as client:
            response = client.post(
                "/capabilities",
                json={"id": "DUP", "name": "n", "description": "d", "level": "1.0"},
            )
        assert response.status_code == 409

    def test_database_failure_returns_500(self) -> None:
        with client_graph_scope(run_error="boom") as client:
            response = client.post(
                "/capabilities",
                json={"id": "NEW", "name": "n", "description": "d", "level": "1.0"},
            )
        assert response.status_code == 500
        assert "Neo4j" in response.json()["detail"]

    def test_missing_required_field_returns_400(self) -> None:
        with client_graph_scope() as client:
            response = client.post(
                "/capabilities",
                json={"id": "NEW", "name": "n", "description": "d"},
             )
        assert response.status_code == 400


class TestUpdateCapabilityEndpoint:
    """PUT /capabilities/{id} (Feature 1.4, BR-02 / BR-03)."""

    def test_update_name_returns_200(self) -> None:
        with client_graph_scope({"A": "old"}) as client:
            response = client.put("/capabilities/A", json={"name": "new"})
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "new"
        assert set(body.keys()) == {"id", "name", "description", "level"}

    def test_update_description_keeps_name(self) -> None:
        with client_graph_scope({"A": "keep"}) as client:
            response = client.put("/capabilities/A", json={"description": "desc"})
        assert response.status_code == 200
        body = response.json()
        assert body["description"] == "desc"
        assert body["name"] == "keep"

    def test_immutable_field_returns_400(self) -> None:
        with client_graph_scope({"A": "old"}) as client:
            response = client.put("/capabilities/A", json={"name": "new", "level": "9.9"})
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid input: ID and Level are immutable."

    def test_missing_id_returns_404(self) -> None:
        with client_graph_scope({}) as client:
            response = client.put("/capabilities/MISSING", json={"name": "x"})
        assert response.status_code == 404

    def test_database_failure_returns_500(self) -> None:
        with client_graph_scope({"A": "old"}, run_error="boom") as client:
            response = client.put("/capabilities/A", json={"name": "z"})
        assert response.status_code == 500
        assert "Neo4j" in response.json()["detail"]


class TestDeleteCapabilityEndpoint:
    """DELETE /capabilities/{id} (Feature 1.4, BR-04 / BR-05)."""

    def test_delete_leaf_returns_204_empty_body(self) -> None:
        with client_graph_scope({"L": "leaf"}) as client:
            response = client.delete("/capabilities/L")
        assert response.status_code == 204
        assert response.content == b""

    def test_delete_parent_returns_409(self) -> None:
        with client_graph_scope({"P": "p"}, children={"P"}) as client:
            response = client.delete("/capabilities/P")
        assert response.status_code == 409

    def test_missing_id_returns_404(self) -> None:
        with client_graph_scope({}) as client:
            response = client.delete("/capabilities/MISSING")
        assert response.status_code == 404

    def test_database_failure_returns_500(self) -> None:
        with client_graph_scope({"L": "leaf"}, run_error="boom") as client:
            response = client.delete("/capabilities/L")
        assert response.status_code == 500
        assert "Neo4j" in response.json()["detail"]
