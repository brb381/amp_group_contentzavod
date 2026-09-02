from datetime import timedelta

from sqlalchemy import select

from app.clock import utc_now
from app.content.models import Publication, PublicationAvailability
from app.contracts import YouTubeViewCollectionCommand
from app.readings.models import (
    ReadingSource,
    ReadingStatus,
    ViewReading,
    ViewReadingHistory,
    YouTubeViewCollectionJob,
)
from app.readings.policy import risk_flags
from app.readings.revision import lock_reading_dataset_revision
from app.youtube.client import YouTubeClient, YouTubeClientError
from app.youtube.models import ExternalProviderState
from app.youtube.service import (
    CONFIGURATION_REASONS,
    DAILY_LIMIT_REASONS,
    MAX_TRANSIENT_ATTEMPTS,
    TRANSIENT_REASONS,
    _retry_after,
)
from app.youtube.time import next_pacific_reset


def _provider(db) -> ExternalProviderState:
    provider = db.get(ExternalProviderState, "youtube")
    if not provider:
        provider = ExternalProviderState(provider="youtube")
        db.add(provider)
    return provider


def _claim(command: YouTubeViewCollectionCommand, session_factory) -> bool:
    db = session_factory()
    try:
        ids = [item.job_id for item in command.items]
        jobs = list(db.scalars(select(YouTubeViewCollectionJob).where(
            YouTubeViewCollectionJob.id.in_(ids),
            YouTubeViewCollectionJob.dispatch_id == command.dispatch_id,
            YouTubeViewCollectionJob.state == "queued",
        ).with_for_update()))
        if len(jobs) != len(ids):
            db.rollback()
            return False
        for job in jobs:
            job.state = "processing"
        db.commit()
        return True
    finally:
        db.close()


def _record_error(command: YouTubeViewCollectionCommand, error: YouTubeClientError, session_factory) -> None:
    db = session_factory()
    try:
        now = utc_now()
        jobs = list(db.scalars(select(YouTubeViewCollectionJob).where(
            YouTubeViewCollectionJob.dispatch_id == command.dispatch_id,
            YouTubeViewCollectionJob.state == "processing",
        ).with_for_update()))
        if not jobs:
            return
        is_daily = error.reason in DAILY_LIMIT_REASONS
        is_configuration = error.reason in CONFIGURATION_REASONS
        is_transient = (
            error.status_code == 429
            or (error.status_code is not None and error.status_code >= 500)
            or error.reason in TRANSIENT_REASONS
        )
        if is_daily:
            retry_at = next_pacific_reset(now)
        elif is_configuration:
            retry_at = now + timedelta(minutes=15)
        elif is_transient:
            attempt = max(job.attempt_count for job in jobs)
            fallback = now + timedelta(seconds=min(1800, 60 * (2 ** max(0, attempt - 1))))
            retry_at = _retry_after(error.retry_after, now=now) or fallback
        else:
            retry_at = None
        if is_daily or is_configuration or is_transient:
            provider = _provider(db)
            provider.status = "blocked"
            provider.blocked_until = retry_at
            provider.block_reason = error.reason
        for job in jobs:
            should_retry = (
                is_daily
                or is_configuration
                or (is_transient and job.attempt_count < MAX_TRANSIENT_ATTEMPTS)
            )
            job.state = "retry_wait" if should_retry else "failed"
            job.available_at = retry_at or now
            job.lease_until = None
            job.dispatch_id = None
            job.last_error_code = error.reason
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _apply_success(command, response, settings, session_factory) -> None:
    counts = {item.id: item.statistics.viewCount for item in response.items}
    commands = {item.job_id: item for item in command.items}
    db = session_factory()
    try:
        now = utc_now()
        jobs = list(db.scalars(select(YouTubeViewCollectionJob).where(
            YouTubeViewCollectionJob.dispatch_id == command.dispatch_id,
            YouTubeViewCollectionJob.state == "processing",
        ).with_for_update()))
        revision = lock_reading_dataset_revision(db)
        readings_changed = False
        for job in jobs:
            item = commands.get(job.id)
            publication = db.get(Publication, job.publication_id)
            if not item or not publication or publication.external_id != item.video_id:
                job.state = "failed"
                job.last_error_code = "stale_command"
            elif item.video_id not in counts:
                publication.availability = PublicationAvailability.UNAVAILABLE
                job.state = "failed"
                job.last_error_code = "video_unavailable"
            else:
                value = counts[item.video_id]
                period = job.collection_date.replace(day=1)
                if (
                    revision.closed_through_period is not None
                    and period <= revision.closed_through_period
                ):
                    job.state = "failed"
                    job.last_error_code = "reading_period_financially_closed"
                    job.lease_until = None
                    job.dispatch_id = None
                    continue
                key = f"youtube:{job.collection_date.isoformat()}"
                reading = db.scalar(select(ViewReading).where(
                    ViewReading.publication_id == publication.id,
                    ViewReading.idempotency_key == key,
                ))
                if not reading:
                    flags = risk_flags(
                        db,
                        publication_id=publication.id,
                        period=period,
                        value=value,
                        suspicious_growth_threshold=(
                            settings.suspicious_monthly_view_growth
                        ),
                    )
                    pending = "views_decreased" in flags
                    reading = ViewReading(
                        publication_id=publication.id,
                        reporting_period=period,
                        source=ReadingSource.YOUTUBE_API,
                        reported_value=value,
                        accepted_value=None if pending else value,
                        status=ReadingStatus.PENDING if pending else ReadingStatus.ACCEPTED,
                        risk_flags=flags,
                        idempotency_key=key,
                        captured_at=now,
                    )
                    db.add(reading)
                    db.flush()
                    readings_changed = True
                    db.add(
                        ViewReadingHistory(
                            reading_id=reading.id,
                            action="created" if pending else "auto_accepted",
                            new_value=value,
                        )
                    )
                publication.availability = PublicationAvailability.AVAILABLE
                job.state = "succeeded"
                job.last_error_code = None
            job.lease_until = None
            job.dispatch_id = None
        if readings_changed:
            revision.revision += 1
        provider = _provider(db)
        provider.status = "available"
        provider.blocked_until = None
        provider.block_reason = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_youtube_view_collection(command, settings, session_factory, client=None) -> None:
    if not _claim(command, session_factory):
        return
    api_key = settings.youtube_api_key.get_secret_value() if settings.youtube_api_key else None
    if not api_key:
        _record_error(command, YouTubeClientError(None, "youtube_api_key_missing"), session_factory)
        return
    client = client or YouTubeClient(api_key=api_key, timeout_seconds=settings.youtube_request_timeout_seconds)
    try:
        response = client.fetch_view_counts([item.video_id for item in command.items])
    except YouTubeClientError as error:
        _record_error(command, error, session_factory)
        return
    _apply_success(command, response, settings, session_factory)
