"""Unit tests for the pure data layer of Feature 1.3 (no HTTP, no DB)."""

from typing import Any, cast

import pytest
from neo4j.exceptions import ClientError, GqlError

from darkangel.capabilities import (
    CAPABILITIES_QUERY,
    Capability,
    CapabilityConflictError,
    CapabilityCreate,
    CapabilityNotFoundError,
    CapabilityUpdate,
    CapabilityWithLevel,
    map_capability,
)
from tests.capabilities_fakes import FakeNeo4jDriver, fake_graph_store, fake_store


class TestMapCapability:
    def test_single_capability(self) -> None:
        row: dict[str, Any] = {
            "id": "CAP-L1-CORP-001",
            "name": "Services Corporatifs",
            "description": "Fonctionnement.",
        }
        capability = map_capability(row)
        assert isinstance(capability, Capability)
        assert capability.id == "CAP-L1-CORP-001"
        assert capability.name == "Services Corporatifs"
        assert capability.description == "Fonctionnement."

    def test_level_is_dropped(self) -> None:
        # Feature 1.3: flat response; `level` must not appear in the output.
        row: dict[str, Any] = {
            "id": "T1",
            "name": "n",
            "description": "d",
            "level": "L1",
        }
        capability = map_capability(row)
        assert set(capability.model_dump().keys()) == {"id", "name", "description"}

    def test_missing_key_raises(self) -> None:
        with pytest.raises(KeyError):
            map_capability({"name": "x", "description": "y"})


class TestCapabilityStore:
    def test_all_maps_multiple_rows(self) -> None:
        rows: list[dict[str, Any]] = [
            {"id": "id1", "name": "n1", "description": "d1"},
            {"id": "id2", "name": "n2", "description": "d2"},
        ]
        store = fake_store(rows=rows)
        capabilities = store.all()
        assert len(capabilities) == 2
        assert all(isinstance(c, Capability) for c in capabilities)
        assert [c.id for c in capabilities] == ["id1", "id2"]
        # Query matches the Feature 1.3 canonical Cypher (no `level`).
        driver = cast(FakeNeo4jDriver, store._driver)
        session = driver.sessions[0]
        assert session.query == CAPABILITIES_QUERY

    def test_db_failure_propagates_as_neo4jerror(self) -> None:
        with pytest.raises(ClientError):
            fake_store(run_error=ClientError("connection refused")).all()

    def test_all_handles_empty_rows(self) -> None:
        store = fake_store([])
        assert store.all() == []


class TestCreateCapability:
    def test_create_succeeds_and_persists_node(self) -> None:
        store = fake_graph_store()
        result = store.create(CapabilityCreate(id="X", name="n", description="d", level="1.0"))
        assert isinstance(result, CapabilityWithLevel)
        assert result.id == "X"
        assert result.name == "n"
        assert result.description == "d"
        assert result.level == "1.0"
        fetched = store.get("X")
        assert fetched is not None
        assert fetched.id == "X"
        assert fetched.name == "n"
        assert fetched.description == "d"
        assert fetched.level == "1.0"

    def test_duplicate_id_raises_conflict(self) -> None:
        store = fake_graph_store({"DUP": "existing"})
        with pytest.raises(CapabilityConflictError):
            store.create(CapabilityCreate(id="DUP", name="new", description="d", level="1.0"))


class TestUpdateCapability:
    def test_update_by_name(self) -> None:
        store = fake_graph_store({"A": "old"})
        result = store.update("A", CapabilityUpdate(name="new"))
        assert result.name == "new"

    def test_update_by_description(self) -> None:
        store = fake_graph_store({"A": "keep"})
        result = store.update("A", CapabilityUpdate(description="desc"))
        assert result.description == "desc"
        assert result.name == "keep"

    def test_partial_merge_keeps_untouched_field(self) -> None:
        store = fake_graph_store({"A": "old"})
        result = store.update("A", CapabilityUpdate(name="new"))
        assert result.name == "new"
        assert result.description == "test"

    def test_missing_id_raises_not_found(self) -> None:
        store = fake_graph_store({})
        with pytest.raises(CapabilityNotFoundError):
            store.update("NOPE", CapabilityUpdate(name="x"))


class TestDeleteCapability:
    def test_leaf_success(self) -> None:
        store = fake_graph_store({"L": "leaf"})
        assert store.delete("L") is None
        assert store.get("L") is None

    def test_parent_refused(self) -> None:
        store = fake_graph_store({"P": "p"}, children={"P"})
        with pytest.raises(CapabilityConflictError):
            store.delete("P")

    def test_missing_id_raises_not_found(self) -> None:
        store = fake_graph_store({})
        with pytest.raises(CapabilityNotFoundError):
            store.delete("MISSING")


class TestRunErrorPropagation:
    def test_create_propagates_gql_error(self) -> None:
        store = fake_graph_store(run_error="boom")
        with pytest.raises(GqlError):
            store.create(CapabilityCreate(id="X", name="n", description="d", level="1.0"))

    def test_update_propagates_gql_error(self) -> None:
        store = fake_graph_store({"A": "old"}, run_error="boom")
        with pytest.raises(GqlError):
            store.update("A", CapabilityUpdate(name="new"))

    def test_delete_propagates_gql_error(self) -> None:
        store = fake_graph_store({"L": "leaf"}, run_error="boom")
        with pytest.raises(GqlError):
            store.delete("L")

    def test_get_propagates_gql_error(self) -> None:
        store = fake_graph_store({"A": "old"}, run_error="boom")
        with pytest.raises(GqlError):
            store.get("A")

    def test_all_propagates_gql_error(self) -> None:
        store = fake_graph_store(run_error="boom")
        with pytest.raises(GqlError):
            store.all()
