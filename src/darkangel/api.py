from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from neo4j.exceptions import GqlError

from darkangel.capabilities import (
    Capability,
    CapabilityConflictError,
    CapabilityCreate,
    CapabilityNotFoundError,
    CapabilityStore,
    CapabilityUpdate,
    CapabilityWithLevel,
    build_capability_store,
)

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
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Conflict: Capability has children and cannot be deleted."},
        )

    @app.exception_handler(CapabilityNotFoundError)
    async def _not_found(request: Request, exc: CapabilityNotFoundError) -> JSONResponse:
        capability_id = exc.args[0] if exc.args else ""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"Capability with ID {capability_id} not found."},
        )

    @app.exception_handler(RequestValidationError)
    async def _immutable(request: Request, exc: RequestValidationError) -> JSONResponse:
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
