from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from neo4j.exceptions import GqlError

from darkangel.capabilities import (
    Capability,
    CapabilityStore,
    build_capability_store,
)

VERSION = "1.2.0"


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

    return app


app = create_app()
