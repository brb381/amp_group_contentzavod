import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from sqlalchemy import select

from app.clock import utc_now
from app.content.models import Publication, PublicationAvailability, PublicationEnrichmentStatus
from app.contracts import VKEnrichmentCommand
from app.external_jobs import MAX_EXTERNAL_JOB_ATTEMPTS
from app.integrations.models import ExternalProviderState
from app.platforms import Platform
from app.vk.client import VKClient, VKClientError
from app.vk.models import VKEnrichmentJob
from app.vk_config import VKWorkerSettings


logger = logging.getLogger(__name__)
WORKER_UNEXPECTED_ERROR = "worker_unexpected_error"
TRANSIENT_REASONS = {"vk_unreachable", "vk_response_invalid"}


def _retry_after(value: str | None, *, now: datetime) -> datetime | None:
    if not value:
        return None
    try:
        return now + timedelta(seconds=max(1, int(value)))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value).astimezone(timezone.utc)
            return parsed if parsed > now else now + timedelta(seconds=60)
        except (TypeError, ValueError):
            return None


def _provider_state(db) -> ExternalProviderState:
    state = db.get(ExternalProviderState, "vk")
    if not state:
        state = ExternalProviderState(provider="vk")
        db.add(state)
    return state


def _claim(command: VKEnrichmentCommand, session_factory) -> bool:
    db = session_factory()
    try:
        job = db.scalar(
            select(VKEnrichmentJob)
            .where(
                VKEnrichmentJob.id == command.job_id,
                VKEnrichmentJob.publication_id == command.publication_id,
                VKEnrichmentJob.dispatch_id == command.dispatch_id,
                VKEnrichmentJob.state == "queued",
            )
            .with_for_update()
        )
        if not job:
            db.rollback()
            return False
        job.state = "processing"
        publication = db.get(Publication, job.publication_id)
        if publication:
            publication.enrichment_status = PublicationEnrichmentStatus.PROCESSING
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _finish_unavailable(command: VKEnrichmentCommand, session_factory) -> None:
    db = session_factory()
    try:
        job = db.scalar(
            select(VKEnrichmentJob)
            .where(
                VKEnrichmentJob.id == command.job_id,
                VKEnrichmentJob.dispatch_id == command.dispatch_id,
                VKEnrichmentJob.state == "processing",
            )
            .with_for_update()
        )
        if not job:
            return
        publication = db.get(Publication, job.publication_id)
        if publication:
            publication.enrichment_status = PublicationEnrichmentStatus.SUCCEEDED
            publication.enrichment_error_code = None
            publication.availability = PublicationAvailability.UNAVAILABLE
            publication.external_title = None
            publication.external_author_id = None
            publication.external_author_name = None
            publication.external_published_at = None
            publication.external_duration_seconds = None
            publication.external_thumbnail_url = None
            publication.external_etag = None
            publication.enriched_at = utc_now()
        job.state = "succeeded"
        job.last_error_code = "vk_not_found"
        job.lease_until = None
        job.dispatch_id = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _record_error(
    command: VKEnrichmentCommand,
    error: VKClientError,
    session_factory,
) -> None:
    db = session_factory()
    try:
        job = db.scalar(
            select(VKEnrichmentJob)
            .where(
                VKEnrichmentJob.id == command.job_id,
                VKEnrichmentJob.dispatch_id == command.dispatch_id,
                VKEnrichmentJob.state == "processing",
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
            provider = _provider_state(db)
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
        if publication:
            publication.enrichment_status = (
                PublicationEnrichmentStatus.RETRY_WAIT
                if should_retry
                else PublicationEnrichmentStatus.FAILED
            )
            publication.enrichment_error_code = error.reason
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _apply_success(command: VKEnrichmentCommand, response, session_factory) -> None:
    db = session_factory()
    try:
        job = db.scalar(
            select(VKEnrichmentJob)
            .where(
                VKEnrichmentJob.id == command.job_id,
                VKEnrichmentJob.dispatch_id == command.dispatch_id,
                VKEnrichmentJob.state == "processing",
            )
            .with_for_update()
        )
        if not job:
            return
        publication = db.get(Publication, job.publication_id)
        stale = (
            not publication
            or publication.platform != Platform.VK
            or publication.submitted_url != command.source_url
        )
        if stale:
            job.state = "failed"
            job.last_error_code = "stale_command"
            if publication:
                publication.enrichment_status = PublicationEnrichmentStatus.FAILED
                publication.enrichment_error_code = "stale_command"
        else:
            publication.enrichment_status = PublicationEnrichmentStatus.SUCCEEDED
            publication.enrichment_error_code = None
            publication.availability = PublicationAvailability.AVAILABLE
            publication.external_title = response.title
            publication.external_author_id = None
            publication.external_author_name = response.author_name
            publication.external_thumbnail_url = str(response.thumbnail_url)
            publication.enriched_at = utc_now()
            job.state = "succeeded"
            job.last_error_code = None
            provider = _provider_state(db)
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


def execute_vk_enrichment(
    command: VKEnrichmentCommand,
    settings: VKWorkerSettings,
    session_factory,
    client: VKClient | None = None,
) -> None:
    if not _claim(command, session_factory):
        return
    try:
        client = client or VKClient(timeout_seconds=settings.vk_request_timeout_seconds)
        try:
            response = client.fetch_publication(command.source_url)
        except VKClientError as error:
            if error.reason == "vk_not_found":
                _finish_unavailable(command, session_factory)
            else:
                _record_error(command, error, session_factory)
            return
        _apply_success(command, response, session_factory)
    except Exception:
        logger.exception(
            "Unexpected VK enrichment worker error",
            extra={"dispatch_id": str(command.dispatch_id)},
        )
        _record_error(command, VKClientError(500, WORKER_UNEXPECTED_ERROR), session_factory)
