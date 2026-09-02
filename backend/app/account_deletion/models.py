import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy import event
from sqlalchemy import inspect
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.database.base import Base


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class DeletionRequestStatus(str, enum.Enum):
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DeletionEventAction(str, enum.Enum):
    REQUESTED = "requested"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AccountDeletionRequest(Base):
    __tablename__ = "account_deletion_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_confirmation', 'completed', 'cancelled', 'expired')",
            name="ck_account_deletion_request_status",
        ),
        CheckConstraint(
            "expires_at > requested_at", name="ck_account_deletion_request_expiry"
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND cancelled_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('awaiting_confirmation', 'expired') AND completed_at IS NULL "
            "AND cancelled_at IS NULL)",
            name="ck_account_deletion_request_terminal_shape",
        ),
        UniqueConstraint(
            "blogger_id",
            "creation_idempotency_key",
            name="uq_account_deletion_creation_key",
        ),
        UniqueConstraint(
            "confirmation_token_hash", name="uq_account_deletion_token_hash"
        ),
        Index(
            "uq_account_deletion_active_blogger",
            "blogger_id",
            unique=True,
            postgresql_where=text("status = 'awaiting_confirmation'"),
            sqlite_where=text("status = 'awaiting_confirmation'"),
        ),
        Index(
            "ix_account_deletion_blogger_requested", "blogger_id", "requested_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[DeletionRequestStatus] = mapped_column(
        Enum(
            DeletionRequestStatus,
            values_callable=enum_values,
            native_enum=False,
            length=32,
        ),
        nullable=False,
        default=DeletionRequestStatus.AWAITING_CONFIRMATION,
    )
    creation_idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    creation_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmation_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AccountDeletionEvent(Base):
    __tablename__ = "account_deletion_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('requested', 'completed', 'cancelled', 'expired')",
            name="ck_account_deletion_event_action",
        ),
        CheckConstraint(
            "length(payload_hash) = 64", name="ck_account_deletion_event_payload_hash"
        ),
        UniqueConstraint(
            "actor_user_id",
            "idempotency_key",
            name="uq_account_deletion_event_actor_key",
        ),
        Index(
            "ix_account_deletion_event_request_created", "request_id", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account_deletion_requests.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[DeletionEventAction] = mapped_column(
        Enum(
            DeletionEventAction,
            values_callable=enum_values,
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_deletion_event_mutation(
    mapper: Mapper, connection: object, target: object
) -> None:
    del mapper, connection, target
    raise ValueError("Account deletion history is append-only")


def _guard_deletion_request_update(
    mapper: Mapper, connection: object, target: AccountDeletionRequest
) -> None:
    del mapper, connection
    state = inspect(target)
    changed = {attribute.key for attribute in state.attrs if attribute.history.has_changes()}
    status_history = state.attrs.status.history
    previous = status_history.deleted[0] if status_history.deleted else target.status
    allowed = {
        DeletionRequestStatus.COMPLETED: {"status", "completed_at"},
        DeletionRequestStatus.CANCELLED: {"status", "cancelled_at"},
        DeletionRequestStatus.EXPIRED: {"status"},
    }
    if (
        previous != DeletionRequestStatus.AWAITING_CONFIRMATION
        or target.status not in allowed
        or not {"status"} <= changed <= allowed[target.status]
    ):
        raise ValueError("Invalid account deletion request transition")


def _reject_deletion_request_delete(
    mapper: Mapper, connection: object, target: AccountDeletionRequest
) -> None:
    del mapper, connection, target
    raise ValueError("Account deletion requests cannot be deleted")


event.listen(AccountDeletionEvent, "before_update", _reject_deletion_event_mutation)
event.listen(AccountDeletionEvent, "before_delete", _reject_deletion_event_mutation)
event.listen(AccountDeletionRequest, "before_update", _guard_deletion_request_update)
event.listen(AccountDeletionRequest, "before_delete", _reject_deletion_request_delete)
