import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from app.database.base import Base


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class ReadingSource(str, enum.Enum):
    MANUAL = "manual"
    YOUTUBE_API = "youtube_api"


class ReadingStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ViewReading(Base):
    __tablename__ = "view_readings"
    __table_args__ = (
        UniqueConstraint("publication_id", "idempotency_key", name="uq_view_reading_idempotency"),
        CheckConstraint("reported_value >= 0", name="ck_view_reading_reported_nonnegative"),
        CheckConstraint("accepted_value IS NULL OR accepted_value >= 0", name="ck_view_reading_accepted_nonnegative"),
        Index("ix_view_readings_period_status", "reporting_period", "status", "captured_at"),
        Index("ix_view_readings_publication_captured", "publication_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="RESTRICT"), nullable=False
    )
    reporting_period: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[ReadingSource] = mapped_column(
        Enum(ReadingSource, values_callable=enum_values, native_enum=False, length=20),
        nullable=False,
    )
    reported_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[ReadingStatus] = mapped_column(
        Enum(ReadingStatus, values_callable=enum_values, native_enum=False, length=20),
        nullable=False,
    )
    risk_flags: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    financial_locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    financial_locked_by_period_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calculation_periods.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ReadingDatasetRevision(Base):
    __tablename__ = "reading_dataset_revision"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_reading_dataset_revision_singleton"),
        CheckConstraint("revision >= 0", name="ck_reading_dataset_revision_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    closed_through_period: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ViewReadingHistory(Base):
    __tablename__ = "view_reading_history"
    __table_args__ = (Index("ix_view_reading_history_reading_created", "reading_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    reading_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("view_readings.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    old_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class YouTubeViewCollectionJob(Base):
    __tablename__ = "youtube_view_collection_jobs"
    __table_args__ = (
        UniqueConstraint("publication_id", "collection_date", name="uq_youtube_view_job_publication_date"),
        CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name="ck_youtube_view_collection_job_state",
        ),
        Index("ix_youtube_view_collection_jobs_due", "state", "available_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    collection_date: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
