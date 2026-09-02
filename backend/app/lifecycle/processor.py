import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import load_only

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, RefreshSession, Role, User
from app.billing.models import CreatorBalance
from app.clock import utc_now
from app.content.models import (
    Publication,
    PublicationEnrichmentStatus,
    PublicationHistory,
    PublicationStatus,
    VideoCard,
)
from app.contracts import LifecycleCommand
from app.creators.models import CreatorProfile, ProfileHistory, ProfileStatus
from app.database.locking import set_transaction_timeouts
from app.lifecycle.models import (
    CreatorLifecycle,
    LifecycleAction,
    LifecycleJob,
    LifecycleJobState,
)
from app.lifecycle.policy import block_due_at, suspension_due_at, warning_due_at
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification
from app.readings.models import YouTubeViewCollectionJob
from app.youtube.models import YouTubeEnrichmentJob


logger = logging.getLogger(__name__)
MAX_LIFECYCLE_ATTEMPTS = 5
LOCK_TIMEOUT_MS = 5_000
STATEMENT_TIMEOUT_MS = 30_000


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _audit_context(job: LifecycleJob) -> AuditContext:
    return AuditContext(
        request_id=f"lifecycle-job:{job.id}",
        ip_address="internal",
        user_agent="lifecycle-worker",
    )


def _notify(
    db,
    *,
    user: User,
    lifecycle: CreatorLifecycle,
    job: LifecycleJob,
    template_code: str,
    context: dict[str, str | int],
    severity: NotificationSeverity,
) -> None:
    create_notification(
        db,
        NotificationCommand(
            recipient_user_id=user.id,
            template_code=template_code,
            context=context,
            severity=severity,
            deduplication_key=f"lifecycle:{user.id}:{job.action.value}:{job.basis_revision}",
            related_object_type="creator_lifecycle",
            related_object_id=user.id,
            action_path="/account-status",
        ),
    )


def _suspend(db, user: User, lifecycle: CreatorLifecycle, job: LifecycleJob, now: datetime) -> bool:
    if user.status != AccountStatus.ACTIVE or suspension_due_at(lifecycle.last_activity_at) > now:
        return False
    user.status = AccountStatus.SUSPENDED
    user.status_reason = "inactive_for_six_months"
    user.status_changed_at = now
    user.updated_at = now
    lifecycle.suspended_at = now
    lifecycle.activity_revision += 1
    profile = db.scalar(
        select(CreatorProfile)
        .options(
            load_only(
                CreatorProfile.id,
                CreatorProfile.user_id,
                CreatorProfile.status,
                CreatorProfile.moderation_reason,
            )
        )
        .where(CreatorProfile.user_id == user.id)
        .with_for_update()
    )
    if profile and profile.status == ProfileStatus.APPROVED:
        previous_profile_status = profile.status
        profile.status = ProfileStatus.SUSPENDED
        profile.moderation_reason = "inactive_for_six_months"
        db.add(
            ProfileHistory(
                profile_id=profile.id,
                actor_user_id=user.id,
                event_type="account_suspended_by_lifecycle",
                from_status=previous_profile_status.value,
                to_status=profile.status.value,
                reason="inactive_for_six_months",
                changes={"source": "lifecycle_worker"},
                created_at=now,
            )
        )
    publications = list(
        db.scalars(
            select(Publication)
            .options(load_only(Publication.id, Publication.status))
            .join(VideoCard, VideoCard.id == Publication.video_card_id)
            .where(
                VideoCard.blogger_id == user.id,
                Publication.status == PublicationStatus.APPROVED,
                Publication.deleted_at.is_(None),
            )
            .order_by(Publication.id)
            .with_for_update()
        )
    )
    publication_ids = [publication.id for publication in publications]
    if publication_ids:
        db.execute(
            update(Publication)
            .where(Publication.id.in_(publication_ids))
            .values(
                status=PublicationStatus.RE_REVIEW_REQUIRED,
                moderation_reason="account_suspended_for_inactivity",
                enrichment_status=PublicationEnrichmentStatus.NOT_REQUESTED,
                updated_at=now,
            )
        )
        for publication in publications:
            db.add(
                PublicationHistory(
                    publication_id=publication.id,
                    actor_user_id=user.id,
                    event_type="account_suspended_by_lifecycle",
                    from_status=PublicationStatus.APPROVED.value,
                    to_status=PublicationStatus.RE_REVIEW_REQUIRED.value,
                    reason="account_suspended_for_inactivity",
                    changes={"source": "lifecycle_worker"},
                    created_at=now,
                )
            )
        db.execute(
            update(YouTubeEnrichmentJob)
            .where(
                YouTubeEnrichmentJob.publication_id.in_(publication_ids),
                YouTubeEnrichmentJob.state.in_(
                    ("pending", "queued", "processing", "retry_wait")
                ),
            )
            .values(
                state="failed",
                lease_until=None,
                dispatch_id=None,
                last_error_code="account_suspended",
            )
        )
        db.execute(
            update(YouTubeViewCollectionJob)
            .where(
                YouTubeViewCollectionJob.publication_id.in_(publication_ids),
                YouTubeViewCollectionJob.state.in_(
                    ("pending", "queued", "processing", "retry_wait")
                ),
            )
            .values(
                state="failed",
                lease_until=None,
                dispatch_id=None,
                last_error_code="account_suspended",
            )
        )
    _notify(
        db,
        user=user,
        lifecycle=lifecycle,
        job=job,
        template_code="account_suspended",
        context={},
        severity=NotificationSeverity.ACTION_REQUIRED,
    )
    record_event(
        db,
        context=_audit_context(job),
        action=AuditAction.ACCOUNT_SUSPENDED_FOR_INACTIVITY,
        actor_role="lifecycle_worker",
        object_type="user",
        object_id=user.id,
        metadata={"publication_count": len(publication_ids)},
    )
    return True


def _block(db, user: User, lifecycle: CreatorLifecycle, job: LifecycleJob, now: datetime) -> bool:
    if (
        user.status != AccountStatus.SUSPENDED
        or lifecycle.suspended_at is None
        or block_due_at(lifecycle.suspended_at) > now
    ):
        return False
    user.status = AccountStatus.BLOCKED
    user.status_before_block = AccountStatus.SUSPENDED
    user.status_reason = "not_restored_after_suspension"
    user.status_changed_at = now
    user.updated_at = now
    lifecycle.blocked_at = now
    lifecycle.balance_claim_expired_at = now
    lifecycle.activity_revision += 1
    profile = db.scalar(
        select(CreatorProfile)
        .options(
            load_only(
                CreatorProfile.id,
                CreatorProfile.user_id,
                CreatorProfile.status,
                CreatorProfile.moderation_reason,
            )
        )
        .where(CreatorProfile.user_id == user.id)
        .with_for_update()
    )
    if profile and profile.status == ProfileStatus.SUSPENDED:
        previous_profile_status = profile.status
        profile.status = ProfileStatus.BLOCKED
        profile.moderation_reason = "not_restored_after_suspension"
        db.add(
            ProfileHistory(
                profile_id=profile.id,
                actor_user_id=user.id,
                event_type="account_blocked_by_lifecycle",
                from_status=previous_profile_status.value,
                to_status=profile.status.value,
                reason="not_restored_after_suspension",
                changes={"source": "lifecycle_worker"},
                created_at=now,
            )
        )
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    balance = db.scalar(
        select(CreatorBalance)
        .options(
            load_only(
                CreatorBalance.blogger_id,
                CreatorBalance.available_kopecks,
                CreatorBalance.claim_expired_at,
            )
        )
        .where(CreatorBalance.blogger_id == user.id)
        .with_for_update()
    )
    if balance:
        balance.claim_expired_at = now
    _notify(
        db,
        user=user,
        lifecycle=lifecycle,
        job=job,
        template_code="account_fully_blocked",
        context={"balance_kopecks": balance.available_kopecks if balance else 0},
        severity=NotificationSeverity.ACTION_REQUIRED,
    )
    record_event(
        db,
        context=_audit_context(job),
        action=AuditAction.ACCOUNT_BLOCKED_FOR_INACTIVITY,
        actor_role="lifecycle_worker",
        object_type="user",
        object_id=user.id,
        metadata={
            "available_balance_kopecks": balance.available_kopecks if balance else 0,
            "balance_claim_expired": True,
        },
    )
    return True


def _execute_action(db, user: User, lifecycle: CreatorLifecycle, job: LifecycleJob, now: datetime) -> bool:
    if job.action == LifecycleAction.SUSPEND:
        return _suspend(db, user, lifecycle, job, now)
    if job.action == LifecycleAction.BLOCK:
        return _block(db, user, lifecycle, job, now)
    if job.action in {
        LifecycleAction.WARN_SUSPENSION_30,
        LifecycleAction.WARN_SUSPENSION_7,
    }:
        days = 30 if job.action == LifecycleAction.WARN_SUSPENSION_30 else 7
        due = suspension_due_at(lifecycle.last_activity_at)
        window_end = warning_due_at(due, 7) if days == 30 else due
        if user.status != AccountStatus.ACTIVE or not _aware(job.due_at) <= now < window_end:
            return False
        _notify(
            db,
            user=user,
            lifecycle=lifecycle,
            job=job,
            template_code="account_suspension_warning",
            context={"days": days, "deadline": due.date().isoformat()},
            severity=NotificationSeverity.WARNING,
        )
        return True
    days = 30 if job.action == LifecycleAction.WARN_BLOCK_30 else 7
    if lifecycle.suspended_at is None:
        return False
    due = block_due_at(lifecycle.suspended_at)
    window_end = warning_due_at(due, 7) if days == 30 else due
    if user.status != AccountStatus.SUSPENDED or not _aware(job.due_at) <= now < window_end:
        return False
    _notify(
        db,
        user=user,
        lifecycle=lifecycle,
        job=job,
        template_code="account_block_warning",
        context={"days": days, "deadline": due.date().isoformat()},
        severity=NotificationSeverity.WARNING,
    )
    return True


def execute_lifecycle_job(command: LifecycleCommand, session_factory, *, now: datetime | None = None) -> None:
    now = now or utc_now()
    try:
        with session_factory.begin() as db:
            set_transaction_timeouts(
                db,
                lock_timeout_ms=LOCK_TIMEOUT_MS,
                statement_timeout_ms=STATEMENT_TIMEOUT_MS,
            )
            candidate = db.execute(
                select(LifecycleJob.id, LifecycleJob.blogger_id).where(
                    LifecycleJob.id == command.job_id,
                    LifecycleJob.dispatch_id == command.dispatch_id,
                    LifecycleJob.state == LifecycleJobState.QUEUED,
                )
            ).one_or_none()
            if not candidate:
                return
            user = db.scalar(
                select(User)
                .options(
                    load_only(
                        User.id,
                        User.email,
                        User.role,
                        User.status,
                        User.status_before_block,
                        User.status_reason,
                        User.status_changed_at,
                        User.updated_at,
                    )
                )
                .where(User.id == candidate.blogger_id)
                .with_for_update()
            )
            lifecycle = db.scalar(
                select(CreatorLifecycle)
                .where(CreatorLifecycle.blogger_id == candidate.blogger_id)
                .with_for_update()
            )
            job = db.scalar(
                select(LifecycleJob)
                .where(
                    LifecycleJob.id == candidate.id,
                    LifecycleJob.dispatch_id == command.dispatch_id,
                    LifecycleJob.state == LifecycleJobState.QUEUED,
                )
                .with_for_update()
            )
            if not job:
                return
            job.state = LifecycleJobState.PROCESSING
            if (
                not user
                or not lifecycle
                or user.role != Role.BLOGGER
                or lifecycle.activity_revision != job.basis_revision
            ):
                job.state = LifecycleJobState.OBSOLETE
            elif _execute_action(db, user, lifecycle, job, now):
                job.state = LifecycleJobState.SUCCEEDED
            else:
                job.state = LifecycleJobState.OBSOLETE
            job.lease_until = None
            job.last_error_code = None
    except Exception:
        with session_factory.begin() as db:
            set_transaction_timeouts(
                db,
                lock_timeout_ms=LOCK_TIMEOUT_MS,
                statement_timeout_ms=STATEMENT_TIMEOUT_MS,
            )
            job = db.scalar(
                select(LifecycleJob)
                .where(
                    LifecycleJob.id == command.job_id,
                    LifecycleJob.dispatch_id == command.dispatch_id,
                )
                .with_for_update()
            )
            if job:
                job.state = (
                    LifecycleJobState.RETRY_WAIT
                    if job.attempt_count < MAX_LIFECYCLE_ATTEMPTS
                    else LifecycleJobState.FAILED
                )
                job.available_at = now + timedelta(minutes=5)
                job.lease_until = None
                job.dispatch_id = None
                job.last_error_code = "worker_error"
        logger.exception("Lifecycle job failed", extra={"job_id": str(command.job_id)})
        raise
