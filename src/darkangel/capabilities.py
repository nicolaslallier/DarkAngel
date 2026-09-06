"""Business capability access layer (Neo4j).

Cognitive Architecture Management System — Feature 1.3.

This module is the *pure* data boundary for the ``:Capability`` graph. It keeps
all Neo4j interaction behind a small, fully typed surface so the graph logic can
be exercised in isolation of a live database:

- :func:`map_capability` turns one Cypher result row (a mapping with ``id``,
  ``name`` and ``description``) into a validated :class:`Capability`.
- :class:`CapabilityStore` runs the canonical query (a ``Neo4jError`` bubbles up
  untouched so the API layer can translate it into a ``500``).
- :func:`build_capability_store` is the *only* place that touches the concrete
  ``neo4j`` driver; the store itself works against a structural ``Protocol``, so
  tests can supply a fake driver instead of a running graph.

The ``level`` property is deliberately ignored here — Feature 1.3 exposes a flat
list of capabilities, not the hierarchy.
"""

import os
from collections.abc import Mapping
from typing import Any, Protocol

from neo4j import GraphDatabase
from pydantic import BaseModel, ConfigDict

# Feature 1.3: flat list of every :Capability node, in the requested shape only.
CAPABILITIES_QUERY = (
    "MATCH (c:Capability) "
    "RETURN c.id AS id, c.name AS name, c.description AS description"
)

# --- Environment configuration ------------------------------------------------
# No hard-coded database coordinate: the store location and credentials are read
# from the environment so the same code runs locally and in CI alike.
_ENV_URI = "NEO4J_URI"
_ENV_USER = "NEO4J_USER"
_ENV_PASSWORD = "NEO4J_PASSWORD"

_DEFAULT_URI = "bolt://localhost:7687"
_DEFAULT_USER = "neo4j"
_DEFAULT_PASSWORD = "password"


class Capability(BaseModel):
    """API response shape for a single business capability (Feature 1.3 §3).

    Exactly three fields — ``id``, ``name`` and ``description``. ``extra`` is
    forbidden so the JSON contract is strict even if the graph ever carries
    additional properties.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str


class _Result(Protocol):
    """Structural view of a Neo4j :class:`~neo4j.Result` (v6 ``.data()`` shape)."""

    def data(self) -> list[dict[str, Any]]: ...


class _Session(Protocol):
    """Structural view of a Neo4j session used to run the capability query."""

    def run(self, query: str) -> _Result: ...


class _Driver(Protocol):
    """Structural view of a Neo4j driver — enough to open a session and run the
    query, without binding the store to the concrete driver type."""

    def session(self) -> _Session: ...


def map_capability(record: Mapping[str, Any]) -> Capability:
    """Map one Cypher result row to a validated :class:`Capability`.

    The three properties are cast from JSON-typed ``Any`` to ``str`` (the graph
    stores them as strings), then validated by the Pydantic model. A malformed
    row — missing key, or a non-string value — surfaces as a
    :class:`pydantic.ValidationError` (a ``ValueError`` subclass), which the
    API layer turns into a ``500``.
    """
    return Capability(
        id=str(record["id"]),
        name=str(record["name"]),
        description=str(record["description"]),
    )


class CapabilityStore:
    """Executes the canonical capability query and maps its rows.

    A database failure is *not* swallowed here: a :class:`neo4j.Neo4jError`
    (connection down, auth failure, …) propagates so the caller can translate it
    into a ``500 Internal Server Error`` with a clear message (Feature 1.3 §5.3).
    """

    def __init__(self, driver: _Driver) -> None:
        self._driver = driver

    def all(self) -> list[Capability]:
        session = self._driver.session()
        result = session.run(CAPABILITIES_QUERY)
        return [map_capability(row) for row in result.data()]


def load_neo4j_settings() -> tuple[str, str, str]:
    """Read the driver credential triple ``(uri, user, password)`` from env.

    Each value falls back to a sane local default so the module imports without
    any configuration.
    """
    uri = os.environ.get(_ENV_URI, _DEFAULT_URI)
    user = os.environ.get(_ENV_USER, _DEFAULT_USER)
    password = os.environ.get(_ENV_PASSWORD, _DEFAULT_PASSWORD)
    return uri, user, password


def build_capability_store() -> CapabilityStore:
    """Construct a :class:`CapabilityStore` from a real Neo4j driver.

    The only entry point that touches the concrete ``neo4j`` driver. It is called
    from the FastAPI lifespan, so importing the module (and therefore running the
    test suite) never opens a database connection.
    """
    uri, user, password = load_neo4j_settings()
    driver = GraphDatabase.driver(uri, auth=(user, password))
    return CapabilityStore(driver)
