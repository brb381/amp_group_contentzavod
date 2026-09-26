import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.integrations.models import ExternalProviderState, ExternalQuotaUsage


class YouTubeEnrichmentJob(Base):
    __tablename__ = "youtube_enrichment_jobs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name="ck_youtube_enrichment_jobs_state",
        ),
        Index("ix_youtube_enrichment_jobs_due", "state", "available_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
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


__all__ = ("ExternalProviderState", "ExternalQuotaUsage", "YouTubeEnrichmentJob")
