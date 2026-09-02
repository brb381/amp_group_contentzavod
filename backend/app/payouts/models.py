import enum
import uuid
from datetime import date, datetime
from typing import Any

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
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.database.base import Base


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class PayoutStatus(str, enum.Enum):
    REQUESTED = "requested"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    PAID = "paid"
    REJECTED = "rejected"


class RecipientType(str, enum.Enum):
    INDIVIDUAL = "individual"
    SELF_EMPLOYED = "self_employed"


class ReceiptStatus(str, enum.Enum):
    """API-facing status derived from payout and receipt dates."""

    PENDING_PAYMENT = "pending_payment"
    NOT_APPLICABLE = "not_applicable"
    AWAITING_RECEIPT = "awaiting_receipt"
    OVERDUE = "overdue"
    RECEIVED = "received"


class PayoutEventAction(str, enum.Enum):
    REQUESTED = "requested"
    REVIEW_STARTED = "review_started"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"
    RECEIPT_RECEIVED = "receipt_received"


ACTIVE_PAYOUT_STATUS_SQL = "status IN ('requested', 'under_review', 'approved')"
ANONYMIZED_TEXT = "[anonymized]"


class PayoutDetails(Base):
    __tablename__ = "payout_details"
    __table_args__ = (
        CheckConstraint("length(trim(sbp_phone)) > 0", name="ck_payout_details_phone_required"),
        CheckConstraint(
            "bank_name IS NULL OR length(trim(bank_name)) > 0",
            name="ck_payout_details_bank_nonempty",
        ),
    )

    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    sbp_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pii_anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PayoutRequest(Base):
    __tablename__ = "payout_requests"
    __table_args__ = (
        CheckConstraint("amount_kopecks > 0", name="ck_payout_requests_amount_positive"),
        CheckConstraint("currency = 'RUB'", name="ck_payout_requests_currency_rub"),
        CheckConstraint(
            "status IN ('requested', 'under_review', 'approved', 'paid', 'rejected')",
            name="ck_payout_requests_status",
        ),
        CheckConstraint(
            "recipient_type IN ('individual', 'self_employed')",
            name="ck_payout_requests_recipient_type",
        ),
        CheckConstraint(
            "length(trim(request_number)) > 0 AND "
            "length(trim(recipient_full_name)) > 0 AND "
            "length(trim(recipient_display_name)) > 0 AND "
            "length(trim(sbp_phone)) > 0",
            name="ck_payout_requests_snapshot_required",
        ),
        CheckConstraint(
            "((review_started_at IS NULL AND reviewer_user_id IS NULL) OR "
            "(review_started_at IS NOT NULL AND reviewer_user_id IS NOT NULL))",
            name="ck_payout_requests_review_pair",
        ),
        CheckConstraint(
            "status NOT IN ('under_review', 'approved', 'paid') OR "
            "(review_started_at IS NOT NULL AND reviewer_user_id IS NOT NULL)",
            name="ck_payout_requests_review_required",
        ),
        CheckConstraint(
            "((requisites_verified_at IS NULL AND requisites_verified_by_user_id IS NULL) OR "
            "(requisites_verified_at IS NOT NULL AND requisites_verified_by_user_id IS NOT NULL))",
            name="ck_payout_requests_requisites_pair",
        ),
        CheckConstraint(
            "((self_employment_verified_at IS NULL AND "
            "self_employment_verified_by_user_id IS NULL) OR "
            "(self_employment_verified_at IS NOT NULL AND "
            "self_employment_verified_by_user_id IS NOT NULL))",
            name="ck_payout_requests_self_employment_pair",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'paid') OR "
            "(approved_at IS NOT NULL AND approved_by_user_id IS NOT NULL AND "
            "payment_due_date IS NOT NULL AND requisites_verified_at IS NOT NULL)",
            name="ck_payout_requests_approval_required",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'paid') OR recipient_type != 'self_employed' OR "
            "self_employment_verified_at IS NOT NULL",
            name="ck_payout_requests_self_employment_approval",
        ),
        CheckConstraint(
            "((paid_on IS NULL AND paid_recorded_at IS NULL AND paid_by_user_id IS NULL) OR "
            "(paid_on IS NOT NULL AND paid_recorded_at IS NOT NULL AND "
            "paid_by_user_id IS NOT NULL))",
            name="ck_payout_requests_paid_pair",
        ),
        CheckConstraint(
            "(status = 'paid' AND paid_on IS NOT NULL) OR "
            "(status != 'paid' AND paid_on IS NULL)",
            name="ck_payout_requests_paid_status",
        ),
        CheckConstraint(
            "((rejected_at IS NULL AND rejected_by_user_id IS NULL AND rejection_reason IS NULL) "
            "OR (rejected_at IS NOT NULL AND rejected_by_user_id IS NOT NULL AND "
            "rejection_reason IS NOT NULL AND length(trim(rejection_reason)) > 0))",
            name="ck_payout_requests_rejection_pair",
        ),
        CheckConstraint(
            "(status = 'rejected' AND rejected_at IS NOT NULL) OR "
            "(status != 'rejected' AND rejected_at IS NULL)",
            name="ck_payout_requests_rejected_status",
        ),
        CheckConstraint(
            "receipt_due_date IS NULL OR "
            "(status = 'paid' AND recipient_type = 'self_employed')",
            name="ck_payout_requests_receipt_due_scope",
        ),
        CheckConstraint(
            "status != 'paid' OR recipient_type != 'self_employed' OR "
            "receipt_due_date IS NOT NULL",
            name="ck_payout_requests_receipt_due_required",
        ),
        CheckConstraint(
            "((receipt_received_on IS NULL AND receipt_recorded_at IS NULL AND "
            "receipt_received_by_user_id IS NULL) OR "
            "(receipt_received_on IS NOT NULL AND receipt_recorded_at IS NOT NULL AND "
            "receipt_received_by_user_id IS NOT NULL AND receipt_due_date IS NOT NULL AND "
            "status = 'paid' AND recipient_type = 'self_employed'))",
            name="ck_payout_requests_receipt_received_pair",
        ),
        UniqueConstraint(
            "request_number",
            name="uq_payout_requests_request_number",
        ),
        Index(
            "uq_payout_requests_active_blogger",
            "blogger_id",
            unique=True,
            postgresql_where=text(ACTIVE_PAYOUT_STATUS_SQL),
            sqlite_where=text(ACTIVE_PAYOUT_STATUS_SQL),
        ),
        Index("ix_payout_requests_blogger_requested", "blogger_id", "requested_at"),
        Index("ix_payout_requests_status_requested", "status", "requested_at"),
        Index("ix_payout_requests_pii_retention", "pii_anonymized_at", "blogger_id"),
        Index("ix_payout_requests_payment_due", "status", "payment_due_date"),
        Index(
            "ix_payout_requests_receipt_due",
            "recipient_type",
            "status",
            "receipt_due_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_number: Mapped[str] = mapped_column(String(40), nullable=False)
    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    amount_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB", server_default="RUB")
    status: Mapped[PayoutStatus] = mapped_column(
        Enum(PayoutStatus, values_callable=enum_values, native_enum=False, length=20),
        nullable=False,
        default=PayoutStatus.REQUESTED,
        server_default=PayoutStatus.REQUESTED.value,
    )

    recipient_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_type: Mapped[RecipientType] = mapped_column(
        Enum(RecipientType, values_callable=enum_values, native_enum=False, length=20),
        nullable=False,
    )
    sbp_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pii_anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    review_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    requisites_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requisites_verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    self_employment_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    self_employment_verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    payment_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    payment_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    receipt_received_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    receipt_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_received_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PayoutEvent(Base):
    __tablename__ = "payout_events"
    __table_args__ = (
        CheckConstraint("sequence_number >= 1", name="ck_payout_events_sequence_positive"),
        CheckConstraint("length(payload_hash) = 64", name="ck_payout_events_payload_hash"),
        CheckConstraint(
            "action IN ('requested', 'review_started', 'approved', 'rejected', "
            "'paid', 'receipt_received')",
            name="ck_payout_events_action",
        ),
        CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('requested', 'under_review', 'approved', 'paid', 'rejected')",
            name="ck_payout_events_from_status",
        ),
        CheckConstraint(
            "to_status IN ('requested', 'under_review', 'approved', 'paid', 'rejected')",
            name="ck_payout_events_to_status",
        ),
        UniqueConstraint(
            "payout_request_id", "sequence_number", name="uq_payout_events_request_sequence"
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_payout_events_idempotency_key",
        ),
        Index("ix_payout_events_request_created", "payout_request_id", "created_at"),
        Index("ix_payout_events_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    payout_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payout_requests.id", ondelete="RESTRICT"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[PayoutEventAction] = mapped_column(
        Enum(PayoutEventAction, values_callable=enum_values, native_enum=False, length=32),
        nullable=False,
    )
    from_status: Mapped[PayoutStatus | None] = mapped_column(
        Enum(PayoutStatus, values_callable=enum_values, native_enum=False, length=20),
        nullable=True,
    )
    to_status: Mapped[PayoutStatus] = mapped_column(
        Enum(PayoutStatus, values_callable=enum_values, native_enum=False, length=20),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    detail: Mapped["PayoutEventDetail | None"] = relationship(
        back_populates="event",
        cascade="save-update",
        uselist=False,
    )


class PayoutEventDetail(Base):
    __tablename__ = "payout_event_details"
    __table_args__ = (
        CheckConstraint(
            "comment IS NOT NULL OR rejection_reason IS NOT NULL OR "
            "payment_reference IS NOT NULL",
            name="ck_payout_event_details_not_empty",
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payout_events.id", ondelete="RESTRICT"), primary_key=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pii_anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    event: Mapped[PayoutEvent] = relationship(back_populates="detail")


PAYOUT_SNAPSHOT_FIELDS = (
    "request_number",
    "blogger_id",
    "amount_kopecks",
    "currency",
    "recipient_full_name",
    "recipient_display_name",
    "recipient_type",
    "sbp_phone",
    "bank_name",
    "requested_at",
)

PAYOUT_RECORDED_FACT_FIELDS = (
    "review_started_at",
    "reviewer_user_id",
    "requisites_verified_at",
    "requisites_verified_by_user_id",
    "self_employment_verified_at",
    "self_employment_verified_by_user_id",
    "approved_at",
    "approved_by_user_id",
    "payment_due_date",
    "paid_on",
    "paid_recorded_at",
    "paid_by_user_id",
    "payment_reference",
    "rejected_at",
    "rejected_by_user_id",
    "rejection_reason",
    "receipt_due_date",
    "receipt_received_on",
    "receipt_recorded_at",
    "receipt_received_by_user_id",
)


def _reject_payout_snapshot_mutation(
    mapper: Mapper, connection: object, target: PayoutRequest
) -> None:
    del mapper, connection
    state = inspect(target)
    changed_fields = [
        field_name
        for field_name in PAYOUT_SNAPSHOT_FIELDS
        if state.attrs[field_name].history.has_changes()
    ]
    anonymizing = (
        state.attrs.pii_anonymized_at.history.has_changes()
        and state.attrs.pii_anonymized_at.history.deleted
        and state.attrs.pii_anonymized_at.history.deleted[0] is None
        and target.pii_anonymized_at is not None
        and target.status in {PayoutStatus.PAID, PayoutStatus.REJECTED}
        and target.recipient_full_name == ANONYMIZED_TEXT
        and target.recipient_display_name == ANONYMIZED_TEXT
        and target.sbp_phone == ANONYMIZED_TEXT
        and target.bank_name is None
        and target.manager_comment is None
        and (
            target.rejection_reason is None
            or target.rejection_reason == ANONYMIZED_TEXT
        )
    )
    allowed_anonymized_fields = {
        "recipient_full_name",
        "recipient_display_name",
        "sbp_phone",
        "bank_name",
    }
    if changed_fields and not (
        anonymizing and set(changed_fields) <= allowed_anonymized_fields
    ):
        raise ValueError(
            f"Payout request snapshot is immutable: {', '.join(changed_fields)}"
        )
    for field_name in PAYOUT_RECORDED_FACT_FIELDS:
        history = state.attrs[field_name].history
        if (
            history.has_changes()
            and history.deleted
            and history.deleted[0] is not None
            and not (
                anonymizing
                and field_name == "rejection_reason"
                and target.rejection_reason == ANONYMIZED_TEXT
            )
        ):
            raise ValueError(f"Payout fact is immutable once recorded: {field_name}")
    manager_comment_history = state.attrs.manager_comment.history
    old_status = state.attrs.status.history.deleted
    if (
        (
            (old_status and old_status[0] in {PayoutStatus.PAID, PayoutStatus.REJECTED})
            or (
                not state.attrs.status.history.has_changes()
                and target.status in {PayoutStatus.PAID, PayoutStatus.REJECTED}
            )
        )
        and manager_comment_history.has_changes()
        and manager_comment_history.deleted
        and not anonymizing
    ):
        raise ValueError("Payout fact is immutable once recorded: manager_comment")


def _guard_payout_details_mutation(
    mapper: Mapper, connection: object, target: PayoutDetails
) -> None:
    del mapper, connection
    state = inspect(target)
    old_marker = state.attrs.pii_anonymized_at.history.deleted
    anonymizing = (
        state.attrs.pii_anonymized_at.history.has_changes()
        and old_marker
        and old_marker[0] is None
        and target.pii_anonymized_at is not None
        and target.sbp_phone == ANONYMIZED_TEXT
        and target.bank_name is None
    )
    if target.pii_anonymized_at is not None and not anonymizing:
        raise ValueError("Anonymized payout details are immutable")


def _guard_payout_event_detail_mutation(
    mapper: Mapper, connection: object, target: PayoutEventDetail
) -> None:
    del mapper, connection
    state = inspect(target)
    old_marker = state.attrs.pii_anonymized_at.history.deleted
    anonymizing = (
        state.attrs.pii_anonymized_at.history.has_changes()
        and old_marker
        and old_marker[0] is None
        and target.pii_anonymized_at is not None
        and (target.comment is None or target.comment == ANONYMIZED_TEXT)
        and (
            target.rejection_reason is None
            or target.rejection_reason == ANONYMIZED_TEXT
        )
    )
    if not anonymizing:
        raise ValueError("Payout event details are append-only")


def _reject_payout_event_mutation(
    mapper: Mapper, connection: object, target: PayoutEvent
) -> None:
    del mapper, connection, target
    raise ValueError("Payout events are append-only")


event.listen(PayoutRequest, "before_update", _reject_payout_snapshot_mutation)
event.listen(PayoutDetails, "before_update", _guard_payout_details_mutation)
event.listen(PayoutEvent, "before_update", _reject_payout_event_mutation)
event.listen(PayoutEvent, "before_delete", _reject_payout_event_mutation)
event.listen(PayoutEventDetail, "before_update", _guard_payout_event_detail_mutation)
event.listen(PayoutEventDetail, "before_delete", _reject_payout_event_mutation)
