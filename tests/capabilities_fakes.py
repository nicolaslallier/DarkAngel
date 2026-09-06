"""Fixtures and fakes for the capabilities end-to-end tests.

Kept in a dedicated module so test files can import them cleanly (mypy does
not resolve a sibling ``conftest`` import reliably). The fake driver stands in
for a live Neo4j instance: no port, no container, no network call.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

from darkangel.api import app, get_capability_store
from darkangel.capabilities import CapabilityStore


class FakeNeo4jResult:
    """Mimics the v6 ``.data()`` result shape: a list of row mappings."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def data(self) -> list[dict[str, Any]]:
        return self._rows


class FakeNeo4jSession:
    """Captures the query it is asked to run and feeds back canned rows."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        run_error: Exception | None,
    ) -> None:
        self._rows = rows
        self._run_error = run_error
        self.query: str | None = None

    def run(self, query: str) -> FakeNeo4jResult:
        self.query = query
        if self._run_error is not None:
            raise self._run_error
        return FakeNeo4jResult(self._rows)


class FakeNeo4jDriver:
    """Fake of the ``neo4j.Driver`` surface consumed by the store.

    ``rows`` are returned by every session; ``run_error`` (if set) is raised
    inside ``session().run()`` so it bubbles up as a ``GqlError`` to
    ``CapabilityStore.all`` and, through it, to the route's ``except``.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        run_error: Exception | None = None,
    ) -> None:
        self._rows = list(rows) if rows is not None else []
        self._run_error = run_error
        self.sessions: list[FakeNeo4jSession] = []

    def session(self) -> FakeNeo4jSession:
        session = FakeNeo4jSession(self._rows, self._run_error)
        self.sessions.append(session)
        return session


def fake_store(
    rows: list[dict[str, Any]] | None = None,
    run_error: Exception | None = None,
) -> CapabilityStore:
    """A :class:`CapabilityStore` backed by a fake driver - no live DB needed."""
    return CapabilityStore(FakeNeo4jDriver(rows, run_error))


@contextmanager
def client_scope(
    rows: list[dict[str, Any]] | None = None,
    run_error: Exception | None = None,
) -> Iterator[TestClient]:
    """Yield a ``TestClient`` with the capabilities dependency overridden.

    The override is registered before the client is built and removed on exit,
    so no residue leaks into later tests - each ``client_scope`` is hermetic.
    """
    app.dependency_overrides[get_capability_store] = lambda: fake_store(rows, run_error)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_capability_store, None)
