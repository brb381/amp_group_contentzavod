import logging
import uuid
from datetime import datetime, timedelta, timezone

from celery import Celery
from sqlalchemy import select

from app.clock import utc_now
from app.content.models import Publication, PublicationStatus
from app.contracts import (
    VK_QUEUE,
    VK_TASK,
    VK_VIEWS_TASK,
    VKEnrichmentCommand,
    VKViewCollectionCommand,
)
from app.database.factory import create_session_factory
from app.integrations.models import ExternalProviderState
from app.platforms import Platform
from app.scheduler_config import get_scheduler_settings
from app.readings.view_slots import current_view_collection_slot
from app.vk.models import VKEnrichmentJob, VKViewCollectionJob


logger = logging.getLogger(__name__)
settings = get_scheduler_settings()
SessionLocal = create_session_factory(settings.database_url)
producer = Celery("amp_vk_scheduler", broker=settings.redis_url)
VK_LEASE = timedelta(minutes=5)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _provider_available(db, now: datetime) -> bool:
    state = db.scalar(
        select(ExternalProviderState)
        .where(ExternalProviderState.provider == "vk")
        .with_for_update()
    )
    if not state:
        db.add(ExternalProviderState(provider="vk"))
        db.flush()
        return True
    if state.status == "available":
        return True
    if state.blocked_until is None or _aware(state.blocked_until) > now:
        return False
    state.status = "available"
    state.blocked_until = None
    state.block_reason = None
    return True


def _has_active_job(db, now: datetime) -> bool:
    for model in (VKEnrichmentJob, VKViewCollectionJob):
        active = db.scalar(
            select(model.id)
            .where(model.state.in_(("queued", "processing")), model.lease_until >= now)
            .limit(1)
        )
        if active:
            return True
    return False


def _recover_expired(db, model, now: datetime) -> None:
    jobs = list(
        db.scalars(
            select(model)
            .where(model.state.in_(("queued", "processing")), model.lease_until < now)
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        job.state = "retry_wait"
        job.available_at = now
        job.lease_until = None
        job.dispatch_id = None
        job.last_error_code = "worker_lease_expired"


def dispatch_vk_enrichment(
    *, now: datetime | None = None, session_factory=SessionLocal, task_producer=producer
) -> bool:
    now = now or utc_now()
    db = session_factory()
    command = None
    try:
        _recover_expired(db, VKEnrichmentJob, now)
        stale = list(
            db.scalars(
                select(VKEnrichmentJob)
                .join(Publication, Publication.id == VKEnrichmentJob.publication_id)
                .where(
                    VKEnrichmentJob.state.in_(("pending", "retry_wait")),
                    Publication.status.not_in(
                        (PublicationStatus.PENDING_REVIEW, PublicationStatus.APPROVED)
                    ),
                )
                .with_for_update(of=VKEnrichmentJob, skip_locked=True)
            )
        )
        for job in stale:
            job.state = "failed"
            job.last_error_code = "publication_not_active"
        if not _provider_available(db, now) or _has_active_job(db, now):
            db.commit()
            return False
        row = db.execute(
            select(VKEnrichmentJob, Publication.submitted_url)
            .join(Publication, Publication.id == VKEnrichmentJob.publication_id)
            .where(
                VKEnrichmentJob.state.in_(("pending", "retry_wait")),
                VKEnrichmentJob.available_at <= now,
                Publication.status.in_((PublicationStatus.PENDING_REVIEW, PublicationStatus.APPROVED)),
            )
            .order_by(VKEnrichmentJob.created_at, VKEnrichmentJob.id)
            .limit(1)
            .with_for_update(of=VKEnrichmentJob, skip_locked=True)
        ).one_or_none()
        if not row:
            db.commit()
            return False
        job, source_url = row
        dispatch_id = uuid.uuid4()
        job.state = "queued"
        job.attempt_count += 1
        job.lease_until = now + VK_LEASE
        job.dispatch_id = dispatch_id
        job.last_error_code = None
        command = VKEnrichmentCommand(
            dispatch_id=dispatch_id,
            job_id=job.id,
            publication_id=job.publication_id,
            source_url=source_url,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return _publish_or_release(
        command, VKEnrichmentJob, VK_TASK, now, session_factory, task_producer
    )


def _create_view_jobs(db, now: datetime) -> None:
    slot = current_view_collection_slot(now)

    publications = db.scalars(
        select(Publication).where(
            Publication.platform == Platform.VK,
            Publication.status == PublicationStatus.APPROVED,
            Publication.deleted_at.is_(None),
            ~select(VKViewCollectionJob.id).where(
                VKViewCollectionJob.publication_id == Publication.id,
                VKViewCollectionJob.collection_date == slot.collection_date,
                VKViewCollectionJob.collection_slot == slot.index,
            ).exists(),
        )
    )
    for publication in publications:
        db.add(
            VKViewCollectionJob(
                publication_id=publication.id,
                collection_date=slot.collection_date,
                collection_slot=slot.index,
                available_at=now,
            )
        )
    db.flush()


def dispatch_vk_view(
    *, now: datetime | None = None, session_factory=SessionLocal, task_producer=producer
) -> bool:
    now = now or utc_now()
    db = session_factory()
    command = None
    try:
        _create_view_jobs(db, now)
        _recover_expired(db, VKViewCollectionJob, now)
        stale = list(
            db.scalars(
                select(VKViewCollectionJob)
                .join(Publication, Publication.id == VKViewCollectionJob.publication_id)
                .where(
                    VKViewCollectionJob.state.in_(("pending", "retry_wait")),
                    Publication.status != PublicationStatus.APPROVED,
                )
                .with_for_update(of=VKViewCollectionJob, skip_locked=True)
            )
        )
        for job in stale:
            job.state = "failed"
            job.last_error_code = "publication_not_active"
        if not _provider_available(db, now) or _has_active_job(db, now):
            db.commit()
            return False
        row = db.execute(
            select(VKViewCollectionJob, Publication.submitted_url)
            .join(Publication, Publication.id == VKViewCollectionJob.publication_id)
            .where(
                VKViewCollectionJob.state.in_(("pending", "retry_wait")),
                VKViewCollectionJob.available_at <= now,
                Publication.status == PublicationStatus.APPROVED,
            )
            .order_by(VKViewCollectionJob.created_at, VKViewCollectionJob.id)
            .limit(1)
            .with_for_update(of=VKViewCollectionJob, skip_locked=True)
        ).one_or_none()
        if not row:
            db.commit()
            return False
        job, source_url = row
        dispatch_id = uuid.uuid4()
        job.state = "queued"
        job.attempt_count += 1
        job.lease_until = now + VK_LEASE
        job.dispatch_id = dispatch_id
        job.last_error_code = None
        command = VKViewCollectionCommand(
            dispatch_id=dispatch_id,
            job_id=job.id,
            publication_id=job.publication_id,
            source_url=source_url,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return _publish_or_release(
        command, VKViewCollectionJob, VK_VIEWS_TASK, now, session_factory, task_producer
    )


def _publish_or_release(command, model, task_name, now, session_factory, task_producer) -> bool:
    try:
        task_producer.send_task(
            task_name, args=[command.model_dump(mode="json")], queue=VK_QUEUE
        )
        return True
    except Exception:
        db = session_factory()
        try:
            job = db.scalar(
                select(model)
                .where(model.dispatch_id == command.dispatch_id, model.state == "queued")
                .with_for_update()
            )
            if job:
                job.state = "retry_wait"
                job.available_at = now
                job.lease_until = None
                job.dispatch_id = None
                job.last_error_code = "broker_publish_failed"
            db.commit()
        finally:
            db.close()
        logger.exception("Could not publish VK command", extra={"task": task_name})
        return False
