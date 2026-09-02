from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        hide_parameters=True,
    )
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
