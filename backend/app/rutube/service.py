from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from sqlalchemy import select

from app.clock import utc_now
from app.content.models import Publication, PublicationAvailability, PublicationEnrichmentStatus
from app.contracts import RutubeEnrichmentCommand
from app.integrations.models import ExternalProviderState
from app.platforms import Platform
from app.rutube.client import RutubeClient, RutubeClientError
from app.rutube.models import RutubeEnrichmentJob
from app.rutube.schemas import RutubeVideo
from app.rutube_config import RutubeWorkerSettings


MAX_TRANSIENT_ATTEMPTS = 8
TRANSIENT_REASONS = {"rutube_unreachable", "rutube_response_invalid"}


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
    state = db.get(ExternalProviderState, "rutube")
    if not state:
        state = ExternalProviderState(provider="rutube")
        db.add(state)
    return state


def _claim(command: RutubeEnrichmentCommand, session_factory) -> bool:
    db = session_factory()
    try:
        job = db.scalar(
            select(RutubeEnrichmentJob)
            .where(
                RutubeEnrichmentJob.id == command.job_id,
                RutubeEnrichmentJob.publication_id == command.publication_id,
                RutubeEnrichmentJob.dispatch_id == command.dispatch_id,
                RutubeEnrichmentJob.state == "queued",
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


def _finish_unavailable(command: RutubeEnrichmentCommand, session_factory) -> None:
    db = session_factory()
    try:
        job = db.scalar(
            select(RutubeEnrichmentJob)
            .where(
                RutubeEnrichmentJob.id == command.job_id,
                RutubeEnrichmentJob.dispatch_id == command.dispatch_id,
                RutubeEnrichmentJob.state == "processing",
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
        job.last_error_code = "rutube_not_found"
        job.lease_until = None
        job.dispatch_id = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _record_error(
    command: RutubeEnrichmentCommand,
    error: RutubeClientError,
    session_factory,
) -> None:
    db = session_factory()
    try:
        job = db.scalar(
            select(RutubeEnrichmentJob)
            .where(
                RutubeEnrichmentJob.id == command.job_id,
                RutubeEnrichmentJob.dispatch_id == command.dispatch_id,
                RutubeEnrichmentJob.state == "processing",
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
        should_retry = transient and job.attempt_count < MAX_TRANSIENT_ATTEMPTS
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


def _apply_success(
    command: RutubeEnrichmentCommand,
    response: RutubeVideo,
    session_factory,
) -> None:
    db = session_factory()
    try:
        job = db.scalar(
            select(RutubeEnrichmentJob)
            .where(
                RutubeEnrichmentJob.id == command.job_id,
                RutubeEnrichmentJob.dispatch_id == command.dispatch_id,
                RutubeEnrichmentJob.state == "processing",
            )
            .with_for_update()
        )
        if not job:
            return
        publication = db.get(Publication, job.publication_id)
        stale = (
            not publication
            or publication.platform != Platform.RUTUBE
            or publication.submitted_url != command.source_url
            or publication.external_id != command.external_id
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
            publication.external_author_id = (
                str(response.author.id)
                if response.author and response.author.id is not None
                else None
            )
            publication.external_author_name = response.author.name if response.author else None
            publication.external_published_at = response.created_ts
            publication.external_duration_seconds = response.duration
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


def execute_rutube_enrichment(
    command: RutubeEnrichmentCommand,
    settings: RutubeWorkerSettings,
    session_factory,
    client: RutubeClient | None = None,
) -> None:
    if not _claim(command, session_factory):
        return
    client = client or RutubeClient(timeout_seconds=settings.rutube_request_timeout_seconds)
    try:
        response = client.fetch_video(command.external_id)
    except RutubeClientError as error:
        if error.reason == "rutube_not_found":
            _finish_unavailable(command, session_factory)
        else:
            _record_error(command, error, session_factory)
        return
    _apply_success(command, response, session_factory)
