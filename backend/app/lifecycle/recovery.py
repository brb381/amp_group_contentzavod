import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.clock import utc_now
from app.creators.models import CreatorProfile, ProfileHistory, ProfileStatus
from app.database.locking import set_transaction_timeouts
from app.errors import APIError
from app.lifecycle.models import ActivityKind, CreatorLifecycle
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification
from app.support.models import (
    SupportCategory,
    SupportEventType,
    SupportStatus,
    SupportTicket,
    SupportTicketEvent,
)
from app.support.schemas import RecoveryDecisionRequest


RECOVERY_REVIEWER_ROLES = {Role.MODERATOR, Role.ADMIN}


def decide_account_recovery(
    db: Session,
    *,
    actor: User,
    ticket_id: uuid.UUID,
    payload: RecoveryDecisionRequest,
    audit_context: AuditContext,
) -> SupportTicketEvent:
    set_transaction_timeouts(db, lock_timeout_ms=5_000, statement_timeout_ms=30_000)
    locked_actor = db.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not locked_actor
        or locked_actor.status != AccountStatus.ACTIVE
        or locked_actor.role not in RECOVERY_REVIEWER_ROLES
    ):
        raise APIError(403, "RECOVERY_PERMISSION_CHANGED", "Recovery permissions changed")
    existing = db.scalar(
        select(SupportTicketEvent).where(
            SupportTicketEvent.actor_user_id == locked_actor.id,
            SupportTicketEvent.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        if (
            existing.ticket_id != ticket_id
            or existing.event_type != SupportEventType.RECOVERY_DECIDED
            or existing.recovery_decision != payload.decision
            or existing.reason != payload.reason
        ):
            raise APIError(409, "SUPPORT_IDEMPOTENCY_CONFLICT", "Idempotency key was reused")
        return existing
    ticket = db.scalar(
        select(SupportTicket).where(SupportTicket.id == ticket_id).with_for_update()
    )
    if not ticket or ticket.category != SupportCategory.ACCOUNT_RECOVERY:
        raise APIError(404, "SUPPORT_TICKET_NOT_FOUND", "Recovery ticket was not found")
    if ticket.status in {SupportStatus.RESOLVED, SupportStatus.CLOSED}:
        raise APIError(409, "RECOVERY_ALREADY_DECIDED", "Recovery ticket was already decided")
    blogger = db.scalar(
        select(User).where(User.id == ticket.blogger_id).with_for_update()
    )
    lifecycle = db.scalar(
        select(CreatorLifecycle)
        .where(CreatorLifecycle.blogger_id == ticket.blogger_id)
        .with_for_update()
    )
    if (
        not blogger
        or blogger.role != Role.BLOGGER
        or blogger.status != AccountStatus.SUSPENDED
        or not lifecycle
        or lifecycle.suspended_at is None
        or lifecycle.blocked_at is not None
    ):
        raise APIError(409, "ACCOUNT_NOT_RECOVERABLE", "Account cannot be recovered")
    now = utc_now()
    previous_status = ticket.status
    ticket.status = SupportStatus.RESOLVED
    ticket.resolved_at = now
    ticket.updated_at = now
    event = SupportTicketEvent(
        ticket_id=ticket.id,
        event_type=SupportEventType.RECOVERY_DECIDED,
        actor_user_id=locked_actor.id,
        from_status=previous_status,
        to_status=SupportStatus.RESOLVED,
        reason=payload.reason,
        recovery_decision=payload.decision,
        idempotency_key=payload.idempotency_key,
        payload_hash=hashlib.sha256(
            json.dumps(
                {
                    "actor_id": str(locked_actor.id),
                    "ticket_id": str(ticket.id),
                    "decision": payload.decision,
                    "reason": payload.reason,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        created_at=now,
    )
    db.add(event)
    if payload.decision == "approve":
        blogger.status = AccountStatus.ACTIVE
        blogger.status_before_block = None
        blogger.status_reason = payload.reason
        blogger.status_changed_at = now
        blogger.updated_at = now
        lifecycle.last_activity_at = now
        lifecycle.last_activity_kind = ActivityKind.ACCOUNT_RESTORED
        lifecycle.activity_revision += 1
        lifecycle.restored_at = now
        lifecycle.suspended_at = None
        lifecycle.blocked_at = None
        lifecycle.balance_claim_expired_at = None
        profile = db.scalar(
            select(CreatorProfile)
            .where(CreatorProfile.user_id == blogger.id)
            .with_for_update()
        )
        if profile and profile.status == ProfileStatus.SUSPENDED:
            previous_profile_status = profile.status
            profile.status = ProfileStatus.APPROVED
            profile.moderation_reason = None
            db.add(
                ProfileHistory(
                    profile_id=profile.id,
                    actor_user_id=locked_actor.id,
                    event_type="account_recovery_approved",
                    from_status=previous_profile_status.value,
                    to_status=profile.status.value,
                    reason=payload.reason,
                    changes={"support_ticket_id": str(ticket.id)},
                    created_at=now,
                )
            )
        template_code = "account_recovery_approved"
        action = AuditAction.ACCOUNT_RECOVERY_APPROVED
    else:
        template_code = "account_recovery_rejected"
        action = AuditAction.ACCOUNT_RECOVERY_REJECTED
    create_notification(
        db,
        NotificationCommand(
            recipient_user_id=blogger.id,
            template_code=template_code,
            context={},
            severity=(
                NotificationSeverity.INFO
                if payload.decision == "approve"
                else NotificationSeverity.ACTION_REQUIRED
            ),
            deduplication_key=f"recovery:{ticket.id}:{payload.decision}",
            related_object_type="support_ticket",
            related_object_id=ticket.id,
            action_path=f"/support-tickets/{ticket.id}",
        ),
    )
    record_event(
        db,
        context=audit_context,
        action=action,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="user",
        object_id=blogger.id,
        metadata={"ticket_id": str(ticket.id), "reason": payload.reason},
    )
    db.flush()
    return event
