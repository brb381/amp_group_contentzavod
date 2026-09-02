import hashlib
import json
import math
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.billing.models import CreatorBalance
from app.billing.wallet import (
    WalletInvariantError,
    lock_creator_balance,
    release_payout,
    reserve_payout,
    settle_payout,
)
from app.clock import utc_now
from app.creators.models import CreatorProfile, ProfileStatus
from app.database.locking import set_transaction_timeouts
from app.errors import APIError
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification
from app.lifecycle.activity import record_creator_activity
from app.lifecycle.models import ActivityKind
from app.payouts.models import (
    PayoutDetails,
    PayoutEvent,
    PayoutEventDetail,
    PayoutRequest,
    PayoutStatus,
    ReceiptStatus,
    RecipientType,
)
from app.payouts.policy import (
    effective_receipt_status,
    is_payment_overdue,
    moscow_today,
    payment_due_date,
    receipt_due_date,
)
from app.payouts.schemas import (
    MyPayoutRequestListResponse,
    PayoutApprovalRequest,
    PayoutCommandResponse,
    PayoutCreateRequest,
    PayoutDetailsUpsertRequest,
    PayoutPaymentRequest,
    PayoutReceiptRequest,
    PayoutRejectionRequest,
    PayoutRequestListResponse,
    PayoutReviewRequest,
    my_payout_response,
    payout_command_response,
    payout_response,
)


MOSCOW = ZoneInfo("Europe/Moscow")
ACTIVE_PAYOUT_STATUSES = (
    PayoutStatus.REQUESTED,
    PayoutStatus.UNDER_REVIEW,
    PayoutStatus.APPROVED,
)
STAFF_PAYOUT_READER_ROLES = {Role.MANAGER, Role.FINANCE, Role.ADMIN}
PAYOUT_LOCK_TIMEOUT_MS = 5_000
PAYOUT_STATEMENT_TIMEOUT_MS = 30_000
PAYOUT_COMMAND_SCHEMA_VERSION = 1
FINANCE_VISIBLE_PAYOUT_STATUSES = (PayoutStatus.APPROVED, PayoutStatus.PAID)


def _enum_value(value: Any) -> str:
    return getattr(value, "value", value)


def _lock_actor(db: Session, actor: User, roles: set[Role], error_code: str) -> User:
    set_transaction_timeouts(
        db,
        lock_timeout_ms=PAYOUT_LOCK_TIMEOUT_MS,
        statement_timeout_ms=PAYOUT_STATEMENT_TIMEOUT_MS,
    )
    locked = db.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not locked or locked.status != AccountStatus.ACTIVE or locked.role not in roles:
        raise APIError(403, error_code, "Payout permissions changed")
    return locked


def _forbid_self_processing(actor: User, payout: PayoutRequest) -> None:
    if actor.id == payout.blogger_id:
        raise APIError(
            403,
            "PAYOUT_SELF_PROCESSING_FORBIDDEN",
            "Staff cannot process their own payout request",
        )


def _command_hash(
    *,
    action: str,
    actor_id: uuid.UUID,
    payout_request_id: uuid.UUID | None,
    payload: Any,
    schema_version: int | None = None,
) -> str:
    effective_schema_version = schema_version or PAYOUT_COMMAND_SCHEMA_VERSION
    payload_data = payload.model_dump(
        mode="json",
        exclude={"idempotency_key"},
        exclude_none=True,
        exclude_unset=True,
    )
    canonical = json.dumps(
        {
            "action": action,
            "command_schema_version": effective_schema_version,
            "actor_id": str(actor_id),
            "payout_request_id": str(payout_request_id) if payout_request_id else None,
            "payload": payload_data,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotent_result(
    db: Session,
    *,
    idempotency_key: uuid.UUID,
    action: str,
    actor_id: uuid.UUID,
    payout_request_id: uuid.UUID | None,
    payload: Any,
) -> PayoutCommandResponse | None:
    event = db.scalar(
        select(PayoutEvent).where(PayoutEvent.idempotency_key == idempotency_key)
    )
    if not event:
        return None
    try:
        event_schema_version = int(
            (event.event_metadata or {}).get("command_schema_version", 1)
        )
    except (TypeError, ValueError) as error:
        raise APIError(
            409,
            "PAYOUT_HISTORY_INCONSISTENT",
            "Payout command history has an invalid schema version",
        ) from error
    expected_hash = _command_hash(
        action=action,
        actor_id=actor_id,
        payout_request_id=payout_request_id,
        payload=payload,
        schema_version=event_schema_version,
    )
    if event.action != action or event.payload_hash != expected_hash:
        raise APIError(
            409,
            "PAYOUT_IDEMPOTENCY_CONFLICT",
            "Idempotency key was already used for a different payout command",
        )
    payout = db.get(PayoutRequest, event.payout_request_id)
    if not payout:
        raise APIError(
            409,
            "PAYOUT_HISTORY_INCONSISTENT",
            "Payout command history references a missing request",
        )
    return payout_command_response(payout, event)


def _next_event_sequence(db: Session, payout_request_id: uuid.UUID) -> int:
    current = db.scalar(
        select(func.max(PayoutEvent.sequence_number)).where(
            PayoutEvent.payout_request_id == payout_request_id
        )
    )
    return (current or 0) + 1


def _add_event(
    db: Session,
    *,
    payout: PayoutRequest,
    action: str,
    actor_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    payload_hash: str,
    from_status: PayoutStatus | None,
    to_status: PayoutStatus,
    metadata: dict[str, Any] | None = None,
    comment: str | None = None,
    rejection_reason: str | None = None,
    payment_reference: str | None = None,
) -> PayoutEvent:
    event = PayoutEvent(
        payout_request_id=payout.id,
        sequence_number=_next_event_sequence(db, payout.id),
        action=action,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=actor_id,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        event_metadata={
            "command_schema_version": PAYOUT_COMMAND_SCHEMA_VERSION,
            **(metadata or {}),
        },
    )
    if comment is not None or rejection_reason is not None or payment_reference is not None:
        event.detail = PayoutEventDetail(
            comment=comment,
            rejection_reason=rejection_reason,
            payment_reference=payment_reference,
        )
    db.add(event)
    return event


def _state_conflict(payout: PayoutRequest, *allowed: PayoutStatus) -> APIError:
    return APIError(
        409,
        "PAYOUT_STATE_CONFLICT",
        "Payout request is not in a state allowed for this command",
        {
            "current_status": _enum_value(payout.status),
            "allowed_statuses": [_enum_value(item) for item in allowed],
        },
    )


def _wallet_error(error: WalletInvariantError) -> APIError:
    return APIError(409, error.code, error.message)


def _decorate_payout(payout: PayoutRequest, *, today: date) -> PayoutRequest:
    payout.receipt_status = effective_receipt_status(payout, today=today)
    payout.is_payment_overdue = is_payment_overdue(payout, today=today)
    payout._response_today = today
    return payout


def _attach_history(db: Session, payout: PayoutRequest, *, today: date) -> PayoutRequest:
    payout.history = list(
        db.scalars(
            select(PayoutEvent)
            .options(selectinload(PayoutEvent.detail))
            .where(PayoutEvent.payout_request_id == payout.id)
            .order_by(PayoutEvent.sequence_number)
        )
    )
    return _decorate_payout(payout, today=today)


def _request_number(payout_id: uuid.UUID, today: date) -> str:
    return f"PAY-{today:%Y%m%d}-{payout_id.hex[:19].upper()}"


def _notify_payout_status(db: Session, payout: PayoutRequest) -> None:
    status_value = _enum_value(payout.status)
    create_notification(
        db,
        NotificationCommand(
            recipient_user_id=payout.blogger_id,
            template_code="payout_status_changed",
            context={
                "request_number": payout.request_number,
                "status": status_value,
            },
            severity=(
                NotificationSeverity.ACTION_REQUIRED
                if status_value == PayoutStatus.REJECTED.value
                else NotificationSeverity.INFO
            ),
            deduplication_key=f"payout:{payout.id}:status:{status_value}",
            related_object_type="payout_request",
            related_object_id=payout.id,
            action_path=f"/payouts/{payout.id}",
        ),
    )


def _has_overdue_receipt(db: Session, blogger_id: uuid.UUID, today: date) -> bool:
    return bool(
        db.scalar(
            select(PayoutRequest.id)
            .where(
                PayoutRequest.blogger_id == blogger_id,
                PayoutRequest.status == PayoutStatus.PAID,
                PayoutRequest.recipient_type == RecipientType.SELF_EMPLOYED,
                PayoutRequest.receipt_received_on.is_(None),
                PayoutRequest.receipt_due_date < today,
            )
            .limit(1)
        )
    )


def _get_payout_blogger_id(db: Session, payout_request_id: uuid.UUID) -> uuid.UUID:
    blogger_id = db.scalar(
        select(PayoutRequest.blogger_id).where(PayoutRequest.id == payout_request_id)
    )
    if not blogger_id:
        raise APIError(404, "PAYOUT_REQUEST_NOT_FOUND", "Payout request was not found")
    return blogger_id


def _lock_payout(db: Session, payout_request_id: uuid.UUID) -> PayoutRequest:
    payout = db.scalar(
        select(PayoutRequest)
        .where(PayoutRequest.id == payout_request_id)
        .with_for_update()
    )
    if not payout:
        raise APIError(404, "PAYOUT_REQUEST_NOT_FOUND", "Payout request was not found")
    return payout


def _lock_balance_then_payout(
    db: Session, payout_request_id: uuid.UUID
) -> tuple[CreatorBalance, PayoutRequest]:
    blogger_id = _get_payout_blogger_id(db, payout_request_id)
    balance = lock_creator_balance(db, blogger_id=blogger_id)
    payout = _lock_payout(db, payout_request_id)
    if payout.blogger_id != blogger_id:
        raise APIError(
            409,
            "PAYOUT_BALANCE_INCONSISTENT",
            "Payout request owner changed while the command was running",
        )
    return balance, payout


def _flush_command(
    db: Session,
    *,
    idempotency_key: uuid.UUID,
    action: str,
    actor_id: uuid.UUID,
    payout_request_id: uuid.UUID | None,
    payload: Any,
) -> PayoutCommandResponse | None:
    try:
        db.flush()
        return None
    except IntegrityError as error:
        return _recover_command_integrity(
            db,
            error=error,
            idempotency_key=idempotency_key,
            action=action,
            actor_id=actor_id,
            payout_request_id=payout_request_id,
            payload=payload,
        )


def _recover_command_integrity(
    db: Session,
    *,
    error: IntegrityError,
    idempotency_key: uuid.UUID,
    action: str,
    actor_id: uuid.UUID,
    payout_request_id: uuid.UUID | None,
    payload: Any,
) -> PayoutCommandResponse:
    if not _is_recoverable_command_integrity(error):
        raise error
    db.rollback()
    existing = _idempotent_result(
        db,
        idempotency_key=idempotency_key,
        action=action,
        actor_id=actor_id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if existing:
        return existing
    raise APIError(
        409,
        "PAYOUT_CONCURRENT_CHANGE",
        "Payout request changed concurrently; reload it and retry",
    ) from error


def _is_recoverable_command_integrity(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name in {
        "uq_payout_events_idempotency_key",
        "uq_payout_events_request_sequence",
        "uq_payout_requests_active_blogger",
    }:
        return True
    if constraint_name is not None:
        return False
    message = str(error.orig).lower()
    return any(
        marker in message
        for marker in (
            "unique constraint failed: payout_events.idempotency_key",
            "unique constraint failed: payout_events.payout_request_id, "
            "payout_events.sequence_number",
            "unique constraint failed: payout_requests.blogger_id",
        )
    )


def get_my_payout_details(db: Session, *, actor: User) -> PayoutDetails:
    if actor.role != Role.BLOGGER:
        raise APIError(403, "PAYOUT_BLOGGER_REQUIRED", "Only bloggers have payout details")
    details = db.get(PayoutDetails, actor.id)
    if not details:
        raise APIError(404, "PAYOUT_DETAILS_NOT_FOUND", "Payout details were not found")
    return details


def upsert_my_payout_details(
    db: Session,
    *,
    actor: User,
    payload: PayoutDetailsUpsertRequest,
    audit_context: AuditContext,
) -> PayoutDetails:
    locked_actor = _lock_actor(
        db, actor, {Role.BLOGGER}, "PAYOUT_DETAILS_PERMISSION_CHANGED"
    )
    details = db.scalar(
        select(PayoutDetails)
        .where(PayoutDetails.blogger_id == locked_actor.id)
        .with_for_update()
    )
    if details:
        details.sbp_phone = payload.sbp_phone
        details.bank_name = payload.bank_name
    else:
        details = PayoutDetails(
            blogger_id=locked_actor.id,
            sbp_phone=payload.sbp_phone,
            bank_name=payload.bank_name,
        )
        db.add(details)
    db.flush()
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PAYOUT_DETAILS_UPDATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="payout_details",
        object_id=locked_actor.id,
        metadata={"bank_specified": bool(details.bank_name)},
    )
    return details


def create_my_payout_request(
    db: Session,
    *,
    actor: User,
    payload: PayoutCreateRequest,
    audit_context: AuditContext,
) -> PayoutCommandResponse:
    locked_actor = _lock_actor(db, actor, {Role.BLOGGER}, "PAYOUT_PERMISSION_CHANGED")
    action = "requested"
    payload_hash = _command_hash(
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=None,
        payload=payload,
    )
    existing = _idempotent_result(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=None,
        payload=payload,
    )
    if existing:
        return existing

    profile = db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == locked_actor.id)
    )
    if not profile or profile.status != ProfileStatus.APPROVED:
        raise APIError(
            409,
            "PAYOUT_PROFILE_NOT_APPROVED",
            "An approved creator profile is required to request a payout",
        )
    if not profile.full_name or not profile.recipient_status:
        raise APIError(
            409,
            "PAYOUT_RECIPIENT_DATA_INCOMPLETE",
            "Recipient name and type must be completed before requesting a payout",
        )
    details = db.get(PayoutDetails, locked_actor.id)
    if not details or not details.sbp_phone:
        raise APIError(
            409,
            "PAYOUT_RECIPIENT_DATA_INCOMPLETE",
            "SBP payout details must be completed before requesting a payout",
        )
    balance = lock_creator_balance(db, blogger_id=locked_actor.id)
    today = moscow_today()
    if _has_overdue_receipt(db, locked_actor.id, today):
        raise APIError(
            409,
            "PAYOUT_BLOCKED_BY_OVERDUE_RECEIPT",
            "A receipt from an earlier self-employed payout is overdue",
        )
    active = db.scalar(
        select(PayoutRequest.id)
        .where(
            PayoutRequest.blogger_id == locked_actor.id,
            PayoutRequest.status.in_(ACTIVE_PAYOUT_STATUSES),
        )
        .limit(1)
    )
    if active:
        raise APIError(
            409,
            "PAYOUT_ALREADY_ACTIVE",
            "The blogger already has an active payout request",
        )
    if balance.available_kopecks <= 0:
        raise APIError(
            409,
            "NO_AVAILABLE_BALANCE",
            "There is no confirmed balance available for payout",
        )

    payout_id = uuid.uuid4()
    amount = balance.available_kopecks
    payout = PayoutRequest(
        id=payout_id,
        request_number=_request_number(payout_id, today),
        blogger_id=locked_actor.id,
        amount_kopecks=amount,
        currency="RUB",
        status=PayoutStatus.REQUESTED,
        recipient_full_name=profile.full_name,
        recipient_display_name=profile.display_name or profile.full_name,
        recipient_type=RecipientType(_enum_value(profile.recipient_status)),
        sbp_phone=details.sbp_phone,
        bank_name=details.bank_name,
        requested_at=utc_now(),
    )
    db.add(payout)
    try:
        db.flush([payout])
    except IntegrityError as error:
        return _recover_command_integrity(
            db,
            error=error,
            idempotency_key=payload.idempotency_key,
            action=action,
            actor_id=locked_actor.id,
            payout_request_id=None,
            payload=payload,
        )
    try:
        reserve_payout(
            db,
            blogger_id=locked_actor.id,
            payout_request_id=payout.id,
            amount_kopecks=amount,
        )
    except WalletInvariantError as error:
        raise _wallet_error(error) from error
    command_event = _add_event(
        db,
        payout=payout,
        action=action,
        actor_id=locked_actor.id,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        from_status=None,
        to_status=PayoutStatus.REQUESTED,
        metadata={"amount_kopecks": amount},
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PAYOUT_REQUESTED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="payout_request",
        object_id=payout.id,
        metadata={"amount_kopecks": amount, "request_number": payout.request_number},
    )
    concurrent = _flush_command(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=None,
        payload=payload,
    )
    if concurrent:
        return concurrent
    _notify_payout_status(db, payout)
    record_creator_activity(
        db,
        blogger_id=locked_actor.id,
        kind=ActivityKind.PAYOUT_REQUESTED,
        occurred_at=payout.requested_at,
    )
    return payout_command_response(payout, command_event)


def list_my_payout_requests(
    db: Session,
    *,
    actor: User,
    status: PayoutStatus | None,
    page: int,
    page_size: int,
) -> MyPayoutRequestListResponse:
    if actor.role != Role.BLOGGER:
        raise APIError(403, "PAYOUT_BLOGGER_REQUIRED", "Only bloggers have payout requests")
    filters = [PayoutRequest.blogger_id == actor.id]
    if status:
        filters.append(PayoutRequest.status == status)
    total = db.scalar(select(func.count()).select_from(PayoutRequest).where(*filters)) or 0
    items = list(
        db.scalars(
            select(PayoutRequest)
            .where(*filters)
            .order_by(PayoutRequest.requested_at.desc(), PayoutRequest.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    today = moscow_today()
    return MyPayoutRequestListResponse(
        items=[my_payout_response(item, today=today) for item in items],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


def get_my_payout_request(
    db: Session, *, actor: User, payout_request_id: uuid.UUID
) -> PayoutRequest:
    if actor.role != Role.BLOGGER:
        raise APIError(403, "PAYOUT_BLOGGER_REQUIRED", "Only bloggers have payout requests")
    payout = db.scalar(
        select(PayoutRequest).where(
            PayoutRequest.id == payout_request_id,
            PayoutRequest.blogger_id == actor.id,
        )
    )
    if not payout:
        raise APIError(404, "PAYOUT_REQUEST_NOT_FOUND", "Payout request was not found")
    return _decorate_payout(payout, today=moscow_today())


def _staff_filters(
    *,
    status: PayoutStatus | None = None,
    recipient_type: RecipientType | None = None,
    blogger_id: uuid.UUID | None = None,
    request_number: str | None = None,
    requested_from: date | None = None,
    requested_to: date | None = None,
    approved_from: date | None = None,
    approved_to: date | None = None,
    due_before: date | None = None,
    is_overdue: bool | None = None,
    receipt_status: ReceiptStatus | None = None,
    receipt_due_before: date | None = None,
    is_receipt_overdue: bool | None = None,
    today: date,
) -> list[Any]:
    filters: list[Any] = []
    if status:
        filters.append(PayoutRequest.status == status)
    if recipient_type:
        filters.append(PayoutRequest.recipient_type == recipient_type)
    if blogger_id:
        filters.append(PayoutRequest.blogger_id == blogger_id)
    if request_number:
        filters.append(PayoutRequest.request_number == request_number)
    if requested_from:
        filters.append(
            PayoutRequest.requested_at
            >= datetime.combine(requested_from, time.min, tzinfo=MOSCOW).astimezone(timezone.utc)
        )
    if requested_to:
        filters.append(
            PayoutRequest.requested_at
            < datetime.combine(
                requested_to + timedelta(days=1), time.min, tzinfo=MOSCOW
            ).astimezone(timezone.utc)
        )
    if approved_from:
        filters.append(
            PayoutRequest.approved_at
            >= datetime.combine(approved_from, time.min, tzinfo=MOSCOW).astimezone(
                timezone.utc
            )
        )
    if approved_to:
        filters.append(
            PayoutRequest.approved_at
            < datetime.combine(
                approved_to + timedelta(days=1), time.min, tzinfo=MOSCOW
            ).astimezone(timezone.utc)
        )
    if due_before:
        filters.append(PayoutRequest.payment_due_date <= due_before)
    overdue_clause = and_(
        PayoutRequest.status == PayoutStatus.APPROVED,
        PayoutRequest.payment_due_date < today,
    )
    if is_overdue is True:
        filters.append(overdue_clause)
    elif is_overdue is False:
        filters.append(not_(overdue_clause))
    receipt_overdue_clause = and_(
        PayoutRequest.recipient_type == RecipientType.SELF_EMPLOYED,
        PayoutRequest.status == PayoutStatus.PAID,
        PayoutRequest.receipt_received_on.is_(None),
        PayoutRequest.receipt_due_date < today,
    )
    if receipt_due_before:
        filters.append(PayoutRequest.receipt_due_date <= receipt_due_before)
    if is_receipt_overdue is True:
        filters.append(receipt_overdue_clause)
    elif is_receipt_overdue is False:
        filters.append(not_(receipt_overdue_clause))
    if receipt_status == ReceiptStatus.NOT_APPLICABLE:
        filters.append(
            or_(
                PayoutRequest.recipient_type != RecipientType.SELF_EMPLOYED,
                PayoutRequest.status == PayoutStatus.REJECTED,
            )
        )
    elif receipt_status == ReceiptStatus.PENDING_PAYMENT:
        filters.extend(
            (
                PayoutRequest.recipient_type == RecipientType.SELF_EMPLOYED,
                PayoutRequest.status.not_in(
                    (PayoutStatus.PAID, PayoutStatus.REJECTED)
                ),
                PayoutRequest.receipt_received_on.is_(None),
            )
        )
    elif receipt_status == ReceiptStatus.RECEIVED:
        filters.extend(
            (
                PayoutRequest.recipient_type == RecipientType.SELF_EMPLOYED,
                PayoutRequest.status == PayoutStatus.PAID,
                PayoutRequest.receipt_received_on.is_not(None),
            )
        )
    elif receipt_status == ReceiptStatus.OVERDUE:
        filters.append(receipt_overdue_clause)
    elif receipt_status == ReceiptStatus.AWAITING_RECEIPT:
        filters.extend(
            (
                PayoutRequest.recipient_type == RecipientType.SELF_EMPLOYED,
                PayoutRequest.status == PayoutStatus.PAID,
                PayoutRequest.receipt_received_on.is_(None),
                PayoutRequest.receipt_due_date >= today,
            )
        )
    return filters


def list_staff_payout_requests(
    db: Session,
    *,
    actor: User,
    status: PayoutStatus | None,
    recipient_type: RecipientType | None,
    blogger_id: uuid.UUID | None,
    request_number: str | None,
    requested_from: date | None,
    requested_to: date | None,
    approved_from: date | None,
    approved_to: date | None,
    due_before: date | None,
    is_overdue: bool | None,
    receipt_status: ReceiptStatus | None,
    receipt_due_before: date | None,
    is_receipt_overdue: bool | None,
    page: int,
    page_size: int,
) -> PayoutRequestListResponse:
    if actor.status != AccountStatus.ACTIVE or actor.role not in STAFF_PAYOUT_READER_ROLES:
        raise APIError(403, "PAYOUT_STAFF_REQUIRED", "Payout staff access is required")
    today = moscow_today()
    filters = _staff_filters(
        status=status,
        recipient_type=recipient_type,
        blogger_id=blogger_id,
        request_number=request_number,
        requested_from=requested_from,
        requested_to=requested_to,
        approved_from=approved_from,
        approved_to=approved_to,
        due_before=due_before,
        is_overdue=is_overdue,
        receipt_status=receipt_status,
        receipt_due_before=receipt_due_before,
        is_receipt_overdue=is_receipt_overdue,
        today=today,
    )
    if actor.role == Role.FINANCE:
        filters.append(PayoutRequest.status.in_(FINANCE_VISIBLE_PAYOUT_STATUSES))
    total = db.scalar(select(func.count()).select_from(PayoutRequest).where(*filters)) or 0
    items = list(
        db.scalars(
            select(PayoutRequest)
            .where(*filters)
            .order_by(PayoutRequest.requested_at.desc(), PayoutRequest.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return PayoutRequestListResponse(
        items=[payout_response(item, today=today) for item in items],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


def get_staff_payout_request(
    db: Session, *, actor: User, payout_request_id: uuid.UUID
) -> PayoutRequest:
    if actor.status != AccountStatus.ACTIVE or actor.role not in STAFF_PAYOUT_READER_ROLES:
        raise APIError(403, "PAYOUT_STAFF_REQUIRED", "Payout staff access is required")
    filters = [PayoutRequest.id == payout_request_id]
    if actor.role == Role.FINANCE:
        filters.append(PayoutRequest.status.in_(FINANCE_VISIBLE_PAYOUT_STATUSES))
    payout = db.scalar(select(PayoutRequest).where(*filters))
    if not payout:
        raise APIError(404, "PAYOUT_REQUEST_NOT_FOUND", "Payout request was not found")
    return _attach_history(db, payout, today=moscow_today())


def review_payout_request(
    db: Session,
    *,
    actor: User,
    payout_request_id: uuid.UUID,
    payload: PayoutReviewRequest,
    audit_context: AuditContext,
) -> PayoutCommandResponse:
    locked_actor = _lock_actor(
        db, actor, {Role.MANAGER, Role.ADMIN}, "PAYOUT_REVIEW_PERMISSION_CHANGED"
    )
    action = "review_started"
    payload_hash = _command_hash(
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    existing = _idempotent_result(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if existing:
        return existing
    payout = _lock_payout(db, payout_request_id)
    _forbid_self_processing(locked_actor, payout)
    if payout.status != PayoutStatus.REQUESTED:
        raise _state_conflict(payout, PayoutStatus.REQUESTED)
    payout.status = PayoutStatus.UNDER_REVIEW
    payout.review_started_at = utc_now()
    payout.reviewer_user_id = locked_actor.id
    if payload.comment:
        payout.manager_comment = payload.comment
    command_event = _add_event(
        db,
        payout=payout,
        action=action,
        actor_id=locked_actor.id,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        from_status=PayoutStatus.REQUESTED,
        to_status=PayoutStatus.UNDER_REVIEW,
        metadata={"comment_recorded": bool(payload.comment)},
        comment=payload.comment,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PAYOUT_REVIEW_STARTED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="payout_request",
        object_id=payout.id,
        metadata={"request_number": payout.request_number},
    )
    concurrent = _flush_command(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if concurrent:
        return concurrent
    _notify_payout_status(db, payout)
    return payout_command_response(payout, command_event)


def approve_payout_request(
    db: Session,
    *,
    actor: User,
    payout_request_id: uuid.UUID,
    payload: PayoutApprovalRequest,
    audit_context: AuditContext,
) -> PayoutCommandResponse:
    locked_actor = _lock_actor(
        db, actor, {Role.MANAGER, Role.ADMIN}, "PAYOUT_APPROVAL_PERMISSION_CHANGED"
    )
    action = "approved"
    payload_hash = _command_hash(
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    existing = _idempotent_result(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if existing:
        return existing
    balance, payout = _lock_balance_then_payout(db, payout_request_id)
    _forbid_self_processing(locked_actor, payout)
    if payout.status != PayoutStatus.UNDER_REVIEW:
        raise _state_conflict(payout, PayoutStatus.UNDER_REVIEW)
    if balance.reserved_kopecks != payout.amount_kopecks:
        raise APIError(
            409,
            "PAYOUT_BALANCE_INCONSISTENT",
            "Reserved balance does not match the payout amount",
        )
    if balance.available_kopecks < 0:
        raise APIError(
            409,
            "PAYOUT_BLOCKED_BY_NEGATIVE_BALANCE",
            "A negative balance correction must be resolved before approval",
        )
    if not payload.requisites_verified:
        raise APIError(
            409,
            "PAYMENT_DETAILS_NOT_VERIFIED",
            "Payout details must be verified before approval",
        )
    if (
        payout.recipient_type == RecipientType.SELF_EMPLOYED
        and payload.self_employment_verified is not True
    ):
        raise APIError(
            409,
            "SELF_EMPLOYED_STATUS_REQUIRED",
            "Self-employed status must be verified for every payout",
        )
    today = moscow_today()
    if _has_overdue_receipt(db, payout.blogger_id, today):
        raise APIError(
            409,
            "PAYOUT_BLOCKED_BY_OVERDUE_RECEIPT",
            "A receipt from an earlier self-employed payout is overdue",
        )
    now = utc_now()
    payout.status = PayoutStatus.APPROVED
    payout.requisites_verified_at = now
    payout.requisites_verified_by_user_id = locked_actor.id
    if payout.recipient_type == RecipientType.SELF_EMPLOYED:
        payout.self_employment_verified_at = now
        payout.self_employment_verified_by_user_id = locked_actor.id
    payout.approved_at = now
    payout.approved_by_user_id = locked_actor.id
    payout.payment_due_date = payment_due_date(now)
    if payload.comment:
        payout.manager_comment = payload.comment
    command_event = _add_event(
        db,
        payout=payout,
        action=action,
        actor_id=locked_actor.id,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        from_status=PayoutStatus.UNDER_REVIEW,
        to_status=PayoutStatus.APPROVED,
        metadata={
            "requisites_verified": True,
            "self_employment_verified": (
                True if payout.recipient_type == RecipientType.SELF_EMPLOYED else None
            ),
            "payment_due_date": payout.payment_due_date.isoformat(),
            "comment_recorded": bool(payload.comment),
        },
        comment=payload.comment,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PAYOUT_APPROVED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="payout_request",
        object_id=payout.id,
        metadata={
            "amount_kopecks": payout.amount_kopecks,
            "payment_due_date": payout.payment_due_date.isoformat(),
        },
    )
    concurrent = _flush_command(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if concurrent:
        return concurrent
    _notify_payout_status(db, payout)
    return payout_command_response(payout, command_event)


def reject_payout_request(
    db: Session,
    *,
    actor: User,
    payout_request_id: uuid.UUID,
    payload: PayoutRejectionRequest,
    audit_context: AuditContext,
) -> PayoutCommandResponse:
    locked_actor = _lock_actor(
        db,
        actor,
        {Role.MANAGER, Role.FINANCE, Role.ADMIN},
        "PAYOUT_REJECTION_PERMISSION_CHANGED",
    )
    action = "rejected"
    payload_hash = _command_hash(
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    existing = _idempotent_result(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if existing:
        return existing
    _, payout = _lock_balance_then_payout(db, payout_request_id)
    _forbid_self_processing(locked_actor, payout)
    if payout.status in {PayoutStatus.REQUESTED, PayoutStatus.UNDER_REVIEW}:
        if locked_actor.role not in {Role.MANAGER, Role.ADMIN}:
            raise APIError(
                403,
                "PAYOUT_REJECTION_PERMISSION_CHANGED",
                "Only a manager can reject a new or reviewed request",
            )
    elif payout.status == PayoutStatus.APPROVED:
        if locked_actor.role not in {Role.FINANCE, Role.ADMIN}:
            raise APIError(
                403,
                "PAYOUT_REJECTION_PERMISSION_CHANGED",
                "Only finance can reject an approved unpaid request",
            )
    else:
        raise _state_conflict(
            payout,
            PayoutStatus.REQUESTED,
            PayoutStatus.UNDER_REVIEW,
            PayoutStatus.APPROVED,
        )
    old_status = payout.status
    try:
        release_payout(
            db,
            blogger_id=payout.blogger_id,
            payout_request_id=payout.id,
            amount_kopecks=payout.amount_kopecks,
        )
    except WalletInvariantError as error:
        raise _wallet_error(error) from error
    payout.status = PayoutStatus.REJECTED
    payout.rejected_at = utc_now()
    payout.rejected_by_user_id = locked_actor.id
    payout.rejection_reason = payload.reason
    if payload.comment:
        payout.manager_comment = payload.comment
    command_event = _add_event(
        db,
        payout=payout,
        action=action,
        actor_id=locked_actor.id,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        from_status=old_status,
        to_status=PayoutStatus.REJECTED,
        metadata={
            "reason_recorded": True,
            "comment_recorded": bool(payload.comment),
        },
        comment=payload.comment,
        rejection_reason=payload.reason,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PAYOUT_REJECTED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="payout_request",
        object_id=payout.id,
        metadata={"amount_kopecks": payout.amount_kopecks},
    )
    concurrent = _flush_command(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if concurrent:
        return concurrent
    _notify_payout_status(db, payout)
    return payout_command_response(payout, command_event)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def record_payout_payment(
    db: Session,
    *,
    actor: User,
    payout_request_id: uuid.UUID,
    payload: PayoutPaymentRequest,
    audit_context: AuditContext,
) -> PayoutCommandResponse:
    locked_actor = _lock_actor(
        db, actor, {Role.FINANCE, Role.ADMIN}, "PAYOUT_PAYMENT_PERMISSION_CHANGED"
    )
    action = "paid"
    payload_hash = _command_hash(
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    existing = _idempotent_result(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if existing:
        return existing
    balance, payout = _lock_balance_then_payout(db, payout_request_id)
    _forbid_self_processing(locked_actor, payout)
    if payout.status != PayoutStatus.APPROVED:
        raise _state_conflict(payout, PayoutStatus.APPROVED)
    if balance.available_kopecks < 0:
        raise APIError(
            409,
            "PAYOUT_BLOCKED_BY_NEGATIVE_BALANCE",
            "A negative balance correction must be resolved before payment",
        )
    today = moscow_today()
    if payload.paid_on > today:
        raise APIError(422, "PAYMENT_DATE_INVALID", "Payment date cannot be in the future")
    if not payout.approved_at:
        raise APIError(
            409,
            "PAYOUT_HISTORY_INCONSISTENT",
            "Approved payout is missing its approval timestamp",
        )
    approved_on = _as_utc(payout.approved_at).astimezone(MOSCOW).date()
    if payload.paid_on < approved_on:
        raise APIError(
            422,
            "PAYMENT_DATE_INVALID",
            "Payment date cannot be earlier than approval date",
        )
    try:
        settle_payout(
            db,
            blogger_id=payout.blogger_id,
            payout_request_id=payout.id,
            amount_kopecks=payout.amount_kopecks,
        )
    except WalletInvariantError as error:
        raise _wallet_error(error) from error
    payout.status = PayoutStatus.PAID
    payout.paid_on = payload.paid_on
    payout.paid_recorded_at = utc_now()
    payout.paid_by_user_id = locked_actor.id
    payout.payment_reference = payload.payment_reference
    if payout.recipient_type == RecipientType.SELF_EMPLOYED:
        payout.receipt_due_date = receipt_due_date(payload.paid_on)
    command_event = _add_event(
        db,
        payout=payout,
        action=action,
        actor_id=locked_actor.id,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        from_status=PayoutStatus.APPROVED,
        to_status=PayoutStatus.PAID,
        metadata={
            "paid_on": payload.paid_on.isoformat(),
            "payment_reference_recorded": bool(payload.payment_reference),
            "receipt_due_date": (
                payout.receipt_due_date.isoformat() if payout.receipt_due_date else None
            ),
        },
        payment_reference=payload.payment_reference,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PAYOUT_PAID,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="payout_request",
        object_id=payout.id,
        metadata={
            "amount_kopecks": payout.amount_kopecks,
            "paid_on": payload.paid_on.isoformat(),
        },
    )
    concurrent = _flush_command(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if concurrent:
        return concurrent
    _notify_payout_status(db, payout)
    return payout_command_response(payout, command_event)


def record_payout_receipt(
    db: Session,
    *,
    actor: User,
    payout_request_id: uuid.UUID,
    payload: PayoutReceiptRequest,
    audit_context: AuditContext,
) -> PayoutCommandResponse:
    locked_actor = _lock_actor(
        db,
        actor,
        {Role.MANAGER, Role.FINANCE, Role.ADMIN},
        "PAYOUT_RECEIPT_PERMISSION_CHANGED",
    )
    action = "receipt_received"
    payload_hash = _command_hash(
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    existing = _idempotent_result(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    if existing:
        return existing
    payout = _lock_payout(db, payout_request_id)
    _forbid_self_processing(locked_actor, payout)
    if payout.status != PayoutStatus.PAID:
        raise _state_conflict(payout, PayoutStatus.PAID)
    if payout.recipient_type != RecipientType.SELF_EMPLOYED:
        raise APIError(409, "RECEIPT_NOT_APPLICABLE", "This payout does not require a receipt")
    if payout.receipt_received_on:
        raise APIError(409, "RECEIPT_ALREADY_RECORDED", "Receipt was already recorded")
    if not payout.paid_on:
        raise APIError(
            409,
            "PAYOUT_HISTORY_INCONSISTENT",
            "Paid payout is missing its payment date",
        )
    today = moscow_today()
    if payload.received_on > today or payload.received_on < payout.paid_on:
        raise APIError(
            422,
            "RECEIPT_DATE_INVALID",
            "Receipt date must be between the payment date and today",
        )
    payout.receipt_received_on = payload.received_on
    payout.receipt_recorded_at = utc_now()
    payout.receipt_received_by_user_id = locked_actor.id
    command_event = _add_event(
        db,
        payout=payout,
        action=action,
        actor_id=locked_actor.id,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        from_status=PayoutStatus.PAID,
        to_status=PayoutStatus.PAID,
        metadata={"received_on": payload.received_on.isoformat()},
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PAYOUT_RECEIPT_RECORDED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="payout_request",
        object_id=payout.id,
        metadata={"received_on": payload.received_on.isoformat()},
    )
    concurrent = _flush_command(
        db,
        idempotency_key=payload.idempotency_key,
        action=action,
        actor_id=locked_actor.id,
        payout_request_id=payout_request_id,
        payload=payload,
    )
    return concurrent or payout_command_response(payout, command_event)
