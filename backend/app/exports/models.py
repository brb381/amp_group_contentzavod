import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum as SqlEnum
from sqlalchemy import ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy import event, func, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.database.base import Base


def enum_values(enum_type: type[Enum]) -> list[str]:
    return [item.value for item in enum_type]


class ExportType(str, Enum):
    BLOGGERS = "bloggers"
    SOCIAL_ACCOUNTS = "social_accounts"
    PUBLICATIONS = "publications"
    VIEW_READINGS = "view_readings"
    MODERATION_HISTORY = "moderation_history"
    ACCRUALS = "accruals"
    PAYOUT_REGISTER = "payout_register"
    PAYOUT_HISTORY = "payout_history"
    SUPPORT_TICKETS = "support_tickets"
    AUDIT_LOG = "audit_log"


class ExportFormat(str, Enum):
    CSV = "csv"
    XLSX = "xlsx"


class ExportJobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class ExportJob(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (
        UniqueConstraint(
            "requested_by_user_id",
            "idempotency_key",
            name="uq_export_jobs_requester_idempotency",
        ),
        CheckConstraint("schema_version > 0", name="ck_export_jobs_schema_version"),
        CheckConstraint("attempt_count >= 0", name="ck_export_jobs_attempt_count"),
        CheckConstraint("row_count IS NULL OR row_count >= 0", name="ck_export_jobs_row_count"),
        CheckConstraint("file_size IS NULL OR file_size >= 0", name="ck_export_jobs_file_size"),
        Index("ix_export_jobs_dispatch", "status", "available_at", "created_at"),
        Index("ix_export_jobs_requester_created", "requested_by_user_id", "created_at"),
        Index("ix_export_jobs_expiry", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    export_type: Mapped[ExportType] = mapped_column(
        SqlEnum(ExportType, values_callable=enum_values, native_enum=False, length=32),
        nullable=False,
    )
    export_format: Mapped[ExportFormat] = mapped_column(
        SqlEnum(ExportFormat, values_callable=enum_values, native_enum=False, length=8),
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[ExportJobStatus] = mapped_column(
        SqlEnum(ExportJobStatus, values_callable=enum_values, native_enum=False, length=20),
        nullable=False,
        default=ExportJobStatus.PENDING,
    )
    dispatch_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    artifact_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    artifact_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


ARTIFACT_FACT_FIELDS = (
    "artifact_key", "filename", "content_type", "content_sha256", "file_size",
    "row_count", "data_as_of", "completed_at", "expires_at",
)


def _reject_artifact_fact_rewrite(
    mapper: Mapper, connection: object, target: ExportJob
) -> None:
    del mapper, connection
    state = inspect(target)
    for field_name in ARTIFACT_FACT_FIELDS:
        history = state.attrs[field_name].history
        if history.has_changes() and history.deleted and history.deleted[0] is not None:
            raise ValueError(f"Export artifact fact is immutable once recorded: {field_name}")


event.listen(ExportJob, "before_update", _reject_artifact_fact_rewrite)
