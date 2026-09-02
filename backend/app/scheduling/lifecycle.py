import logging
import uuid
from datetime import datetime, timedelta

from celery import Celery
from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.auth.models import AccountStatus, Role, User
from app.clock import utc_now
from app.contracts import LIFECYCLE_QUEUE, LIFECYCLE_TASK, LifecycleCommand
from app.database.factory import create_session_factory
from app.lifecycle.models import (
    CreatorLifecycle,
    LifecycleAction,
    LifecycleJob,
    LifecycleJobState,
)
from app.lifecycle.policy import block_due_at, suspension_due_at, warning_due_at
from app.lifecycle.processor import MAX_LIFECYCLE_ATTEMPTS
from app.scheduler_config import get_scheduler_settings


logger = logging.getLogger(__name__)
settings = get_scheduler_settings()
SessionLocal = create_session_factory(settings.database_url)
producer = Celery("amp_lifecycle_scheduler", broker=settings.redis_url)
LEASE = timedelta(minutes=5)
SCAN_INTERVAL = timedelta(hours=1)
SCAN_BATCH_SIZE = 500


def _candidate_actions(user: User, lifecycle: CreatorLifecycle, now: datetime):
    if user.status == AccountStatus.ACTIVE:
        transition_due = suspension_due_at(lifecycle.last_activity_at)
        warning_30 = warning_due_at(transition_due, 30)
        warning_7 = warning_due_at(transition_due, 7)
        if warning_30 <= now < warning_7:
            yield LifecycleAction.WARN_SUSPENSION_30, warning_30
        if warning_7 <= now < transition_due:
            yield LifecycleAction.WARN_SUSPENSION_7, warning_7
        if transition_due <= now:
            yield LifecycleAction.SUSPEND, transition_due
    elif user.status == AccountStatus.SUSPENDED and lifecycle.suspended_at:
        transition_due = block_due_at(lifecycle.suspended_at)
        warning_30 = warning_due_at(transition_due, 30)
        warning_7 = warning_due_at(transition_due, 7)
        if warning_30 <= now < warning_7:
            yield LifecycleAction.WARN_BLOCK_30, warning_30
        if warning_7 <= now < transition_due:
            yield LifecycleAction.WARN_BLOCK_7, warning_7
        if transition_due <= now:
            yield LifecycleAction.BLOCK, transition_due


def _ensure_due_jobs(db, now: datetime) -> None:
    rows = db.execute(
        select(User, CreatorLifecycle)
        .options(load_only(User.id, User.role, User.status))
        .join(CreatorLifecycle, CreatorLifecycle.blogger_id == User.id)
        .where(
            User.role == Role.BLOGGER,
            User.status.in_((AccountStatus.ACTIVE, AccountStatus.SUSPENDED)),
            (
                CreatorLifecycle.scheduler_scanned_at.is_(None)
                | (CreatorLifecycle.scheduler_scanned_at <= now - SCAN_INTERVAL)
            ),
        )
        .order_by(
            CreatorLifecycle.scheduler_scanned_at.asc().nulls_first(),
            User.id,
        )
        .limit(SCAN_BATCH_SIZE)
        .with_for_update(of=CreatorLifecycle, skip_locked=True)
    ).all()
    for user, lifecycle in rows:
        lifecycle.scheduler_scanned_at = now
        for action, due_at in _candidate_actions(user, lifecycle, now):
            exists = db.scalar(
                select(LifecycleJob.id).where(
                    LifecycleJob.blogger_id == user.id,
                    LifecycleJob.action == action,
                    LifecycleJob.basis_revision == lifecycle.activity_revision,
                )
            )
            if not exists:
                db.add(
                    LifecycleJob(
                        blogger_id=user.id,
                        action=action,
                        basis_revision=lifecycle.activity_revision,
                        due_at=due_at,
                        available_at=now,
                    )
                )
    db.flush()


def dispatch_lifecycle(
    *,
    now: datetime | None = None,
    session_factory=SessionLocal,
    task_producer=producer,
) -> bool:
    now = now or utc_now()
    command = None
    with session_factory.begin() as db:
        _ensure_due_jobs(db, now)
        expired = list(
            db.scalars(
                select(LifecycleJob)
                .where(
                    LifecycleJob.state.in_(
                        (LifecycleJobState.QUEUED, LifecycleJobState.PROCESSING)
                    ),
                    LifecycleJob.lease_until < now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in expired:
            job.state = (
                LifecycleJobState.RETRY_WAIT
                if job.attempt_count < MAX_LIFECYCLE_ATTEMPTS
                else LifecycleJobState.FAILED
            )
            job.available_at = now
            job.lease_until = None
            job.dispatch_id = None
            job.last_error_code = "worker_lease_expired"
        job = db.scalar(
            select(LifecycleJob)
            .where(
                LifecycleJob.state.in_(
                    (LifecycleJobState.PENDING, LifecycleJobState.RETRY_WAIT)
                ),
                LifecycleJob.attempt_count < MAX_LIFECYCLE_ATTEMPTS,
                LifecycleJob.available_at <= now,
            )
            .order_by(LifecycleJob.due_at, LifecycleJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job:
            dispatch_id = uuid.uuid4()
            job.state = LifecycleJobState.QUEUED
            job.attempt_count += 1
            job.dispatch_id = dispatch_id
            job.lease_until = now + LEASE
            job.last_error_code = None
            command = LifecycleCommand(job_id=job.id, dispatch_id=dispatch_id)
    if not command:
        return False
    try:
        task_producer.send_task(
            LIFECYCLE_TASK,
            args=[command.model_dump(mode="json")],
            queue=LIFECYCLE_QUEUE,
        )
        return True
    except Exception:
        with session_factory.begin() as db:
            job = db.scalar(
                select(LifecycleJob)
                .where(
                    LifecycleJob.id == command.job_id,
                    LifecycleJob.dispatch_id == command.dispatch_id,
                    LifecycleJob.state == LifecycleJobState.QUEUED,
                )
                .with_for_update()
            )
            if job:
                job.state = LifecycleJobState.RETRY_WAIT
                job.attempt_count = max(0, job.attempt_count - 1)
                job.available_at = now
                job.lease_until = None
                job.dispatch_id = None
                job.last_error_code = "broker_publish_failed"
        logger.exception("Could not publish lifecycle command")
        return False
