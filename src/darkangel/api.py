from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from neo4j.exceptions import GqlError

from darkangel.capabilities import (
    Capability,
    CapabilityConflictError,
    CapabilityCreate,
    CapabilityDuplicateIdError,
    CapabilityNotFoundError,
    CapabilityStore,
    CapabilityUpdate,
    CapabilityWithLevel,
    build_capability_store,
)

# Feature 1.4 BR-02: the structural anchors a client may never send on an update.
_IMMUTABLE_FIELDS = frozenset({"id", "level"})

VERSION = "1.4.0"


def health_payload() -> dict[str, str]:
    return {"status": "ok"}


def hello_payload() -> dict[str, str]:
    return {"message": "Hello, World!"}


def get_capability_store() -> CapabilityStore:
    """Default dependency: a store bound to a real Neo4j driver.

    It is overridden in tests with a fake driver, so the default path is the
    only one that ever opens a database connection (lazily, on first use).
    """
    return build_capability_store()


def create_app() -> FastAPI:
    app = FastAPI(title="DarkAngel API", version=VERSION)

    @app.exception_handler(CapabilityConflictError)
    async def _conflict(request: Request, exc: CapabilityConflictError) -> JSONResponse:
        """Both 409 causes share this handler, so name the one that fired.

        BR-01 (duplicate ``id`` on create) and BR-04 (delete of a parent) are
        distinct failures; reporting the delete message for either one tells the
        caller the node has children when it does not.
        """
        capability_id = exc.args[0] if exc.args else ""
        if isinstance(exc, CapabilityDuplicateIdError):
            detail = f"Conflict: Capability with ID {capability_id} already exists."
        else:
            detail = "Conflict: Capability has children and cannot be deleted."
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": detail},
        )

    @app.exception_handler(CapabilityNotFoundError)
    async def _not_found(request: Request, exc: CapabilityNotFoundError) -> JSONResponse:
        capability_id = exc.args[0] if exc.args else ""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"Capability with ID {capability_id} not found."},
        )

    @app.exception_handler(RequestValidationError)
    async def _immutable(request: Request, exc: RequestValidationError) -> Response:
        """Answer ``400`` for BR-02 only, and leave every other 422 intact.

        ``CapabilityUpdate`` forbids extra fields, so an attempt to send the
        immutable ``id``/``level`` arrives as an ``extra_forbidden`` error. Any
        other validation failure - a missing field, a wrong type, a bad path
        parameter, on this route or any other - keeps FastAPI's ``422`` and its
        field-level detail, which is the only way a client can tell what to fix.
        """
        immutable_touched = any(
            error.get("type") == "extra_forbidden"
            and error.get("loc")
            and error["loc"][-1] in _IMMUTABLE_FIELDS
            for error in exc.errors()
        )
        if not immutable_touched:
            return await request_validation_exception_handler(request, exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid input: ID and Level are immutable."},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return health_payload()

    @app.get("/")
    def hello() -> dict[str, str]:
        return hello_payload()

    @app.get("/capabilities", response_model=list[Capability])
    def list_capabilities(
        store: Annotated[CapabilityStore, Depends(get_capability_store)],
    ) -> list[Capability]:
        """Flat list of every ``:Capability`` node (Feature 1.3).

        Returns an empty list when the graph holds no capability. A database
        connection failure is translated into a ``500`` with a clear message so
        the eventual MCP consumer never sees an opaque error.
        """
        try:
            return store.all()
        except GqlError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Neo4j unavailable: {exc}",
            ) from exc

    @app.post("/capabilities", response_model=CapabilityWithLevel, status_code=status.HTTP_201_CREATED)
    def create_capability(
        payload: CapabilityCreate,
        store: Annotated[CapabilityStore, Depends(get_capability_store)],
    ) -> CapabilityWithLevel:
        """Create a ``:Capability`` node (Feature 1.4, REQ-1.4.1 / REQ-1.4.2).

        A duplicate ``id`` yields a ``409``; a database failure yields a ``500``.
        """
        try:
            return store.create(payload)
        except GqlError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Neo4j unavailable: {exc}",
            ) from exc

    @app.put(
        "/capabilities/{capability_id}",
        response_model=CapabilityWithLevel,
        status_code=status.HTTP_200_OK,
    )
    def update_capability(
        capability_id: str,
        payload: CapabilityUpdate,
        store: Annotated[CapabilityStore, Depends(get_capability_store)],
    ) -> CapabilityWithLevel:
        """Update ``name``/``description`` of a capability (Feature 1.4, BR-03).

        An unknown ``id`` yields a ``404``; a database failure yields a ``500``.
        Mutating the immutable ``id``/``level`` is rejected as a ``400``.
        """
        try:
            return store.update(capability_id, payload)
        except GqlError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Neo4j unavailable: {exc}",
            ) from exc

    @app.delete(
        "/capabilities/{capability_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_capability(
        capability_id: str,
        store: Annotated[CapabilityStore, Depends(get_capability_store)],
    ) -> None:
        """Delete a capability (Feature 1.4, BR-04 / BR-05).

        A node that still owns sub-capabilities yields a ``409``; an unknown
        ``id`` yields a ``404``; a database failure yields a ``500``.
        """
        try:
            store.delete(capability_id)
        except GqlError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Neo4j unavailable: {exc}",
            ) from exc

    return app


app = create_app()
