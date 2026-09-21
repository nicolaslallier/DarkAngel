from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def engine() -> Engine:
    """One engine per process. `create_engine` opens no connection: the pool
    fills lazily on the first statement, so importing this module is free."""
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


@lru_cache
def session_factory() -> sessionmaker[Session]:
    # expire_on_commit=False: a route builds its response model from the ORM
    # object *after* the repository commits, and the default would make every
    # attribute access re-query a closed session.
    return sessionmaker(bind=engine(), expire_on_commit=False)


def db_session() -> Iterator[Session]:
    """Request-scoped session. Sync on purpose, like every other dependency
    here: FastAPI runs it in the threadpool, so the blocking driver never
    stalls the event loop."""
    with session_factory()() as session:
        yield session


Db = Annotated[Session, Depends(db_session)]
