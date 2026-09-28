import logging
from datetime import timedelta

from sqlalchemy import select

import app.billing.models  # noqa: F401 - registers calculation_periods FK target
import app.creators.models  # noqa: F401 - registers social_accounts FK target

from app.clock import utc_now
from app.program.service import suspicious_growth_threshold
from app.content.models import Publication, PublicationAvailability
from app.contracts import DzenViewCollectionCommand
from app.external_jobs import MAX_EXTERNAL_JOB_ATTEMPTS
from app.integrations.models import ExternalProviderState
from app.platforms import Platform
from app.readings.models import ReadingSource, ReadingStatus, ViewReading, ViewReadingHistory
from app.readings.policy import risk_flags
from app.readings.view_slots import view_reading_idempotency_key
from app.readings.revision import lock_reading_dataset_revision
from app.dzen.client import DzenClient, DzenClientError
from app.dzen.models import DzenViewCollectionJob
from app.dzen.service import (
    TRANSIENT_REASONS,
    WORKER_UNEXPECTED_ERROR,
    _retry_after,
)
from app.dzen_config import DzenWorkerSettings


logger = logging.getLogger(__name__)


def _provider(db) -> ExternalProviderState:
    provider = db.get(ExternalProviderState, "dzen")
    if not provider:
        provider = ExternalProviderState(provider="dzen")
        db.add(provider)
    return provider


def _claim(command: DzenViewCollectionCommand, session_factory) -> bool:
    db = session_factory()
    try:
        job = db.scalar(
            select(DzenViewCollectionJob)
            .where(
                DzenViewCollectionJob.id == command.job_id,
                DzenViewCollectionJob.publication_id == command.publication_id,
                DzenViewCollectionJob.dispatch_id == command.dispatch_id,
                DzenViewCollectionJob.state == "queued",
            )
            .with_for_update()
        )
        if not job:
            db.rollback()
            return False
        job.state = "processing"
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _record_error(
    command: DzenViewCollectionCommand,
    error: DzenClientError,
    session_factory,
) -> None:
    db = session_factory()
    try:
        job = db.scalar(
            select(DzenViewCollectionJob)
            .where(
                DzenViewCollectionJob.id == command.job_id,
                DzenViewCollectionJob.dispatch_id == command.dispatch_id,
                DzenViewCollectionJob.state == "processing",
            )
            .with_for_update()
        )
        if not job:
            return
        now = utc_now()
        transient = (
            error.status_code in {403, 429}
            or (error.status_code is not None and error.status_code >= 500)
            or error.reason in TRANSIENT_REASONS
        )
        retry_at = None
        if transient:
            fallback = now + timedelta(seconds=min(1800, 60 * (2 ** max(0, job.attempt_count - 1))))
            retry_at = _retry_after(error.retry_after, now=now) or fallback
            provider = _provider(db)
            provider.status = "blocked"
            provider.blocked_until = retry_at
            provider.block_reason = error.reason
        should_retry = transient and job.attempt_count < MAX_EXTERNAL_JOB_ATTEMPTS
        job.state = "retry_wait" if should_retry else "failed"
        job.available_at = retry_at or now
        job.lease_until = None
        job.dispatch_id = None
        job.last_error_code = error.reason
        publication = db.get(Publication, job.publication_id)
        if publication and error.reason == "dzen_not_found":
            publication.availability = PublicationAvailability.UNAVAILABLE
            publication.external_title = None
            publication.external_author_id = None
            publication.external_author_name = None
            publication.external_published_at = None
            publication.external_duration_seconds = None
            publication.external_thumbnail_url = None
            publication.external_etag = None
            publication.enriched_at = now
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _apply_success(
    command: DzenViewCollectionCommand,
    response,
    settings: DzenWorkerSettings,
    session_factory,
) -> None:
    db = session_factory()
    try:
        job = db.scalar(
            select(DzenViewCollectionJob)
            .where(
                DzenViewCollectionJob.id == command.job_id,
                DzenViewCollectionJob.dispatch_id == command.dispatch_id,
                DzenViewCollectionJob.state == "processing",
            )
            .with_for_update()
        )
        if not job:
            return
        publication = db.get(Publication, job.publication_id)
        if (
            not publication
            or publication.platform != Platform.DZEN
            or publication.submitted_url != command.source_url
        ):
            job.state = "failed"
            job.last_error_code = "stale_command"
        elif publication.external_id and publication.external_id != response.id:
            job.state = "failed"
            job.last_error_code = "dzen_video_id_mismatch"
        else:
            revision = lock_reading_dataset_revision(db)
            period = job.collection_date.replace(day=1)
            if revision.closed_through_period is not None and period <= revision.closed_through_period:
                job.state = "failed"
                job.last_error_code = "reading_period_financially_closed"
            else:
                key = view_reading_idempotency_key("dzen-public", job.collection_date, job.collection_slot)
                reading = db.scalar(
                    select(ViewReading).where(
                        ViewReading.publication_id == publication.id,
                        ViewReading.idempotency_key == key,
                    )
                )
                if not reading:
                    value = response.stats.playCount
                    flags = risk_flags(
                        db,
                        publication_id=publication.id,
                        period=period,
                        value=value,
                        suspicious_growth_threshold=suspicious_growth_threshold(db, settings.suspicious_monthly_view_growth),
                    )
                    flags = sorted(set([*flags, "approximate_public_counter"]))
                    reading = ViewReading(
                        publication_id=publication.id,
                        reporting_period=period,
                        source=ReadingSource.DZEN_PUBLIC,
                        reported_value=value,
                        accepted_value=None,
                        status=ReadingStatus.PENDING,
                        risk_flags=flags,
                        idempotency_key=key,
                        captured_at=utc_now(),
                    )
                    db.add(reading)
                    db.flush()
                    db.add(
                        ViewReadingHistory(
                            reading_id=reading.id,
                            action="created",
                            new_value=value,
                            reason="Approximate public Dzen counter",
                        )
                    )
                    revision.revision += 1
                publication.availability = PublicationAvailability.AVAILABLE
                job.state = "succeeded"
                job.last_error_code = None
                provider = _provider(db)
                provider.status = "available"
                provider.blocked_until = None
                provider.block_reason = None
        job.lease_until = None
        job.dispatch_id = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_dzen_view_collection(
    command: DzenViewCollectionCommand,
    settings: DzenWorkerSettings,
    session_factory,
    client: DzenClient | None = None,
) -> None:
    if not _claim(command, session_factory):
        return
    try:
        client = client or DzenClient(timeout_seconds=settings.dzen_request_timeout_seconds)
        try:
            response = client.fetch_public_stats(command.source_url)
        except DzenClientError as error:
            _record_error(command, error, session_factory)
            return
        _apply_success(command, response, settings, session_factory)
    except Exception:
        logger.exception(
            "Unexpected Dzen view worker error",
            extra={"dispatch_id": str(command.dispatch_id)},
        )
        _record_error(command, DzenClientError(500, WORKER_UNEXPECTED_ERROR), session_factory)
