import hashlib
import json
import math
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.account_deletion.models import (
    AccountDeletionEvent,
    AccountDeletionRequest,
    DeletionEventAction,
    DeletionRequestStatus,
)
from app.account_deletion.schemas import (
    AccountDeletionCancellationRequest,
    AccountDeletionConfirmationRequest,
    AccountDeletionCreateRequest,
    AccountDeletionListResponse,
    AccountDeletionResponse,
)
from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, RefreshSession, Role, User
from app.auth.security import verify_password
from app.billing.models import (
    CalculationPeriod,
    CalculationPeriodStatus,
    CreatorBalance,
    CreatorPeriodTotal,
)
from app.clock import utc_now
from app.config import Settings
from app.content.models import (
    Publication,
    PublicationEnrichmentStatus,
    PublicationHistory,
    PublicationStatus,
    VideoCard,
)
from app.creators.models import (
    CreatorProfile,
    ProfileHistory,
    ProfileStatus,
    SocialAccount,
    SocialAccountHistory,
)
from app.database.locking import set_transaction_timeouts
from app.errors import APIError
from app.lifecycle.models import LifecycleJob, LifecycleJobState
from app.outbox.models import OutboxEvent
from app.payouts.models import PayoutRequest, PayoutStatus, RecipientType
from app.readings.models import YouTubeViewCollectionJob
from app.readings.revision import lock_reading_dataset_revision
from app.youtube.models import YouTubeEnrichmentJob


LOCK_TIMEOUT_MS = 5_000
STATEMENT_TIMEOUT_MS = 30_000


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(f"account-deletion:{token}".encode()).hexdigest()


def _payload_hash(action: str, actor_id: uuid.UUID, request_id: uuid.UUID | None) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "action": action,
                "actor_id": str(actor_id),
                "request_id": str(request_id) if request_id else None,
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _lock_blogger(db: Session, blogger_id: uuid.UUID) -> User:
    blogger = db.scalar(
        select(User)
        .where(User.id == blogger_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not blogger or blogger.role != Role.BLOGGER:
        raise APIError(404, "ACCOUNT_NOT_FOUND", "Account was not found")
    return blogger


def _expire_request(
    db: Session, request: AccountDeletionRequest, blogger_id: uuid.UUID, now: datetime
) -> None:
    if (
        request.status == DeletionRequestStatus.AWAITING_CONFIRMATION
        and _aware(request.expires_at) <= now
    ):
        request.status = DeletionRequestStatus.EXPIRED
        db.add(
            AccountDeletionEvent(
                request_id=request.id,
                action=DeletionEventAction.EXPIRED,
                actor_user_id=blogger_id,
                idempotency_key=uuid.uuid5(uuid.NAMESPACE_URL, f"deletion-expired:{request.id}"),
                payload_hash=_payload_hash("expired", blogger_id, request.id),
                created_at=now,
            )
        )
        db.flush()


def create_deletion_request(
    db: Session,
    *,
    actor: User,
    payload: AccountDeletionCreateRequest,
    settings: Settings,
    audit_context: AuditContext,
) -> AccountDeletionRequest:
    set_transaction_timeouts(
        db, lock_timeout_ms=LOCK_TIMEOUT_MS, statement_timeout_ms=STATEMENT_TIMEOUT_MS
    )
    blogger = _lock_blogger(db, actor.id)
    if blogger.status not in {AccountStatus.ACTIVE, AccountStatus.SUSPENDED}:
        raise APIError(409, "ACCOUNT_DELETION_UNAVAILABLE", "Account cannot be deleted")
    existing = db.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.blogger_id == blogger.id,
            AccountDeletionRequest.creation_idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        return existing
    if not verify_password(payload.password, blogger.password_hash):
        raise APIError(403, "ACCOUNT_DELETION_PASSWORD_INVALID", "Password is invalid")
    now = utc_now()
    active = db.scalar(
        select(AccountDeletionRequest)
        .where(
            AccountDeletionRequest.blogger_id == blogger.id,
            AccountDeletionRequest.status == DeletionRequestStatus.AWAITING_CONFIRMATION,
        )
        .with_for_update()
    )
    if active:
        _expire_request(db, active, blogger.id, now)
        if active.status == DeletionRequestStatus.AWAITING_CONFIRMATION:
            raise APIError(
                409,
                "ACCOUNT_DELETION_ALREADY_REQUESTED",
                "An account deletion confirmation is already pending",
                {"request_id": str(active.id), "expires_at": active.expires_at.isoformat()},
            )
    raw_token = secrets.token_urlsafe(32)
    deletion = AccountDeletionRequest(
        blogger_id=blogger.id,
        creation_idempotency_key=payload.idempotency_key,
        creation_payload_hash=_payload_hash("requested", blogger.id, None),
        confirmation_token_hash=_token_hash(raw_token),
        requested_at=now,
        expires_at=now + timedelta(hours=settings.account_deletion_confirmation_ttl_hours),
    )
    db.add(deletion)
    db.flush()
    db.add(
        AccountDeletionEvent(
            request_id=deletion.id,
            action=DeletionEventAction.REQUESTED,
            actor_user_id=blogger.id,
            idempotency_key=payload.idempotency_key,
            payload_hash=_payload_hash("requested", blogger.id, deletion.id),
            created_at=now,
        )
    )
    link = (
        f"{settings.frontend_url.rstrip('/')}/confirm-account-deletion"
        f"#request_id={deletion.id}&token={raw_token}"
    )
    db.add(
        OutboxEvent(
            event_type="email_delivery_requested",
            payload={
                "recipient": blogger.email,
                "subject": "Confirm account deletion",
                "body": (
                    "Confirm account deletion within "
                    f"{settings.account_deletion_confirmation_ttl_hours} hours: {link}"
                ),
            },
            correlation_type="account_deletion_request",
            correlation_id=deletion.id,
        )
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.ACCOUNT_DELETION_REQUESTED,
        actor_user_id=blogger.id,
        actor_role=blogger.role.value,
        object_type="account_deletion_request",
        object_id=deletion.id,
    )
    return deletion


def _check_financial_obligations(db: Session, blogger_id: uuid.UUID) -> None:
    balance = db.scalar(
        select(CreatorBalance)
        .where(CreatorBalance.blogger_id == blogger_id)
        .with_for_update()
    )
    if balance and (balance.available_kopecks != 0 or balance.reserved_kopecks != 0):
        raise APIError(
            409,
            "ACCOUNT_DELETION_BALANCE_NOT_SETTLED",
            "Withdraw or settle the creator balance before deleting the account",
        )
    active_payout = db.scalar(
        select(PayoutRequest.id)
        .where(
            PayoutRequest.blogger_id == blogger_id,
            PayoutRequest.status.in_(
                (PayoutStatus.REQUESTED, PayoutStatus.UNDER_REVIEW, PayoutStatus.APPROVED)
            ),
        )
        .limit(1)
        .with_for_update()
    )
    if active_payout:
        raise APIError(
            409,
            "ACCOUNT_DELETION_PAYOUT_IN_PROGRESS",
            "Complete the active payout request before deleting the account",
        )
    missing_receipt = db.scalar(
        select(PayoutRequest.id)
        .where(
            PayoutRequest.blogger_id == blogger_id,
            PayoutRequest.status == PayoutStatus.PAID,
            PayoutRequest.recipient_type == RecipientType.SELF_EMPLOYED,
            PayoutRequest.receipt_received_on.is_(None),
        )
        .limit(1)
        .with_for_update()
    )
    if missing_receipt:
        raise APIError(
            409,
            "ACCOUNT_DELETION_RECEIPT_REQUIRED",
            "Submit the required payout receipt before deleting the account",
        )
    unsettled_earnings = db.scalar(
        select(CreatorPeriodTotal.id)
        .join(CalculationPeriod, CalculationPeriod.id == CreatorPeriodTotal.period_id)
        .where(
            CreatorPeriodTotal.blogger_id == blogger_id,
            CalculationPeriod.status != CalculationPeriodStatus.CONFIRMED,
            CreatorPeriodTotal.amount_kopecks + CreatorPeriodTotal.adjustment_kopecks != 0,
        )
        .limit(1)
    )
    if unsettled_earnings:
        raise APIError(
            409,
            "ACCOUNT_DELETION_EARNINGS_NOT_SETTLED",
            "Wait until preliminary earnings are finalized before deleting the account",
        )


def _soft_delete_creator_data(db: Session, blogger: User, now: datetime) -> None:
    profile = db.scalar(
        select(CreatorProfile)
        .where(CreatorProfile.user_id == blogger.id)
        .with_for_update()
    )
    if profile and profile.status != ProfileStatus.DELETED:
        previous_status = profile.status
        profile.status = ProfileStatus.DELETED
        profile.moderation_reason = "account_deleted_by_creator"
        db.add(
            ProfileHistory(
                profile_id=profile.id,
                actor_user_id=blogger.id,
                event_type="account_deleted_by_creator",
                from_status=previous_status.value,
                to_status=ProfileStatus.DELETED.value,
                reason="account_deleted_by_creator",
                changes={},
                created_at=now,
            )
        )
    publications = list(
        db.scalars(
            select(Publication)
            .join(VideoCard, VideoCard.id == Publication.video_card_id)
            .where(
                VideoCard.blogger_id == blogger.id,
                Publication.deleted_at.is_(None),
                Publication.status != PublicationStatus.INACTIVE,
            )
            .order_by(Publication.id)
            .with_for_update()
        )
    )
    publication_ids = [publication.id for publication in publications]
    for publication in publications:
        previous_status = publication.status
        publication.status = PublicationStatus.INACTIVE
        publication.enrichment_status = PublicationEnrichmentStatus.NOT_REQUESTED
        publication.moderation_reason = "account_deleted_by_creator"
        publication.updated_at = now
        db.add(
            PublicationHistory(
                publication_id=publication.id,
                actor_user_id=blogger.id,
                event_type="account_deleted_by_creator",
                from_status=previous_status.value,
                to_status=PublicationStatus.INACTIVE.value,
                reason="account_deleted_by_creator",
                changes={},
                created_at=now,
            )
        )
    if publication_ids:
        for job_model in (YouTubeEnrichmentJob, YouTubeViewCollectionJob):
            db.execute(
                update(job_model)
                .where(
                    job_model.publication_id.in_(publication_ids),
                    job_model.state.in_(("pending", "queued", "processing", "retry_wait")),
                )
                .values(
                    state="failed",
                    lease_until=None,
                    dispatch_id=None,
                    last_error_code="account_deleted",
                )
            )
    accounts = list(
        db.scalars(
            select(SocialAccount)
            .where(SocialAccount.user_id == blogger.id, SocialAccount.deleted_at.is_(None))
            .order_by(SocialAccount.id)
            .with_for_update()
        )
    )
    for account in accounts:
        account.deleted_at = now
        db.add(
            SocialAccountHistory(
                social_account_id=account.id,
                actor_user_id=blogger.id,
                event_type="account_deleted_by_creator",
                from_status=account.status.value,
                to_status=account.status.value,
                reason="account_deleted_by_creator",
                created_at=now,
            )
        )
    db.execute(
        update(LifecycleJob)
        .where(
            LifecycleJob.blogger_id == blogger.id,
            LifecycleJob.state.in_(
                (
                    LifecycleJobState.PENDING,
                    LifecycleJobState.QUEUED,
                    LifecycleJobState.PROCESSING,
                    LifecycleJobState.RETRY_WAIT,
                )
            ),
        )
        .values(
            state=LifecycleJobState.OBSOLETE,
            lease_until=None,
            dispatch_id=None,
            last_error_code="account_deleted",
        )
    )
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == blogger.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def confirm_deletion_request(
    db: Session,
    *,
    payload: AccountDeletionConfirmationRequest,
    request_id: uuid.UUID,
    audit_context: AuditContext,
) -> AccountDeletionRequest:
    set_transaction_timeouts(
        db, lock_timeout_ms=LOCK_TIMEOUT_MS, statement_timeout_ms=STATEMENT_TIMEOUT_MS
    )
    candidate = db.execute(
        select(AccountDeletionRequest.blogger_id).where(AccountDeletionRequest.id == request_id)
    ).one_or_none()
    if not candidate:
        raise APIError(404, "ACCOUNT_DELETION_REQUEST_NOT_FOUND", "Deletion request was not found")
    blogger = _lock_blogger(db, candidate.blogger_id)
    existing = db.scalar(
        select(AccountDeletionEvent).where(
            AccountDeletionEvent.actor_user_id == blogger.id,
            AccountDeletionEvent.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        if existing.request_id != request_id or existing.action != DeletionEventAction.COMPLETED:
            raise APIError(409, "ACCOUNT_DELETION_IDEMPOTENCY_CONFLICT", "Idempotency key was reused")
        return db.get(AccountDeletionRequest, request_id)
    deletion = db.scalar(
        select(AccountDeletionRequest)
        .where(AccountDeletionRequest.id == request_id)
        .with_for_update()
    )
    now = utc_now()
    if deletion.status == DeletionRequestStatus.COMPLETED:
        raise APIError(409, "ACCOUNT_ALREADY_DELETED", "Account is already deleted")
    _expire_request(db, deletion, blogger.id, now)
    if deletion.status != DeletionRequestStatus.AWAITING_CONFIRMATION:
        raise APIError(409, "ACCOUNT_DELETION_NOT_CONFIRMABLE", "Deletion request cannot be confirmed")
    if not secrets.compare_digest(deletion.confirmation_token_hash, _token_hash(payload.token)):
        raise APIError(400, "ACCOUNT_DELETION_TOKEN_INVALID", "Deletion token is invalid or expired")
    if blogger.status not in {
        AccountStatus.ACTIVE,
        AccountStatus.SUSPENDED,
        AccountStatus.BLOCKED,
    }:
        raise APIError(409, "ACCOUNT_DELETION_UNAVAILABLE", "Account cannot be deleted")
    lock_reading_dataset_revision(db)
    _check_financial_obligations(db, blogger.id)
    _soft_delete_creator_data(db, blogger, now)
    deletion.status = DeletionRequestStatus.COMPLETED
    deletion.completed_at = now
    blogger.status = AccountStatus.DELETED
    blogger.status_before_block = None
    blogger.status_reason = "deleted_by_creator"
    blogger.status_changed_at = now
    blogger.collaboration_ended_at = now
    blogger.deleted_at = now
    blogger.updated_at = now
    db.add(
        AccountDeletionEvent(
            request_id=deletion.id,
            action=DeletionEventAction.COMPLETED,
            actor_user_id=blogger.id,
            idempotency_key=payload.idempotency_key,
            payload_hash=_payload_hash("completed", blogger.id, deletion.id),
            created_at=now,
        )
    )
    db.add(
        OutboxEvent(
            event_type="email_delivery_requested",
            payload={
                "recipient": blogger.email,
                "subject": "Account deleted",
                "body": "Your AMP Content Factory account has been deleted.",
            },
            correlation_type="account_deletion_request",
            correlation_id=deletion.id,
        )
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.ACCOUNT_DELETION_COMPLETED,
        actor_user_id=blogger.id,
        actor_role=blogger.role.value,
        object_type="account_deletion_request",
        object_id=deletion.id,
    )
    db.flush()
    return deletion


def cancel_deletion_request(
    db: Session,
    *,
    actor: User,
    request_id: uuid.UUID,
    payload: AccountDeletionCancellationRequest,
    audit_context: AuditContext,
) -> AccountDeletionRequest:
    blogger = _lock_blogger(db, actor.id)
    existing = db.scalar(
        select(AccountDeletionEvent).where(
            AccountDeletionEvent.actor_user_id == blogger.id,
            AccountDeletionEvent.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        if existing.request_id != request_id or existing.action != DeletionEventAction.CANCELLED:
            raise APIError(409, "ACCOUNT_DELETION_IDEMPOTENCY_CONFLICT", "Idempotency key was reused")
        return db.get(AccountDeletionRequest, request_id)
    deletion = db.scalar(
        select(AccountDeletionRequest)
        .where(
            AccountDeletionRequest.id == request_id,
            AccountDeletionRequest.blogger_id == blogger.id,
        )
        .with_for_update()
    )
    if not deletion:
        raise APIError(404, "ACCOUNT_DELETION_REQUEST_NOT_FOUND", "Deletion request was not found")
    now = utc_now()
    _expire_request(db, deletion, blogger.id, now)
    if deletion.status != DeletionRequestStatus.AWAITING_CONFIRMATION:
        raise APIError(409, "ACCOUNT_DELETION_NOT_CANCELLABLE", "Deletion request cannot be cancelled")
    deletion.status = DeletionRequestStatus.CANCELLED
    deletion.cancelled_at = now
    db.add(
        AccountDeletionEvent(
            request_id=deletion.id,
            action=DeletionEventAction.CANCELLED,
            actor_user_id=blogger.id,
            idempotency_key=payload.idempotency_key,
            payload_hash=_payload_hash("cancelled", blogger.id, deletion.id),
            created_at=now,
        )
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.ACCOUNT_DELETION_CANCELLED,
        actor_user_id=blogger.id,
        actor_role=blogger.role.value,
        object_type="account_deletion_request",
        object_id=deletion.id,
    )
    return deletion


def list_deletion_requests(
    db: Session, *, actor: User, page: int, page_size: int
) -> AccountDeletionListResponse:
    total = db.scalar(
        select(func.count())
        .select_from(AccountDeletionRequest)
        .where(AccountDeletionRequest.blogger_id == actor.id)
    ) or 0
    items = list(
        db.scalars(
            select(AccountDeletionRequest)
            .where(AccountDeletionRequest.blogger_id == actor.id)
            .order_by(AccountDeletionRequest.requested_at.desc(), AccountDeletionRequest.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    now = utc_now()
    responses = []
    for item in items:
        response = AccountDeletionResponse.model_validate(item)
        if (
            response.status == DeletionRequestStatus.AWAITING_CONFIRMATION
            and _aware(response.expires_at) <= now
        ):
            response = response.model_copy(update={"status": DeletionRequestStatus.EXPIRED})
        responses.append(response)
    return AccountDeletionListResponse(
        items=responses,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size) if total else 0,
    )
