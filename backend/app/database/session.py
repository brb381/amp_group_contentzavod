from collections.abc import Generator

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database.factory import create_session_factory as _create_session_factory


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return _create_session_factory(database_url or get_settings().database_url)


SessionLocal = create_session_factory()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
