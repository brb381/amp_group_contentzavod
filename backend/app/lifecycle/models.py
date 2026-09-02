import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class ActivityKind(str, enum.Enum):
    ACCOUNT_CREATED = "account_created"
    LOGIN = "login"
    PUBLICATION_CHANGED = "publication_changed"
    READING_SUBMITTED = "reading_submitted"
    PAYOUT_REQUESTED = "payout_requested"
    ACCOUNT_RESTORED = "account_restored"


class LifecycleAction(str, enum.Enum):
    WARN_SUSPENSION_30 = "warn_suspension_30"
    WARN_SUSPENSION_7 = "warn_suspension_7"
    SUSPEND = "suspend"
    WARN_BLOCK_30 = "warn_block_30"
    WARN_BLOCK_7 = "warn_block_7"
    BLOCK = "block"


class LifecycleJobState(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    OBSOLETE = "obsolete"
    FAILED = "failed"


class CreatorLifecycle(Base):
    __tablename__ = "creator_lifecycles"
    __table_args__ = (
        CheckConstraint(
            "last_activity_kind IN ('account_created', 'login', 'publication_changed', 'reading_submitted', "
            "'payout_requested', 'account_restored')",
            name="ck_creator_lifecycle_activity_kind",
        ),
        CheckConstraint(
            "activity_revision > 0", name="ck_creator_lifecycle_revision_positive"
        ),
        CheckConstraint(
            "(suspended_at IS NULL) OR suspended_at >= last_activity_at",
            name="ck_creator_lifecycle_suspension_order",
        ),
        CheckConstraint(
            "(blocked_at IS NULL) OR (suspended_at IS NOT NULL AND blocked_at >= suspended_at)",
            name="ck_creator_lifecycle_block_order",
        ),
        Index("ix_creator_lifecycle_activity_due", "last_activity_at", "suspended_at"),
        Index("ix_creator_lifecycle_block_due", "suspended_at", "blocked_at"),
        Index("ix_creator_lifecycle_scan_due", "scheduler_scanned_at", "blogger_id"),
    )

    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_kind: Mapped[ActivityKind] = mapped_column(
        Enum(ActivityKind, values_callable=enum_values, native_enum=False, length=32),
        nullable=False,
    )
    activity_revision: Mapped[int] = mapped_column(nullable=False, default=1)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    balance_claim_expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduler_scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LifecycleJob(Base):
    __tablename__ = "lifecycle_jobs"
    __table_args__ = (
        UniqueConstraint(
            "blogger_id", "action", "basis_revision", name="uq_lifecycle_job_basis"
        ),
        UniqueConstraint("dispatch_id", name="uq_lifecycle_jobs_dispatch_id"),
        CheckConstraint(
            "action IN ('warn_suspension_30', 'warn_suspension_7', 'suspend', "
            "'warn_block_30', 'warn_block_7', 'block')",
            name="ck_lifecycle_job_action",
        ),
        CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', "
            "'obsolete', 'failed')",
            name="ck_lifecycle_job_state",
        ),
        CheckConstraint("basis_revision > 0", name="ck_lifecycle_job_revision_positive"),
        CheckConstraint("attempt_count >= 0", name="ck_lifecycle_job_attempt_nonnegative"),
        Index("ix_lifecycle_jobs_due", "state", "available_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[LifecycleAction] = mapped_column(
        Enum(LifecycleAction, values_callable=enum_values, native_enum=False, length=32),
        nullable=False,
    )
    basis_revision: Mapped[int] = mapped_column(nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[LifecycleJobState] = mapped_column(
        Enum(LifecycleJobState, values_callable=enum_values, native_enum=False, length=20),
        nullable=False,
        default=LifecycleJobState.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dispatch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
