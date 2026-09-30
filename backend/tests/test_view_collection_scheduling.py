from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.scheduling.view_collection import expire_obsolete_view_jobs


class Base(DeclarativeBase):
    pass


class ViewJob(Base):
    __tablename__ = "test_view_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_date: Mapped[date] = mapped_column(Date, nullable=False)
    collection_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_id: Mapped[str | None] = mapped_column(String(36))


def test_expire_obsolete_view_jobs_keeps_only_current_slot_pending():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    with Session(engine) as db:
        previous = ViewJob(
            collection_date=date(2026, 10, 1), collection_slot=5, state="pending"
        )
        current = ViewJob(
            collection_date=date(2026, 10, 1), collection_slot=6, state="pending"
        )
        db.add_all((previous, current))
        db.flush()

        assert expire_obsolete_view_jobs(db, ViewJob, now) == 1
        assert previous.state == "failed"
        assert previous.last_error_code == "collection_slot_expired"
        assert current.state == "pending"
