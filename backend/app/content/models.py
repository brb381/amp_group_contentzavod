import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog.models import Brand, enum_values
from app.database.base import Base
from app.platforms import PLATFORM_CHECK_SQL, Platform


class PublicationStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    CHANGES_REQUIRED = "changes_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    INACTIVE = "inactive"
    RE_REVIEW_REQUIRED = "re_review_required"


class PublicationParseStatus(str, enum.Enum):
    PARSED = "parsed"
    PENDING = "pending"
    MANUAL_REVIEW = "manual_review"


class PublicationAvailability(str, enum.Enum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class PublicationEnrichmentStatus(str, enum.Enum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"


class VideoCard(Base):
    __tablename__ = "video_cards"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="ck_video_cards_title_not_blank"),
        CheckConstraint(
            "(reported_brand IS NULL AND reported_product_name IS NULL) OR "
            "(reported_brand IS NOT NULL AND reported_product_name IS NOT NULL)",
            name="ck_video_cards_reported_product_complete",
        ),
        CheckConstraint(
            "product_id IS NOT NULL OR "
            "(reported_product_name IS NOT NULL AND length(trim(reported_product_name)) > 0)",
            name="ck_video_cards_product_required",
        ),
        CheckConstraint(
            "(product_id IS NULL AND product_snapshot IS NULL) OR "
            "(product_id IS NOT NULL AND product_snapshot IS NOT NULL)",
            name="ck_video_cards_snapshot_matches_product",
        ),
        Index("ix_video_cards_blogger_created", "blogger_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    reported_brand: Mapped[Brand | None] = mapped_column(
        Enum(Brand, values_callable=enum_values, name="productbrand"), nullable=True
    )
    reported_product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("normalized_url", name="uq_publications_normalized_url"),
        UniqueConstraint("platform", "external_id", name="uq_publications_platform_external_id"),
        CheckConstraint(PLATFORM_CHECK_SQL, name="ck_publications_supported_platform"),
        Index("ix_publications_card_created", "video_card_id", "created_at", "id"),
        Index("ix_publications_status_submitted", "status", "submitted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video_cards.id", ondelete="RESTRICT"), nullable=False
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("social_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(
            Platform,
            values_callable=enum_values,
            name="platformvalue",
            native_enum=False,
            length=50,
        ),
        nullable=False,
    )
    submitted_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[PublicationStatus] = mapped_column(
        Enum(PublicationStatus, values_callable=enum_values, name="publicationstatus"),
        nullable=False,
        default=PublicationStatus.DRAFT,
    )
    parse_status: Mapped[PublicationParseStatus] = mapped_column(
        Enum(PublicationParseStatus, values_callable=enum_values, name="publicationparsestatus"),
        nullable=False,
    )
    availability: Mapped[PublicationAvailability] = mapped_column(
        Enum(PublicationAvailability, values_callable=enum_values, name="publicationavailability"),
        nullable=False,
        default=PublicationAvailability.UNKNOWN,
    )
    enrichment_status: Mapped[PublicationEnrichmentStatus] = mapped_column(
        Enum(
            PublicationEnrichmentStatus,
            values_callable=enum_values,
            name="publicationenrichmentstatus",
        ),
        nullable=False,
        default=PublicationEnrichmentStatus.NOT_REQUESTED,
    )
    external_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_author_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_author_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_duration_seconds: Mapped[int | None] = mapped_column(nullable=True)
    external_thumbnail_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    external_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    moderation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PublicationHistory(Base):
    __tablename__ = "publication_history"
    __table_args__ = (
        Index("ix_publication_history_publication_created", "publication_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    pii_anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
