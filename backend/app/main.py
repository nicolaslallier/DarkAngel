import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.db import session_factory
from app.repositories.invoices import InvoiceRepository

log = logging.getLogger(__name__)


def reset_interrupted() -> None:
    """Background reads die with the process: whatever was queued or running at
    the last stop will never finish, so it must not sit in 'extracting' forever."""
    with session_factory()() as db:
        InvoiceRepository(db).reset_stuck()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        reset_interrupted()
    except Exception:
        # The database may simply not be up yet; the API must still start.
        log.exception("could not reset interrupted invoice reads")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name, version=settings.version, debug=settings.debug, lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    return app


app = create_app()
