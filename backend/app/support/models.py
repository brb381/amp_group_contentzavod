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
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.database.base import Base


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class SupportCategory(str, enum.Enum):
    GENERAL = "general"
    CONTENT = "content"
    PAYMENT = "payment"
    TECHNICAL = "technical"
    ACCOUNT_RECOVERY = "account_recovery"


class SupportStatus(str, enum.Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WAITING_BLOGGER = "waiting_blogger"
    RESOLVED = "resolved"
    CLOSED = "closed"


class SupportEventType(str, enum.Enum):
    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    RECOVERY_DECIDED = "recovery_decided"


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint("length(trim(ticket_number)) > 0", name="ck_support_ticket_number_required"),
        CheckConstraint("length(trim(subject)) > 0", name="ck_support_ticket_subject_required"),
        CheckConstraint("length(creation_payload_hash) = 64", name="ck_support_ticket_payload_hash"),
        CheckConstraint(
            "category IN ('general', 'content', 'payment', 'technical', 'account_recovery')",
            name="ck_support_ticket_category",
        ),
        CheckConstraint(
            "status IN ('new', 'in_progress', 'waiting_blogger', 'resolved', 'closed')",
            name="ck_support_ticket_status",
        ),
        CheckConstraint(
            "(resolved_at IS NULL) OR status IN ('resolved', 'closed')",
            name="ck_support_ticket_resolved_scope",
        ),
        CheckConstraint(
            "(closed_at IS NULL) OR status = 'closed'",
            name="ck_support_ticket_closed_scope",
        ),
        CheckConstraint(
            "(related_object_type IS NULL) = (related_object_id IS NULL)",
            name="ck_support_ticket_related_object_pair",
        ),
        UniqueConstraint("ticket_number", name="uq_support_ticket_number"),
        UniqueConstraint(
            "blogger_id", "creation_idempotency_key", name="uq_support_ticket_creation_key"
        ),
        Index("ix_support_ticket_blogger_updated", "blogger_id", "updated_at"),
        Index("ix_support_ticket_queue", "status", "category", "updated_at"),
        Index("ix_support_ticket_assignee", "assigned_to_user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ticket_number: Mapped[str] = mapped_column(String(40), nullable=False)
    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[SupportCategory] = mapped_column(
        Enum(SupportCategory, values_callable=enum_values, native_enum=False, length=24),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[SupportStatus] = mapped_column(
        Enum(SupportStatus, values_callable=enum_values, native_enum=False, length=24),
        nullable=False,
        default=SupportStatus.NEW,
    )
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    related_object_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_object_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    creation_idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    creation_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    pii_anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportMessage(Base):
    __tablename__ = "support_messages"
    __table_args__ = (
        CheckConstraint("length(trim(body)) > 0", name="ck_support_message_body_required"),
        CheckConstraint("length(payload_hash) = 64", name="ck_support_message_payload_hash"),
        UniqueConstraint(
            "author_user_id", "idempotency_key", name="uq_support_message_author_key"
        ),
        Index("ix_support_message_ticket_created", "ticket_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="RESTRICT"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    author_role: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    pii_anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SupportTicketEvent(Base):
    __tablename__ = "support_ticket_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('created', 'status_changed', 'assigned', 'recovery_decided')",
            name="ck_support_event_type",
        ),
        CheckConstraint("length(payload_hash) = 64", name="ck_support_event_payload_hash"),
        CheckConstraint(
            "(event_type = 'created' AND from_status IS NULL AND to_status = 'new' "
            "AND previous_assignee_user_id IS NULL AND new_assignee_user_id IS NULL "
            "AND recovery_decision IS NULL) OR "
            "(event_type = 'status_changed' AND from_status IS NOT NULL AND to_status IS NOT NULL "
            "AND previous_assignee_user_id IS NULL AND new_assignee_user_id IS NULL "
            "AND recovery_decision IS NULL) OR "
            "(event_type = 'assigned' AND from_status IS NULL AND to_status IS NULL "
            "AND recovery_decision IS NULL) OR "
            "(event_type = 'recovery_decided' AND from_status IS NOT NULL "
            "AND to_status = 'resolved' AND recovery_decision IN ('approve', 'reject') "
            "AND previous_assignee_user_id IS NULL AND new_assignee_user_id IS NULL)",
            name="ck_support_event_shape",
        ),
        UniqueConstraint(
            "actor_user_id", "idempotency_key", name="uq_support_event_actor_key"
        ),
        Index("ix_support_event_ticket_created", "ticket_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[SupportEventType] = mapped_column(
        Enum(SupportEventType, values_callable=enum_values, native_enum=False, length=24),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    from_status: Mapped[SupportStatus | None] = mapped_column(
        Enum(SupportStatus, values_callable=enum_values, native_enum=False, length=24),
        nullable=True,
    )
    to_status: Mapped[SupportStatus | None] = mapped_column(
        Enum(SupportStatus, values_callable=enum_values, native_enum=False, length=24),
        nullable=True,
    )
    previous_assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    new_assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    pii_anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def _reject_history_mutation(
    mapper: Mapper, connection: object, target: object
) -> None:
    del mapper, connection, target
    raise ValueError("Support history is append-only")


for history_model in (SupportMessage, SupportTicketEvent):
    event.listen(history_model, "before_update", _reject_history_mutation)
    event.listen(history_model, "before_delete", _reject_history_mutation)
