import hashlib
import json
import math
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.clock import utc_now
from app.database.locking import set_transaction_timeouts
from app.errors import APIError
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification
from app.support.models import (
    SupportCategory,
    SupportEventType,
    SupportMessage,
    SupportStatus,
    SupportTicket,
    SupportTicketEvent,
)
from app.support.schemas import (
    MySupportEventResponse,
    MySupportMessageResponse,
    MySupportTicketDetailResponse,
    MySupportTicketListResponse,
    MySupportTicketResponse,
    StaffSupportEventResponse,
    StaffSupportMessageResponse,
    StaffSupportTicketDetailResponse,
    StaffSupportTicketListResponse,
    StaffSupportTicketResponse,
    SupportAssignmentRequest,
    SupportMessageCreateRequest,
    SupportStatusTransitionRequest,
    SupportTicketCreateRequest,
)


SUPPORT_STAFF_ROLES = {Role.MODERATOR, Role.MANAGER, Role.ADMIN}
GENERAL_SUPPORT_ROLES = {Role.MANAGER, Role.ADMIN}
RECOVERY_SUPPORT_ROLES = {Role.MODERATOR, Role.ADMIN}
SUPPORT_LOCK_TIMEOUT_MS = 5_000
SUPPORT_STATEMENT_TIMEOUT_MS = 30_000


ALLOWED_STATUS_TRANSITIONS = {
    SupportStatus.NEW: {
        SupportStatus.IN_PROGRESS,
        SupportStatus.WAITING_BLOGGER,
        SupportStatus.RESOLVED,
    },
    SupportStatus.IN_PROGRESS: {
        SupportStatus.WAITING_BLOGGER,
        SupportStatus.RESOLVED,
    },
    SupportStatus.WAITING_BLOGGER: {
        SupportStatus.IN_PROGRESS,
        SupportStatus.RESOLVED,
    },
    SupportStatus.RESOLVED: {SupportStatus.IN_PROGRESS, SupportStatus.CLOSED},
    SupportStatus.CLOSED: set(),
}


def _payload_hash(action: str, actor_id: uuid.UUID, ticket_id: uuid.UUID | None, payload: Any) -> str:
    canonical = json.dumps(
        {
            "action": action,
            "actor_id": str(actor_id),
            "ticket_id": str(ticket_id) if ticket_id else None,
            "payload": payload.model_dump(
                mode="json", exclude={"idempotency_key"}, exclude_none=True
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _lock_actor(db: Session, actor: User, *, allow_suspended_blogger: bool = False) -> User:
    set_transaction_timeouts(
        db,
        lock_timeout_ms=SUPPORT_LOCK_TIMEOUT_MS,
        statement_timeout_ms=SUPPORT_STATEMENT_TIMEOUT_MS,
    )
    locked = db.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    allowed_statuses = {AccountStatus.ACTIVE}
    if allow_suspended_blogger:
        allowed_statuses.add(AccountStatus.SUSPENDED)
    if not locked or locked.status not in allowed_statuses:
        raise APIError(403, "SUPPORT_PERMISSION_CHANGED", "Support permissions changed")
    return locked


def _staff_roles(category: SupportCategory) -> set[Role]:
    return RECOVERY_SUPPORT_ROLES if category == SupportCategory.ACCOUNT_RECOVERY else GENERAL_SUPPORT_ROLES


def _require_staff_access(actor: User, ticket: SupportTicket) -> None:
    if actor.role not in _staff_roles(ticket.category):
        raise APIError(404, "SUPPORT_TICKET_NOT_FOUND", "Support ticket was not found")


def _ticket_number(ticket_id: uuid.UUID, now: datetime) -> str:
    return f"SUP-{now:%Y%m%d}-{ticket_id.hex[:19].upper()}"


def _my_ticket(ticket: SupportTicket) -> MySupportTicketResponse:
    return MySupportTicketResponse.model_validate(ticket, from_attributes=True)


def _staff_ticket(ticket: SupportTicket) -> StaffSupportTicketResponse:
    return StaffSupportTicketResponse.model_validate(ticket, from_attributes=True)


def _my_message(message: SupportMessage) -> MySupportMessageResponse:
    return MySupportMessageResponse(
        id=message.id,
        author_type="blogger" if message.author_role == Role.BLOGGER.value else "staff",
        body=message.body,
        created_at=message.created_at,
    )


def _staff_message(message: SupportMessage) -> StaffSupportMessageResponse:
    return StaffSupportMessageResponse(
        **_my_message(message).model_dump(),
        author_user_id=message.author_user_id,
        author_role=message.author_role,
    )


def _my_event(event: SupportTicketEvent) -> MySupportEventResponse:
    return MySupportEventResponse(
        id=event.id,
        event_type=event.event_type,
        from_status=event.from_status,
        to_status=event.to_status,
        reason=(
            event.reason
            if event.event_type
            in {SupportEventType.STATUS_CHANGED, SupportEventType.RECOVERY_DECIDED}
            else None
        ),
        recovery_decision=event.recovery_decision,
        created_at=event.created_at,
    )


def _staff_event(event: SupportTicketEvent) -> StaffSupportEventResponse:
    response = _my_event(event).model_dump()
    response["reason"] = event.reason
    return StaffSupportEventResponse(
        **response,
        actor_user_id=event.actor_user_id,
        previous_assignee_user_id=event.previous_assignee_user_id,
        new_assignee_user_id=event.new_assignee_user_id,
    )


def my_message_response(message: SupportMessage) -> MySupportMessageResponse:
    return _my_message(message)


def staff_message_response(message: SupportMessage) -> StaffSupportMessageResponse:
    return _staff_message(message)


def staff_event_response(event: SupportTicketEvent) -> StaffSupportEventResponse:
    return _staff_event(event)


def _load_history(db: Session, ticket_id: uuid.UUID) -> tuple[list[SupportMessage], list[SupportTicketEvent]]:
    messages = list(
        db.scalars(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == ticket_id)
            .order_by(SupportMessage.created_at, SupportMessage.id)
        )
    )
    events = list(
        db.scalars(
            select(SupportTicketEvent)
            .where(SupportTicketEvent.ticket_id == ticket_id)
            .order_by(SupportTicketEvent.created_at, SupportTicketEvent.id)
        )
    )
    return messages, events


def _notify_staff_for_ticket(db: Session, ticket: SupportTicket, *, message_id: uuid.UUID | None = None) -> None:
    recipients = list(
        db.scalars(
            select(User).where(
                User.status == AccountStatus.ACTIVE,
                User.role.in_(_staff_roles(ticket.category)),
            )
        )
    )
    code = "support_ticket_created" if message_id is None else "support_blogger_message"
    discriminator = "created" if message_id is None else f"message:{message_id}"
    for recipient in recipients:
        create_notification(
            db,
            NotificationCommand(
                recipient_user_id=recipient.id,
                template_code=code,
                context={
                    "ticket_number": ticket.ticket_number,
                    "subject": ticket.subject,
                },
                severity=NotificationSeverity.ACTION_REQUIRED,
                deduplication_key=f"support:{ticket.id}:{discriminator}:{recipient.id}",
                related_object_type="support_ticket",
                related_object_id=ticket.id,
                action_path=f"/staff/support-tickets/{ticket.id}",
            ),
        )


def _notify_blogger(
    db: Session,
    ticket: SupportTicket,
    *,
    code: str,
    discriminator: str,
    severity: NotificationSeverity = NotificationSeverity.INFO,
) -> None:
    create_notification(
        db,
        NotificationCommand(
            recipient_user_id=ticket.blogger_id,
            template_code=code,
            context={
                "ticket_number": ticket.ticket_number,
                "subject": ticket.subject,
                "status": ticket.status.value,
            },
            severity=severity,
            deduplication_key=f"support:{ticket.id}:{discriminator}:{ticket.blogger_id}",
            related_object_type="support_ticket",
            related_object_id=ticket.id,
            action_path=f"/support-tickets/{ticket.id}",
        ),
    )


def create_my_ticket(
    db: Session,
    *,
    actor: User,
    payload: SupportTicketCreateRequest,
    audit_context: AuditContext,
) -> MySupportTicketResponse:
    locked_actor = _lock_actor(db, actor, allow_suspended_blogger=True)
    if locked_actor.role != Role.BLOGGER:
        raise APIError(403, "BLOGGER_REQUIRED", "Only bloggers can create support tickets")
    if locked_actor.status == AccountStatus.SUSPENDED and payload.category != SupportCategory.ACCOUNT_RECOVERY:
        raise APIError(409, "RECOVERY_TICKET_REQUIRED", "Suspended accounts may only request recovery")
    if locked_actor.status == AccountStatus.ACTIVE and payload.category == SupportCategory.ACCOUNT_RECOVERY:
        raise APIError(409, "ACCOUNT_NOT_SUSPENDED", "Account recovery is only available after suspension")

    payload_hash = _payload_hash("create", locked_actor.id, None, payload)
    existing = db.scalar(
        select(SupportTicket).where(
            SupportTicket.blogger_id == locked_actor.id,
            SupportTicket.creation_idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        if existing.creation_payload_hash != payload_hash:
            raise APIError(409, "SUPPORT_IDEMPOTENCY_CONFLICT", "Idempotency key was reused")
        return _my_ticket(existing)

    now = utc_now()
    ticket_id = uuid.uuid4()
    ticket = SupportTicket(
        id=ticket_id,
        ticket_number=_ticket_number(ticket_id, now),
        blogger_id=locked_actor.id,
        category=payload.category,
        subject=payload.subject,
        related_object_type=payload.related_object_type,
        related_object_id=payload.related_object_id,
        creation_idempotency_key=payload.idempotency_key,
        creation_payload_hash=payload_hash,
        created_at=now,
        updated_at=now,
        last_message_at=now,
    )
    message = SupportMessage(
        ticket_id=ticket.id,
        author_user_id=locked_actor.id,
        author_role=locked_actor.role.value,
        body=payload.body,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        created_at=now,
    )
    event = SupportTicketEvent(
        ticket_id=ticket.id,
        event_type=SupportEventType.CREATED,
        actor_user_id=locked_actor.id,
        to_status=SupportStatus.NEW,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        created_at=now,
    )
    db.add(ticket)
    db.flush([ticket])
    db.add_all([message, event])
    db.flush()

    _notify_staff_for_ticket(db, ticket)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.SUPPORT_TICKET_CREATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="support_ticket",
        object_id=ticket.id,
        metadata={"category": ticket.category.value},
    )
    db.flush()
    return _my_ticket(ticket)


def list_my_tickets(
    db: Session,
    *,
    actor: User,
    status: SupportStatus | None,
    page: int,
    page_size: int,
) -> MySupportTicketListResponse:
    conditions = [SupportTicket.blogger_id == actor.id]
    if status:
        conditions.append(SupportTicket.status == status)
    total = db.scalar(select(func.count()).select_from(SupportTicket).where(*conditions)) or 0
    tickets = list(
        db.scalars(
            select(SupportTicket)
            .where(*conditions)
            .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return MySupportTicketListResponse(
        items=[_my_ticket(ticket) for ticket in tickets],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


def get_my_ticket(db: Session, *, actor: User, ticket_id: uuid.UUID) -> MySupportTicketDetailResponse:
    ticket = db.scalar(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            SupportTicket.blogger_id == actor.id,
        )
    )
    if not ticket:
        raise APIError(404, "SUPPORT_TICKET_NOT_FOUND", "Support ticket was not found")
    messages, events = _load_history(db, ticket.id)
    return MySupportTicketDetailResponse(
        **_my_ticket(ticket).model_dump(),
        messages=[_my_message(message) for message in messages],
        history=[_my_event(event) for event in events],
    )


def list_staff_tickets(
    db: Session,
    *,
    actor: User,
    status: SupportStatus | None,
    category: SupportCategory | None,
    blogger_id: uuid.UUID | None,
    assignee_user_id: uuid.UUID | None,
    unassigned_only: bool,
    search: str | None,
    page: int,
    page_size: int,
) -> StaffSupportTicketListResponse:
    allowed_categories = (
        [SupportCategory.ACCOUNT_RECOVERY]
        if actor.role == Role.MODERATOR
        else list(SupportCategory)
        if actor.role == Role.ADMIN
        else [item for item in SupportCategory if item != SupportCategory.ACCOUNT_RECOVERY]
    )
    conditions = [SupportTicket.category.in_(allowed_categories)]
    if status:
        conditions.append(SupportTicket.status == status)
    if category:
        if category not in allowed_categories:
            raise APIError(403, "SUPPORT_CATEGORY_FORBIDDEN", "Support category is not available")
        conditions.append(SupportTicket.category == category)
    if blogger_id:
        conditions.append(SupportTicket.blogger_id == blogger_id)
    if assignee_user_id:
        conditions.append(SupportTicket.assigned_to_user_id == assignee_user_id)
    if unassigned_only:
        conditions.append(SupportTicket.assigned_to_user_id.is_(None))
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(SupportTicket.ticket_number.ilike(pattern), SupportTicket.subject.ilike(pattern))
        )
    total = db.scalar(select(func.count()).select_from(SupportTicket).where(*conditions)) or 0
    tickets = list(
        db.scalars(
            select(SupportTicket)
            .where(*conditions)
            .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return StaffSupportTicketListResponse(
        items=[_staff_ticket(ticket) for ticket in tickets],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


def get_staff_ticket(db: Session, *, actor: User, ticket_id: uuid.UUID) -> StaffSupportTicketDetailResponse:
    ticket = db.get(SupportTicket, ticket_id)
    if not ticket:
        raise APIError(404, "SUPPORT_TICKET_NOT_FOUND", "Support ticket was not found")
    _require_staff_access(actor, ticket)
    messages, events = _load_history(db, ticket.id)
    return StaffSupportTicketDetailResponse(
        **_staff_ticket(ticket).model_dump(),
        messages=[_staff_message(message) for message in messages],
        history=[_staff_event(event) for event in events],
    )


def _lock_ticket(db: Session, ticket_id: uuid.UUID) -> SupportTicket:
    ticket = db.scalar(
        select(SupportTicket).where(SupportTicket.id == ticket_id).with_for_update()
    )
    if not ticket:
        raise APIError(404, "SUPPORT_TICKET_NOT_FOUND", "Support ticket was not found")
    return ticket


def _record_status_change(
    db: Session,
    *,
    ticket: SupportTicket,
    actor: User,
    to_status: SupportStatus,
    idempotency_key: uuid.UUID,
    payload_hash: str,
    reason: str | None,
    now: datetime,
) -> SupportTicketEvent:
    previous = ticket.status
    ticket.status = to_status
    ticket.updated_at = now
    if to_status == SupportStatus.RESOLVED:
        ticket.resolved_at = now
        ticket.closed_at = None
    elif to_status == SupportStatus.CLOSED:
        ticket.closed_at = now
    elif previous == SupportStatus.RESOLVED:
        ticket.resolved_at = None
        ticket.closed_at = None
    event = SupportTicketEvent(
        ticket_id=ticket.id,
        event_type=SupportEventType.STATUS_CHANGED,
        actor_user_id=actor.id,
        from_status=previous,
        to_status=to_status,
        reason=reason,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        created_at=now,
    )
    db.add(event)
    return event


def post_ticket_message(
    db: Session,
    *,
    actor: User,
    ticket_id: uuid.UUID,
    payload: SupportMessageCreateRequest,
    is_staff: bool,
    audit_context: AuditContext,
) -> SupportMessage:
    locked_actor = _lock_actor(db, actor, allow_suspended_blogger=not is_staff)
    payload_hash = _payload_hash("message", locked_actor.id, ticket_id, payload)
    existing = db.scalar(
        select(SupportMessage).where(
            SupportMessage.author_user_id == locked_actor.id,
            SupportMessage.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        if existing.ticket_id != ticket_id or existing.payload_hash != payload_hash:
            raise APIError(409, "SUPPORT_IDEMPOTENCY_CONFLICT", "Idempotency key was reused")
        return existing

    ticket = _lock_ticket(db, ticket_id)
    if is_staff:
        _require_staff_access(locked_actor, ticket)
    elif ticket.blogger_id != locked_actor.id:
        raise APIError(404, "SUPPORT_TICKET_NOT_FOUND", "Support ticket was not found")
    if ticket.status == SupportStatus.CLOSED:
        raise APIError(409, "SUPPORT_TICKET_CLOSED", "Closed support ticket cannot receive messages")
    if is_staff and ticket.status == SupportStatus.RESOLVED:
        raise APIError(409, "SUPPORT_TICKET_RESOLVED", "Reopen the ticket before replying")

    now = utc_now()
    message = SupportMessage(
        ticket_id=ticket.id,
        author_user_id=locked_actor.id,
        author_role=locked_actor.role.value,
        body=payload.body,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        created_at=now,
    )
    db.add(message)
    ticket.last_message_at = now
    ticket.updated_at = now
    if is_staff and ticket.assigned_to_user_id is None:
        ticket.assigned_to_user_id = locked_actor.id
        db.add(
            SupportTicketEvent(
                ticket_id=ticket.id,
                event_type=SupportEventType.ASSIGNED,
                actor_user_id=locked_actor.id,
                previous_assignee_user_id=None,
                new_assignee_user_id=locked_actor.id,
                reason="auto_assigned_on_reply",
                idempotency_key=uuid.uuid5(
                    payload.idempotency_key, "support:auto_assignment"
                ),
                payload_hash=payload_hash,
                created_at=now,
            )
        )
    target_status = None
    if is_staff and ticket.status in {SupportStatus.NEW, SupportStatus.IN_PROGRESS}:
        target_status = SupportStatus.WAITING_BLOGGER
    elif not is_staff and ticket.status in {SupportStatus.WAITING_BLOGGER, SupportStatus.RESOLVED}:
        target_status = SupportStatus.IN_PROGRESS
    if target_status:
        _record_status_change(
            db,
            ticket=ticket,
            actor=locked_actor,
            to_status=target_status,
            idempotency_key=payload.idempotency_key,
            payload_hash=payload_hash,
            reason=None,
            now=now,
        )
    db.flush()
    if is_staff:
        _notify_blogger(
            db,
            ticket,
            code="support_staff_message",
            discriminator=f"message:{message.id}",
            severity=NotificationSeverity.ACTION_REQUIRED,
        )
    elif ticket.assigned_to_user_id:
        create_notification(
            db,
            NotificationCommand(
                recipient_user_id=ticket.assigned_to_user_id,
                template_code="support_blogger_message",
                context={"ticket_number": ticket.ticket_number, "subject": ticket.subject},
                severity=NotificationSeverity.ACTION_REQUIRED,
                deduplication_key=f"support:{ticket.id}:message:{message.id}:{ticket.assigned_to_user_id}",
                related_object_type="support_ticket",
                related_object_id=ticket.id,
                action_path=f"/staff/support-tickets/{ticket.id}",
            ),
        )
    else:
        _notify_staff_for_ticket(db, ticket, message_id=message.id)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.SUPPORT_MESSAGE_CREATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="support_ticket",
        object_id=ticket.id,
        metadata={"author_type": "staff" if is_staff else "blogger"},
    )
    db.flush()
    return message


def assign_ticket(
    db: Session,
    *,
    actor: User,
    ticket_id: uuid.UUID,
    payload: SupportAssignmentRequest,
    audit_context: AuditContext,
) -> SupportTicketEvent:
    locked_actor = _lock_actor(db, actor)
    payload_hash = _payload_hash("assignment", locked_actor.id, ticket_id, payload)
    existing = db.scalar(
        select(SupportTicketEvent).where(
            SupportTicketEvent.actor_user_id == locked_actor.id,
            SupportTicketEvent.idempotency_key == payload.idempotency_key
        )
    )
    if existing:
        if existing.ticket_id != ticket_id or existing.payload_hash != payload_hash:
            raise APIError(409, "SUPPORT_IDEMPOTENCY_CONFLICT", "Idempotency key was reused")
        return existing
    ticket = _lock_ticket(db, ticket_id)
    _require_staff_access(locked_actor, ticket)
    assignee = None
    if payload.assignee_user_id:
        assignee = db.scalar(
            select(User).where(User.id == payload.assignee_user_id).with_for_update()
        )
        if not assignee or assignee.status != AccountStatus.ACTIVE or assignee.role not in _staff_roles(ticket.category):
            raise APIError(422, "INVALID_SUPPORT_ASSIGNEE", "Assignee cannot process this category")
    now = utc_now()
    previous = ticket.assigned_to_user_id
    ticket.assigned_to_user_id = assignee.id if assignee else None
    ticket.updated_at = now
    event = SupportTicketEvent(
        ticket_id=ticket.id,
        event_type=SupportEventType.ASSIGNED,
        actor_user_id=locked_actor.id,
        previous_assignee_user_id=previous,
        new_assignee_user_id=ticket.assigned_to_user_id,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        created_at=now,
    )
    db.add(event)
    db.flush()
    if assignee and assignee.id != locked_actor.id:
        create_notification(
            db,
            NotificationCommand(
                recipient_user_id=assignee.id,
                template_code="support_ticket_assigned",
                context={"ticket_number": ticket.ticket_number, "subject": ticket.subject},
                severity=NotificationSeverity.ACTION_REQUIRED,
                deduplication_key=f"support:{ticket.id}:assignment:{event.id}:{assignee.id}",
                related_object_type="support_ticket",
                related_object_id=ticket.id,
                action_path=f"/staff/support-tickets/{ticket.id}",
            ),
        )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.SUPPORT_TICKET_ASSIGNED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="support_ticket",
        object_id=ticket.id,
        metadata={"assigned": assignee is not None},
    )
    return event


def transition_ticket_status(
    db: Session,
    *,
    actor: User,
    ticket_id: uuid.UUID,
    payload: SupportStatusTransitionRequest,
    audit_context: AuditContext,
) -> SupportTicketEvent:
    locked_actor = _lock_actor(db, actor)
    payload_hash = _payload_hash("status", locked_actor.id, ticket_id, payload)
    existing = db.scalar(
        select(SupportTicketEvent).where(
            SupportTicketEvent.actor_user_id == locked_actor.id,
            SupportTicketEvent.idempotency_key == payload.idempotency_key
        )
    )
    if existing:
        if existing.ticket_id != ticket_id or existing.payload_hash != payload_hash:
            raise APIError(409, "SUPPORT_IDEMPOTENCY_CONFLICT", "Idempotency key was reused")
        return existing
    ticket = _lock_ticket(db, ticket_id)
    _require_staff_access(locked_actor, ticket)
    if (
        ticket.category == SupportCategory.ACCOUNT_RECOVERY
        and payload.to_status in {SupportStatus.RESOLVED, SupportStatus.CLOSED}
    ):
        raise APIError(
            409,
            "RECOVERY_DECISION_REQUIRED",
            "Use the recovery decision command for an account recovery ticket",
        )
    if payload.to_status not in ALLOWED_STATUS_TRANSITIONS[ticket.status]:
        raise APIError(
            409,
            "SUPPORT_STATUS_CONFLICT",
            "Requested support status transition is not allowed",
            details={"current_status": ticket.status.value},
        )
    now = utc_now()
    event = _record_status_change(
        db,
        ticket=ticket,
        actor=locked_actor,
        to_status=payload.to_status,
        idempotency_key=payload.idempotency_key,
        payload_hash=payload_hash,
        reason=payload.reason,
        now=now,
    )
    db.flush()
    _notify_blogger(
        db,
        ticket,
        code="support_status_changed",
        discriminator=f"status:{event.id}",
        severity=(
            NotificationSeverity.ACTION_REQUIRED
            if ticket.status == SupportStatus.WAITING_BLOGGER
            else NotificationSeverity.INFO
        ),
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.SUPPORT_STATUS_CHANGED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="support_ticket",
        object_id=ticket.id,
        metadata={"from_status": event.from_status.value, "to_status": event.to_status.value},
    )
    return event
