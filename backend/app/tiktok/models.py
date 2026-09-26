import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class TikTokEnrichmentJob(Base):
    __tablename__ = "tiktok_enrichment_jobs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name="ck_tiktok_enrichment_jobs_state",
        ),
        Index("ix_tiktok_enrichment_jobs_due", "state", "available_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TikTokViewCollectionJob(Base):
    __tablename__ = "tiktok_view_collection_jobs"
    __table_args__ = (
        UniqueConstraint(
            "publication_id", "collection_date", "collection_slot",
            name="uq_tiktok_view_job_publication_date_slot",
        ),
        CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name="ck_tiktok_view_collection_jobs_state",
        ),
        CheckConstraint(
            "collection_slot >= 0 AND collection_slot < 12",
            name="ck_tiktok_view_collection_jobs_slot",
        ),
        Index("ix_tiktok_view_collection_jobs_due", "state", "available_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="RESTRICT"), nullable=False
    )
    collection_date: Mapped[date] = mapped_column(Date, nullable=False)
    collection_slot: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
