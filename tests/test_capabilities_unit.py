"""Unit tests for the pure data layer of Feature 1.3 (no HTTP, no DB)."""

from typing import Any, cast

import pytest
from neo4j.exceptions import ClientError

from darkangel.capabilities import (
    CAPABILITIES_QUERY,
    Capability,
    map_capability,
)
from tests.capabilities_fakes import FakeNeo4jDriver, fake_store


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
