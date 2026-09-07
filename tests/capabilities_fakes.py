from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, TypedDict

from fastapi.testclient import TestClient
from neo4j.exceptions import GqlError

from darkangel.api import app, get_capability_store
from darkangel.capabilities import (
    CAPABILITIES_QUERY,
    CREATE_CAPABILITY_QUERY,
    DELETE_CAPABILITY_QUERY,
    EXISTS_CAPABILITY_QUERY,
    GET_CAPABILITY_QUERY,
    HAS_CHILDREN_QUERY,
    UPDATE_CAPABILITY_QUERY,
    CapabilityStore,
)

__all__ = [
     "CapabilityRecord",
     "FakeGraph",
     "FakeNeo4jDriver",
     "FakeNeo4jResult",
     "FakeNeo4jSession",
     "client_graph_scope",
     "client_scope",
     "fake_graph_store",
     "fake_store",
]


class CapabilityRecord(TypedDict):
    id: str
    name: str
    description: str


class FakeNeo4jResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def data(self) -> list[dict[str, Any]]:
        return self._rows


class FakeGraph:
    def __init__(
        self,
        nodes: dict[str, dict[str, str]] | None = None,
        children: set[str] | None = None,
    ) -> None:
        self.nodes: dict[str, dict[str, str]] = dict(nodes) if nodes is not None else {}
        self.children: set[str] = set(children) if children is not None else set()

    def flat_rows(self) -> list[dict[str, Any]]:
        return [
            {"id": capability_id, "name": node["name"], "description": node["description"]}
            for capability_id, node in self.nodes.items()
        ]

    def get_row(self, capability_id: str) -> list[dict[str, Any]]:
        node = self.nodes.get(capability_id)
        if node is None:
            return []
        return [
            {
                "id": capability_id,
                "name": node["name"],
                "description": node["description"],
                "level": node["level"],
            }
        ]

    def exists_count(self, capability_id: str) -> int:
        return 1 if capability_id in self.nodes else 0

    def has_children_count(self, capability_id: str) -> int:
        return 1 if capability_id in self.children else 0

    def create(self, parameters: Mapping[str, Any]) -> None:
        capability_id = str(parameters["id"])
        self.nodes[capability_id] = {
            "name": str(parameters["name"]),
            "description": str(parameters["description"]),
            "level": str(parameters["level"]),
        }

    def update(self, parameters: Mapping[str, Any]) -> None:
        capability_id = str(parameters["id"])
        node = self.nodes.get(capability_id)
        if node is None:
            return
        node["name"] = str(parameters["name"])
        node["description"] = str(parameters["description"])

    def delete(self, capability_id: str) -> int:
        """Mirror the conditional Cypher delete: a parent is left untouched."""
        if capability_id not in self.nodes or capability_id in self.children:
            return 0
        self.nodes.pop(capability_id)
        return 1


class FakeNeo4jSession:
    def __init__(self, graph: FakeGraph, run_error: BaseException | None) -> None:
        self._graph = graph
        self._run_error = run_error
        self.query: str | None = None
        self.closed = False

    def run(self, query: str, **parameters: Any) -> FakeNeo4jResult:
        self.query = query
        if self._run_error is not None:
            raise self._run_error
        graph = self._graph
        if query == CAPABILITIES_QUERY:
            return FakeNeo4jResult(graph.flat_rows())
        if query == GET_CAPABILITY_QUERY:
            return FakeNeo4jResult(graph.get_row(str(parameters.get("id"))))
        if query == EXISTS_CAPABILITY_QUERY:
            return FakeNeo4jResult([{"count": graph.exists_count(str(parameters.get("id")))}])
        if query == CREATE_CAPABILITY_QUERY:
            graph.create(parameters)
            return FakeNeo4jResult([])
        if query == UPDATE_CAPABILITY_QUERY:
            graph.update(parameters)
            return FakeNeo4jResult([])
        if query == HAS_CHILDREN_QUERY:
            return FakeNeo4jResult([{"count": graph.has_children_count(str(parameters.get("id")))}])
        if query == DELETE_CAPABILITY_QUERY:
            return FakeNeo4jResult([{"deleted": graph.delete(str(parameters.get("id")))}])
        raise ValueError(f"Unknown capability query: {query}")

    def close(self) -> None:
        self.closed = True


class FakeNeo4jDriver:
    def __init__(self, graph: FakeGraph, run_error: BaseException | None = None) -> None:
        self.graph = graph
        self._run_error = run_error
        self.sessions: list[FakeNeo4jSession] = []

    def session(self) -> FakeNeo4jSession:
        session = FakeNeo4jSession(self.graph, self._run_error)
        self.sessions.append(session)
        return session


def _normalize(run_error: str | Exception | None) -> BaseException | None:
    if isinstance(run_error, str):
        return GqlError(run_error)
    return run_error


def _build_graph_from_rows(rows: list[dict[str, Any]] | None) -> FakeGraph:
    nodes: dict[str, dict[str, str]] = {}
    for row in rows or []:
        nodes[str(row["id"])] = {
            "name": str(row["name"]),
            "description": str(row.get("description", "")),
            "level": "1.0",
        }
    return FakeGraph(nodes)


def _build_graph_from_names(
    names: dict[str, str] | None,
    children: set[str] | None,
) -> FakeGraph:
    nodes: dict[str, dict[str, str]] = {}
    for capability_id, capability_name in (names or {}).items():
        nodes[capability_id] = {
            "name": capability_name,
            "description": "test",
            "level": "1.0",
        }
    return FakeGraph(nodes, children)


def fake_store(
    rows: list[dict[str, Any]] | None = None,
    run_error: str | Exception | None = None,
) -> CapabilityStore:
    return CapabilityStore(FakeNeo4jDriver(_build_graph_from_rows(rows), _normalize(run_error)))


@contextmanager
def client_scope(
    rows: list[dict[str, Any]] | None = None,
    run_error: str | Exception | None = None,
) -> Iterator[TestClient]:
    app.dependency_overrides[get_capability_store] = lambda: fake_store(rows, run_error)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_capability_store, None)


def fake_graph_store(
    nodes: dict[str, str] | None = None,
    children: set[str] | None = None,
    run_error: str | Exception | None = None,
) -> CapabilityStore:
    return CapabilityStore(
        FakeNeo4jDriver(_build_graph_from_names(nodes, children), _normalize(run_error))
    )


@contextmanager
def client_graph_scope(
    nodes: dict[str, str] | None = None,
    children: set[str] | None = None,
    run_error: str | Exception | None = None,
) -> Iterator[TestClient]:
    store = fake_graph_store(nodes, children, run_error)
    app.dependency_overrides[get_capability_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_capability_store, None)
