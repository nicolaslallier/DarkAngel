from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import Db, db_session, engine


def test_engine_is_built_from_settings():
    url = engine().url
    assert url.drivername == "postgresql+psycopg"
    assert get_settings().database_url.endswith(f"/{url.database}")


def test_engine_is_cached():
    assert engine() is engine()


def test_db_session_yields_a_session_and_closes_it():
    # Nothing here touches the network: SQLAlchemy connects lazily, on the
    # first statement, and this test never issues one.
    generator = db_session()
    session = next(generator)

    assert isinstance(session, Session)
    assert session.is_active

    generator.close()


def test_db_is_an_annotated_dependency():
    assert Db.__metadata__  # Annotated[Session, Depends(db_session)]
