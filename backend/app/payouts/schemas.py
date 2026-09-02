import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.payouts.models import PayoutEventAction, PayoutStatus, ReceiptStatus, RecipientType
from app.payouts.policy import effective_receipt_status, is_payment_overdue


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class PayoutDetailsUpsertRequest(_Command):
    sbp_phone: str = Field(pattern=r"^\+7[0-9]{10}$")
    bank_name: str | None = Field(default=None, max_length=255)

    @field_validator("bank_name")
    @classmethod
    def normalize_bank_name(cls, value: str | None) -> str | None:
        return _optional_text(value)


class PayoutDetailsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    blogger_id: uuid.UUID
    sbp_phone: str
    bank_name: str | None
    created_at: datetime
    updated_at: datetime


class PayoutCreateRequest(_Command):
    idempotency_key: uuid.UUID


class PayoutReviewRequest(_Command):
    idempotency_key: uuid.UUID
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        return _optional_text(value)


class PayoutApprovalRequest(_Command):
    idempotency_key: uuid.UUID
    requisites_verified: Literal[True]
    self_employment_verified: bool | None = None
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        return _optional_text(value)


class PayoutRejectionRequest(_Command):
    idempotency_key: uuid.UUID
    reason: str = Field(min_length=3, max_length=2000)
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 3:
            raise ValueError("reason must contain at least 3 non-whitespace characters")
        return stripped

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        return _optional_text(value)


class PayoutPaymentRequest(_Command):
    idempotency_key: uuid.UUID
    paid_on: date
    payment_reference: str | None = Field(default=None, max_length=255)

    @field_validator("payment_reference")
    @classmethod
    def normalize_payment_reference(cls, value: str | None) -> str | None:
        return _optional_text(value)


class PayoutReceiptRequest(_Command):
    idempotency_key: uuid.UUID
    received_on: date


class _PayoutRequestStoredResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_number: str
    blogger_id: uuid.UUID
    amount_kopecks: int
    currency: str
    status: PayoutStatus
    recipient_full_name: str
    recipient_display_name: str
    recipient_type: RecipientType
    sbp_phone: str
    bank_name: str | None
    requested_at: datetime
    review_started_at: datetime | None
    reviewer_user_id: uuid.UUID | None
    requisites_verified_at: datetime | None
    requisites_verified_by_user_id: uuid.UUID | None
    self_employment_verified_at: datetime | None
    self_employment_verified_by_user_id: uuid.UUID | None
    approved_at: datetime | None
    approved_by_user_id: uuid.UUID | None
    payment_due_date: date | None
    paid_on: date | None
    paid_recorded_at: datetime | None
    paid_by_user_id: uuid.UUID | None
    payment_reference: str | None
    rejected_at: datetime | None
    rejected_by_user_id: uuid.UUID | None
    rejection_reason: str | None
    manager_comment: str | None
    receipt_due_date: date | None
    receipt_received_on: date | None
    receipt_recorded_at: datetime | None
    receipt_received_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class PayoutRequestResponse(_PayoutRequestStoredResponse):
    receipt_status: ReceiptStatus
    is_payment_overdue: bool


class MyPayoutRequestResponse(BaseModel):
    """Creator-facing payout projection without staff-only fields."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_number: str
    amount_kopecks: int
    currency: str
    status: PayoutStatus
    recipient_full_name: str
    recipient_display_name: str
    recipient_type: RecipientType
    sbp_phone: str
    bank_name: str | None
    requested_at: datetime
    review_started_at: datetime | None
    approved_at: datetime | None
    payment_due_date: date | None
    paid_on: date | None
    rejected_at: datetime | None
    rejection_reason: str | None
    receipt_due_date: date | None
    receipt_received_on: date | None
    created_at: datetime
    updated_at: datetime
    receipt_status: ReceiptStatus
    is_payment_overdue: bool


class PayoutEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence_number: int
    action: PayoutEventAction
    from_status: PayoutStatus | None
    to_status: PayoutStatus
    actor_user_id: uuid.UUID
    event_metadata: dict[str, Any]
    created_at: datetime
    comment: str | None = None
    rejection_reason: str | None = None
    payment_reference: str | None = None


class PayoutRequestDetailResponse(PayoutRequestResponse):
    history: list[PayoutEventResponse]


class PayoutRequestListResponse(BaseModel):
    items: list[PayoutRequestResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class MyPayoutRequestListResponse(BaseModel):
    items: list[MyPayoutRequestResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class PayoutCommandResponse(BaseModel):
    """Immutable receipt for a state-changing payout command."""

    payout_request_id: uuid.UUID
    request_number: str
    sequence_number: int
    action: PayoutEventAction
    from_status: PayoutStatus | None
    status: PayoutStatus
    actor_user_id: uuid.UUID
    recorded_at: datetime
    details: dict[str, Any]


def payout_response(
    payout: object,
    today: date | None = None,
) -> PayoutRequestResponse:
    effective_today = today or getattr(payout, "_response_today", None)
    if isinstance(payout, PayoutRequestResponse):
        stored_data = payout.model_dump(
            exclude={"receipt_status", "is_payment_overdue"}
        )
    else:
        stored_data = _PayoutRequestStoredResponse.model_validate(payout).model_dump()
    return PayoutRequestResponse(
        **stored_data,
        receipt_status=effective_receipt_status(payout, today=effective_today),
        is_payment_overdue=is_payment_overdue(payout, today=effective_today),
    )


def my_payout_response(
    payout: object,
    today: date | None = None,
) -> MyPayoutRequestResponse:
    effective_today = today or getattr(payout, "_response_today", None)
    return MyPayoutRequestResponse(
        id=getattr(payout, "id"),
        request_number=getattr(payout, "request_number"),
        amount_kopecks=getattr(payout, "amount_kopecks"),
        currency=getattr(payout, "currency"),
        status=getattr(payout, "status"),
        recipient_full_name=getattr(payout, "recipient_full_name"),
        recipient_display_name=getattr(payout, "recipient_display_name"),
        recipient_type=getattr(payout, "recipient_type"),
        sbp_phone=getattr(payout, "sbp_phone"),
        bank_name=getattr(payout, "bank_name"),
        requested_at=getattr(payout, "requested_at"),
        review_started_at=getattr(payout, "review_started_at"),
        approved_at=getattr(payout, "approved_at"),
        payment_due_date=getattr(payout, "payment_due_date"),
        paid_on=getattr(payout, "paid_on"),
        rejected_at=getattr(payout, "rejected_at"),
        rejection_reason=getattr(payout, "rejection_reason"),
        receipt_due_date=getattr(payout, "receipt_due_date"),
        receipt_received_on=getattr(payout, "receipt_received_on"),
        created_at=getattr(payout, "created_at"),
        updated_at=getattr(payout, "updated_at"),
        receipt_status=effective_receipt_status(payout, today=effective_today),
        is_payment_overdue=is_payment_overdue(payout, today=effective_today),
    )


def payout_command_response(
    payout: object,
    event: object,
) -> PayoutCommandResponse:
    details = dict(getattr(event, "event_metadata") or {})
    details.pop("command_schema_version", None)
    return PayoutCommandResponse(
        payout_request_id=getattr(payout, "id"),
        request_number=getattr(payout, "request_number"),
        sequence_number=getattr(event, "sequence_number"),
        action=getattr(event, "action"),
        from_status=getattr(event, "from_status"),
        status=getattr(event, "to_status"),
        actor_user_id=getattr(event, "actor_user_id"),
        recorded_at=getattr(event, "created_at"),
        details=details,
    )


def payout_detail_response(
    payout: object,
    today: date | None = None,
) -> PayoutRequestDetailResponse:
    response = payout_response(payout, today=today)
    return PayoutRequestDetailResponse(
        **response.model_dump(),
        history=[_payout_event_response(event) for event in getattr(payout, "history", ())],
    )


def _payout_event_response(event: object) -> PayoutEventResponse:
    detail = getattr(event, "detail", None)
    stored = PayoutEventResponse.model_validate(event).model_dump(
        exclude={"comment", "rejection_reason", "payment_reference"}
    )
    return PayoutEventResponse(
        **stored,
        comment=getattr(detail, "comment", None),
        rejection_reason=getattr(detail, "rejection_reason", None),
        payment_reference=getattr(detail, "payment_reference", None),
    )
