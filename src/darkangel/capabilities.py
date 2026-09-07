"""Business capability access layer (Neo4j).

Cognitive Architecture Management System - Features 1.3 and 1.4.

This module is the *pure* data boundary for the ``:Capability`` graph. It keeps
all Neo4j interaction behind a small, fully typed surface so the graph logic can
be exercised in isolation of a live database:

- :func:`map_capability` turns one Cypher result row (a mapping with ``id``,
      ``name`` and ``description``) into a validated :class:`Capability`.
- :class:`CapabilityStore` runs the canonical query - a Neo4j ``GqlError``
      (driver/transaction error in the v6 hierarchy) bubbles up untouched so the
      API layer can translate it into a ``500``.
- :func:`build_capability_store` is the *only* place that touches the concrete
      ``neo4j`` driver; the store itself works against a structural ``Protocol``,
      so tests can supply a fake driver instead of a running graph.

The ``level`` property is deliberately ignored by :class:`Capability` (Feature
1.3 exposes a flat list, not the hierarchy), but the write layer of Feature 1.4
persists it on the node and treats ``id``/``level`` as structural anchors.

Feature 1.4 adds the write operations (Create / Update / Delete). Each is a
fixed, well-ordered sequence of Cypher statements so the graph logic stays
deterministic and testable against a fake driver:

- ``create`` pre-checks ``id`` uniqueness (BR-01) before inserting, backed by
      the ``:Capability(id)`` constraint of :meth:`CapabilityStore.ensure_schema`
      so the rule survives concurrent inserts.
- ``update`` only ``SET``s ``name``/``description`` (BR-03); ``id``/``level``
      are rejected upstream by the API layer (BR-02).
- ``delete`` refuses a node that still owns sub-capabilities (BR-04); the
      childless condition is part of the ``DETACH DELETE`` itself, so no link is
      ever silently cut (BR-05).

Every session is opened in a ``with`` block: ``neo4j`` returns the borrowed
connection to the pool on ``close()`` only.
"""

import os
from collections.abc import Mapping
from contextlib import closing
from typing import Any, Protocol

from neo4j import GraphDatabase
from neo4j.exceptions import ConstraintError
from pydantic import BaseModel, ConfigDict

# Feature 1.3: flat list of every :Capability node, in the requested shape only.
CAPABILITIES_QUERY = "MATCH (c:Capability) RETURN c.id AS id, c.name AS name, c.description AS description"

# Feature 1.4: single-node fetch, including the structural ``level`` anchor that
# Feature 1.3 deliberately hid from the flat response.
GET_CAPABILITY_QUERY = "MATCH (c:Capability {id: $id}) RETURN c.id AS id, c.name AS name, c.description AS description, c.level AS level"

# Feature 1.4 uniqueness pre-check (BR-01): a count query whose non-zero result
# means the ``id`` is already taken.
EXISTS_CAPABILITY_QUERY = "MATCH (c:Capability {id: $id}) RETURN count(c) AS count"

# Feature 1.4 insert.
CREATE_CAPABILITY_QUERY = "CREATE (c:Capability {id: $id, name: $name, description: $description, level: $level})"

# Feature 1.4 update (BR-03): touch only the editable properties.
UPDATE_CAPABILITY_QUERY = "MATCH (c:Capability {id: $id}) SET c.name = $name, c.description = $description"

# Feature 1.4 child check (BR-04): outgoing HAS_SUB_CAPABILITY links.
HAS_CHILDREN_QUERY = "MATCH (c:Capability {id: $id})-[:HAS_SUB_CAPABILITY]->() RETURN count(c) AS count"

# Feature 1.4 delete (BR-04 / BR-05): the childless condition lives *inside* the
# delete so the guard cannot go stale between the check and the write, and the
# returned counter tells the caller whether the node actually went away.
DELETE_CAPABILITY_QUERY = (
    "MATCH (c:Capability {id: $id}) "
    "WHERE NOT (c)-[:HAS_SUB_CAPABILITY]->() "
    "DETACH DELETE c "
    "RETURN count(*) AS deleted"
)

# Feature 1.4 schema (BR-01): the uniqueness guarantee the ``create`` pre-check
# cannot provide on its own. Idempotent, so it is safe to re-run at any time.
CAPABILITY_ID_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT capability_id_unique IF NOT EXISTS "
    "FOR (c:Capability) REQUIRE c.id IS UNIQUE"
)


class CapabilityError(Exception):
     """Base class for the domain-level failures of the graph write layer."""


class CapabilityNotFoundError(CapabilityError):
     """The requested ``id`` does not exist (Feature 1.4 -> ``404``)."""


class CapabilityConflictError(CapabilityError):
     """A ``id`` already exists, or the node still owns sub-capabilities
    (Feature 1.4 -> ``409``)."""


class CapabilityDuplicateIdError(CapabilityConflictError):
     """The requested ``id`` is already taken (Feature 1.4 BR-01 -> ``409``)."""


class CapabilityHasChildrenError(CapabilityConflictError):
     """The node still owns sub-capabilities (Feature 1.4 BR-04 -> ``409``)."""


class ImmutableFieldError(CapabilityError):
     """An attempt to mutate the structural ``id``/``level`` anchors
    (Feature 1.4 BR-02 -> ``400``)."""

# --- Environment configuration -----------------------------------------------
# No hard-coded database coordinate: the store location and credentials are read
# from the environment so the same code runs locally and in CI alike.
_ENV_URI = "NEO4J_URI"
_ENV_USER = "NEO4J_USER"
_ENV_PASSWORD = "NEO4J_PASSWORD"

_DEFAULT_URI = "bolt://localhost:7687"
_DEFAULT_USER = "neo4j"
_DEFAULT_PASSWORD = "password"


class Capability(BaseModel):
    """API response shape for a single business capability (Feature 1.3 section 3).

    Exactly three fields - ``id``, ``name`` and ``description``. ``extra`` is
    forbidden so the JSON contract is strict even if the graph ever carries
    additional properties.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str


class CapabilityWithLevel(Capability):
    """Full node shape including the structural ``level`` anchor.

    Used by the write layer (Feature 1.4) where ``level`` must round-trip,
    unlike the flat :class:`Capability` response of Feature 1.3.
    """

    level: str


class CapabilityCreate(BaseModel):
    """Create payload (Feature 1.4 section 3 / REQ-1.4.1, REQ-1.4.2).

    ``id`` and ``level`` are required structural anchors; ``extra`` is forbidden
    so the contract stays strict.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    level: str


class CapabilityUpdate(BaseModel):
    """Update payload (Feature 1.4 section 3 / BR-02, BR-03, REQ-1.4.3, REQ-1.4.4).

    Only the editable fields are accepted. ``extra = "forbid"`` turns any attempt
    to modify the immutable ``id``/``level`` anchors into a validation error,
    which the API layer translates into a ``400``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None


class _Result(Protocol):
    """Structural view of a Neo4j :class:`~neo4j.Result` (v6 ``.data()`` shape)."""

    def data(self) -> list[dict[str, Any]]: ...


class _Session(Protocol):
    """Structural view of a Neo4j session; ``run`` takes a query plus optional named parameters.

    ``close`` is part of the surface because ``neo4j`` returns the borrowed
    connection to the pool there and nowhere else (``__del__`` only warns), so
    every call site below wraps the session in :func:`contextlib.closing`.
    """

    def run(self, query: str, **parameters: Any) -> _Result: ...

    def close(self) -> None: ...


class _Driver(Protocol):
    """Structural view of a Neo4j driver - enough to open a session and run the
    query without binding the store to the concrete driver type.
    """

    def session(self) -> _Session: ...


def _require_property(row: Mapping[str, Any], key: str) -> str:
    """Read one node property as ``str``, refusing a Cypher ``null``.

    A property absent from the node comes back as ``None``; blindly casting it
    would hand the caller the literal string ``"None"`` as if it were real data
    (a node predating Feature 1.4 carries no ``level``, for instance). Raising
    instead surfaces the malformed row as a ``500``, consistent with
    :func:`map_capability`.
    """
    value = row[key]
    if value is None:
        raise ValueError(f"Capability property {key!r} is null on the node")
    return str(value)


def map_capability(record: Mapping[str, Any]) -> Capability:
    """Map one Cypher result row to a validated :class:`Capability`.

    The three properties are cast from JSON-typed ``Any`` to ``str`` (the graph
    stores them as strings), then validated by the Pydantic model. A malformed
    row - a missing key, or a value the model rejects - surfaces as a
    :class:`pydantic.ValidationError` (a ``ValueError`` subclass), which the API
    layer turns into a ``500``.
    """
    return Capability(
        id=str(record["id"]),
        name=str(record["name"]),
        description=str(record["description"]),
    )


class CapabilityStore:
    """Executes the canonical capability query and maps its rows.

    A database failure is *not* swallowed here: a ``neo4j.exceptions.GqlError``
    (connection down, auth failure, ...) propagates so the caller can translate
    it into a ``500 Internal Server Error`` with a clear message (Feature 1.3
    section 5.3).
    """

    def __init__(self, driver: _Driver) -> None:
        self._driver = driver

    def ensure_schema(self) -> None:
        """Install the ``:Capability(id)`` uniqueness constraint (BR-01).

        Idempotent. Without it the ``create`` pre-check is only advisory: two
        concurrent inserts can both pass it and leave duplicate nodes behind.
        """
        with closing(self._driver.session()) as session:
            session.run(CAPABILITY_ID_CONSTRAINT_QUERY)

    def all(self) -> list[Capability]:
        with closing(self._driver.session()) as session:
            result = session.run(CAPABILITIES_QUERY)
            return [map_capability(row) for row in result.data()]

    def get(self, capability_id: str) -> CapabilityWithLevel | None:
        """Fetch a single node by ``id`` (including ``level``), or ``None``.

        Feature 1.4 uses this to decide ``404`` vs. ``200``/``204``. A database
        failure propagates untouched.
        """
        with closing(self._driver.session()) as session:
            rows = session.run(GET_CAPABILITY_QUERY, id=capability_id).data()
        if not rows:
            return None
        row = rows[0]
        return CapabilityWithLevel(
            id=_require_property(row, "id"),
            name=_require_property(row, "name"),
            description=_require_property(row, "description"),
            level=_require_property(row, "level"),
        )

    def create(self, payload: CapabilityCreate) -> CapabilityWithLevel:
        """Insert a new ``:Capability`` node, pre-checking ``id`` uniqueness.

        BR-01: an already-taken ``id`` raises :class:`CapabilityDuplicateIdError`.
        The pre-check is the fast path; the ``:Capability(id)`` constraint
        installed by :meth:`ensure_schema` is what actually makes the rule hold
        when two inserts race, so its violation maps to the same error.
        BR-02: ``id``/``level`` come from the create payload only.
        """
        with closing(self._driver.session()) as session:
            present = bool(session.run(EXISTS_CAPABILITY_QUERY, id=payload.id).data()[0]["count"])
            if present:
                raise CapabilityDuplicateIdError(payload.id)
            try:
                # ``.data()`` drains the (empty) stream so a constraint violation
                # surfaces here rather than later, when the session closes and
                # the error would no longer be distinguishable from a ``500``.
                session.run(
                    CREATE_CAPABILITY_QUERY,
                    id=payload.id,
                    name=payload.name,
                    description=payload.description,
                    level=payload.level,
                ).data()
            except ConstraintError as exc:
                raise CapabilityDuplicateIdError(payload.id) from exc
        return CapabilityWithLevel(
            id=payload.id,
            name=payload.name,
            description=payload.description,
            level=payload.level,
        )

    def update(self, capability_id: str, payload: CapabilityUpdate) -> CapabilityWithLevel:
        """Replace ``name``/``description`` only (BR-03).

        A missing node raises :class:`CapabilityNotFoundError`. The structural
        anchors are never touched by this query.
        """
        with closing(self._driver.session()) as session:
            current = session.run(GET_CAPABILITY_QUERY, id=capability_id).data()
            if not current:
                raise CapabilityNotFoundError(capability_id)
            merged = dict(current[0])
            name = (
                payload.name if payload.name is not None else _require_property(merged, "name")
            )
            description = (
                payload.description
                if payload.description is not None
                else _require_property(merged, "description")
            )
            session.run(
                UPDATE_CAPABILITY_QUERY, id=capability_id, name=name, description=description
            )
        return CapabilityWithLevel(
            id=_require_property(merged, "id"),
            name=name,
            description=description,
            level=_require_property(merged, "level"),
        )

    def delete(self, capability_id: str) -> None:
        """Delete a node after verifying it owns no sub-capabilities.

        BR-04: a node with ``HAS_SUB_CAPABILITY`` links raises
        :class:`CapabilityHasChildrenError`. BR-05: the surviving delete detaches
        first so no link dangles.

        The pre-check exists to tell ``404`` from ``409``; the childless
        condition is repeated *inside* :data:`DELETE_CAPABILITY_QUERY` so a
        child attached in the meantime cannot be silently detached. A zero
        counter therefore means the node grew a child mid-flight.
        """
        with closing(self._driver.session()) as session:
            current = session.run(GET_CAPABILITY_QUERY, id=capability_id).data()
            if not current:
                raise CapabilityNotFoundError(capability_id)
            children = session.run(HAS_CHILDREN_QUERY, id=capability_id).data()
            has_children = bool(children and children[0]["count"])
            if has_children:
                raise CapabilityHasChildrenError(capability_id)
            deleted = session.run(DELETE_CAPABILITY_QUERY, id=capability_id).data()
        if not (deleted and deleted[0]["deleted"]):
            raise CapabilityHasChildrenError(capability_id)


def load_neo4j_settings() -> tuple[str, str, str]:
    """Read the driver credential triple ``(uri, user, password)`` from env.

    Each value falls back to a sane local default so the module imports without
    any configuration.
    """
    uri = os.environ.get(_ENV_URI, _DEFAULT_URI)
    user = os.environ.get(_ENV_USER, _DEFAULT_USER)
    password = os.environ.get(_ENV_PASSWORD, _DEFAULT_PASSWORD)
    return uri, user, password


# The uniqueness constraint only has to be installed once per process; the flag
# keeps the per-request dependency from paying for an extra round trip.
_schema_ready = False


def build_capability_store() -> CapabilityStore:
    """Construct a :class:`CapabilityStore` from a real Neo4j driver.

    The only entry point that touches the concrete ``neo4j`` driver. It is
    called from the default dependency, so the store is created lazily on first
    use and importing the module (and therefore running the test suite) never
    opens a database connection.
    """
    uri, user, password = load_neo4j_settings()
    driver = GraphDatabase.driver(uri, auth=(user, password))
    store = CapabilityStore(driver)
    global _schema_ready
    if not _schema_ready:
        store.ensure_schema()
        _schema_ready = True
    return store
