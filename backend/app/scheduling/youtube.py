import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from celery import Celery
from sqlalchemy import select

from app.clock import utc_now
from app.contracts import (
    YOUTUBE_QUEUE,
    YOUTUBE_TASK,
    YOUTUBE_VIEWS_TASK,
    YouTubeCommandItem,
    YouTubeEnrichmentCommand,
    YouTubeViewCollectionCommand,
    YouTubeViewCommandItem,
)
from app.content.models import Publication, PublicationStatus
from app.database.factory import create_session_factory
from app.platforms import Platform
from app.readings.models import YouTubeViewCollectionJob
from app.scheduler_config import get_scheduler_settings
from app.youtube.models import ExternalProviderState, ExternalQuotaUsage, YouTubeEnrichmentJob
from app.youtube.time import next_pacific_reset, pacific_quota_date


logger = logging.getLogger(__name__)
settings = get_scheduler_settings()
SessionLocal = create_session_factory(settings.database_url)
producer = Celery("amp_youtube_scheduler", broker=settings.redis_url)
YOUTUBE_LEASE = timedelta(minutes=5)
MOSCOW = ZoneInfo("Europe/Moscow")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _provider_available(db, now: datetime) -> bool:
    state = db.scalar(
        select(ExternalProviderState)
        .where(ExternalProviderState.provider == "youtube")
        .with_for_update()
    )
    if not state:
        db.add(ExternalProviderState(provider="youtube"))
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
    for model in (YouTubeEnrichmentJob, YouTubeViewCollectionJob):
        active = db.scalar(
            select(model.id)
            .where(
                model.state.in_(("queued", "processing")),
                model.lease_until >= now,
            )
            .limit(1)
        )
        if active:
            return True
    return False


def _reserve_quota(db, now: datetime):
    quota_date = pacific_quota_date(now)
    usage = db.get(ExternalQuotaUsage, {"provider": "youtube", "quota_date": quota_date})
    if not usage:
        usage = ExternalQuotaUsage(
            provider="youtube",
            quota_date=quota_date,
            working_limit=settings.youtube_daily_working_limit,
        )
        db.add(usage)
        db.flush()
    usage.working_limit = settings.youtube_daily_working_limit
    if usage.reserved_units >= usage.working_limit:
        provider = db.get(ExternalProviderState, "youtube")
        provider.status = "blocked"
        provider.blocked_until = next_pacific_reset(now)
        provider.block_reason = "working_limit_reached"
        return None
    usage.reserved_units += 1
    return usage


def dispatch_youtube_batch(
    *,
    now: datetime | None = None,
    session_factory=SessionLocal,
    task_producer=producer,
) -> bool:
    now = now or utc_now()
    db = session_factory()
    command: YouTubeEnrichmentCommand | None = None
    quota_date = pacific_quota_date(now)
    try:
        stale_jobs = list(
            db.scalars(
                select(YouTubeEnrichmentJob)
                .join(Publication, Publication.id == YouTubeEnrichmentJob.publication_id)
                .where(
                    YouTubeEnrichmentJob.state.in_(("pending", "retry_wait")),
                    Publication.status.not_in(
                        (PublicationStatus.PENDING_REVIEW, PublicationStatus.APPROVED)
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for stale_job in stale_jobs:
            stale_job.state = "failed"
            stale_job.last_error_code = "publication_not_active"
        expired = list(
            db.scalars(
                select(YouTubeEnrichmentJob)
                .where(
                    YouTubeEnrichmentJob.state.in_(("queued", "processing")),
                    YouTubeEnrichmentJob.lease_until < now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in expired:
            job.state = "retry_wait"
            job.available_at = now
            job.lease_until = None
            job.dispatch_id = None
            job.last_error_code = "worker_lease_expired"

        if not _provider_available(db, now):
            db.commit()
            return False

        if _has_active_job(db, now):
            db.commit()
            return False

        jobs = list(
            db.scalars(
                select(YouTubeEnrichmentJob)
                .join(Publication, Publication.id == YouTubeEnrichmentJob.publication_id)
                .where(
                    YouTubeEnrichmentJob.state.in_(("pending", "retry_wait")),
                    YouTubeEnrichmentJob.available_at <= now,
                    Publication.status.in_(
                        (PublicationStatus.PENDING_REVIEW, PublicationStatus.APPROVED)
                    ),
                )
                .order_by(YouTubeEnrichmentJob.created_at, YouTubeEnrichmentJob.id)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        if not jobs:
            db.commit()
            return False

        usage = _reserve_quota(db, now)
        if usage is None:
            db.commit()
            return False

        dispatch_id = uuid.uuid4()
        items = []
        for job in jobs:
            job.state = "queued"
            job.attempt_count += 1
            job.lease_until = now + YOUTUBE_LEASE
            job.dispatch_id = dispatch_id
            job.last_error_code = None
            items.append(
                YouTubeCommandItem(
                    job_id=job.id,
                    publication_id=job.publication_id,
                    video_id=job.external_id,
                )
            )
        command = YouTubeEnrichmentCommand(dispatch_id=dispatch_id, items=items)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    assert command is not None
    try:
        task_producer.send_task(
            YOUTUBE_TASK,
            args=[command.model_dump(mode="json")],
            queue=YOUTUBE_QUEUE,
        )
        return True
    except Exception:
        db = session_factory()
        try:
            jobs = list(
                db.scalars(
                    select(YouTubeEnrichmentJob)
                    .where(
                        YouTubeEnrichmentJob.dispatch_id == command.dispatch_id,
                        YouTubeEnrichmentJob.state == "queued",
                    )
                    .with_for_update()
                )
            )
            for job in jobs:
                job.state = "retry_wait"
                job.available_at = now
                job.lease_until = None
                job.dispatch_id = None
                job.last_error_code = "broker_publish_failed"
            usage = db.get(
                ExternalQuotaUsage,
                {"provider": "youtube", "quota_date": quota_date},
            )
            if usage and jobs:
                usage.reserved_units = max(0, usage.reserved_units - 1)
            db.commit()
        finally:
            db.close()
        logger.exception(
            "Could not publish YouTube command",
            extra={"dispatch_id": str(command.dispatch_id)},
        )
        return False


def _create_daily_view_jobs(db, now: datetime) -> None:
    local = now.astimezone(MOSCOW)
    if local.day < 25 or local.hour < settings.youtube_collection_hour_moscow:
        return
    collection_date = local.date()
    existing = set(
        db.scalars(
            select(YouTubeViewCollectionJob.publication_id).where(
                YouTubeViewCollectionJob.collection_date == collection_date
            )
        )
    )
    publications = db.execute(
        select(Publication.id, Publication.external_id).where(
            Publication.platform == Platform.YOUTUBE,
            Publication.status == PublicationStatus.APPROVED,
            Publication.deleted_at.is_(None),
            Publication.external_id.is_not(None),
            Publication.id.notin_(existing) if existing else True,
        )
    ).all()
    for publication_id, external_id in publications:
        db.add(
            YouTubeViewCollectionJob(
                publication_id=publication_id,
                external_id=external_id,
                collection_date=collection_date,
                available_at=now,
            )
        )
    db.flush()


def dispatch_youtube_view_batch(
    *,
    now: datetime | None = None,
    session_factory=SessionLocal,
    task_producer=producer,
) -> bool:
    now = now or utc_now()
    quota_date = pacific_quota_date(now)
    db = session_factory()
    command = None
    try:
        _create_daily_view_jobs(db, now)
        stale_jobs = list(
            db.scalars(
                select(YouTubeViewCollectionJob)
                .join(Publication, Publication.id == YouTubeViewCollectionJob.publication_id)
                .where(
                    YouTubeViewCollectionJob.state.in_(("pending", "retry_wait")),
                    Publication.status != PublicationStatus.APPROVED,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for stale_job in stale_jobs:
            stale_job.state = "failed"
            stale_job.last_error_code = "publication_not_active"
        expired = list(
            db.scalars(
                select(YouTubeViewCollectionJob)
                .where(
                    YouTubeViewCollectionJob.state.in_(("queued", "processing")),
                    YouTubeViewCollectionJob.lease_until < now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in expired:
            job.state = "retry_wait"
            job.available_at = now
            job.lease_until = None
            job.dispatch_id = None
            job.last_error_code = "worker_lease_expired"
        if not _provider_available(db, now) or _has_active_job(db, now):
            db.commit()
            return False
        jobs = list(
            db.scalars(
                select(YouTubeViewCollectionJob)
                .join(Publication, Publication.id == YouTubeViewCollectionJob.publication_id)
                .where(
                    YouTubeViewCollectionJob.state.in_(("pending", "retry_wait")),
                    YouTubeViewCollectionJob.available_at <= now,
                    Publication.status == PublicationStatus.APPROVED,
                )
                .order_by(YouTubeViewCollectionJob.created_at, YouTubeViewCollectionJob.id)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        if not jobs:
            db.commit()
            return False
        usage = _reserve_quota(db, now)
        if usage is None:
            db.commit()
            return False
        dispatch_id = uuid.uuid4()
        items = []
        for job in jobs:
            job.state = "queued"
            job.attempt_count += 1
            job.lease_until = now + YOUTUBE_LEASE
            job.dispatch_id = dispatch_id
            job.last_error_code = None
            items.append(
                YouTubeViewCommandItem(
                    job_id=job.id,
                    publication_id=job.publication_id,
                    video_id=job.external_id,
                )
            )
        command = YouTubeViewCollectionCommand(dispatch_id=dispatch_id, items=items)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    try:
        task_producer.send_task(
            YOUTUBE_VIEWS_TASK,
            args=[command.model_dump(mode="json")],
            queue=YOUTUBE_QUEUE,
        )
        return True
    except Exception:
        db = session_factory()
        try:
            jobs = list(db.scalars(select(YouTubeViewCollectionJob).where(
                YouTubeViewCollectionJob.dispatch_id == command.dispatch_id,
                YouTubeViewCollectionJob.state == "queued",
            ).with_for_update()))
            for job in jobs:
                job.state = "retry_wait"
                job.available_at = now
                job.lease_until = None
                job.dispatch_id = None
                job.last_error_code = "broker_publish_failed"
            usage = db.get(ExternalQuotaUsage, {"provider": "youtube", "quota_date": quota_date})
            if usage and jobs:
                usage.reserved_units = max(0, usage.reserved_units - 1)
            db.commit()
        finally:
            db.close()
        logger.exception("Could not publish YouTube view command", extra={"dispatch_id": str(command.dispatch_id)})
        return False
