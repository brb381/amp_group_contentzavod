import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import select

from app.clock import utc_now
from app.youtube_config import YouTubeWorkerSettings
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationEnrichmentStatus,
)
from app.contracts import YouTubeEnrichmentCommand
from app.youtube.client import YouTubeClient, YouTubeClientError
from app.youtube.models import ExternalProviderState, YouTubeEnrichmentJob
from app.youtube.time import next_pacific_reset


DAILY_LIMIT_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
CONFIGURATION_REASONS = {
    "accessNotConfigured",
    "API_KEY_INVALID",
    "keyInvalid",
    "youtube_api_key_missing",
}
TRANSIENT_REASONS = {
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "servingLimitExceeded",
    "youtube_unreachable",
    "youtube_response_invalid",
}
MAX_TRANSIENT_ATTEMPTS = 8
ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def _duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    match = ISO_DURATION.fullmatch(value)
    if not match:
        return None
    parts = {key: float(raw or 0) for key, raw in match.groupdict().items()}
    return int(parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"])


def _retry_after(value: str | None, *, now: datetime) -> datetime | None:
    if not value:
        return None
    try:
        return now + timedelta(seconds=max(1, int(value)))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            parsed = parsed.astimezone(timezone.utc)
            return parsed if parsed > now else now + timedelta(seconds=60)
        except (TypeError, ValueError):
            return None


def _provider_state(db) -> ExternalProviderState:
    state = db.get(ExternalProviderState, "youtube")
    if not state:
        state = ExternalProviderState(provider="youtube")
        db.add(state)
    return state


def _claim(command: YouTubeEnrichmentCommand, session_factory) -> list[YouTubeEnrichmentJob]:
    db = session_factory()
    try:
        job_ids = [item.job_id for item in command.items]
        jobs = list(
            db.scalars(
                select(YouTubeEnrichmentJob)
                .where(
                    YouTubeEnrichmentJob.id.in_(job_ids),
                    YouTubeEnrichmentJob.dispatch_id == command.dispatch_id,
                    YouTubeEnrichmentJob.state == "queued",
                )
                .with_for_update()
            )
        )
        if len(jobs) != len(job_ids):
            db.rollback()
            return []
        for job in jobs:
            job.state = "processing"
            publication = db.get(Publication, job.publication_id)
            if publication:
                publication.enrichment_status = PublicationEnrichmentStatus.PROCESSING
        db.commit()
        return jobs
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _record_error(
    command: YouTubeEnrichmentCommand,
    error: YouTubeClientError,
    session_factory,
) -> None:
    db = session_factory()
    try:
        now = utc_now()
        jobs = list(
            db.scalars(
                select(YouTubeEnrichmentJob)
                .where(
                    YouTubeEnrichmentJob.dispatch_id == command.dispatch_id,
                    YouTubeEnrichmentJob.state == "processing",
                )
                .with_for_update()
            )
        )
        if not jobs:
            return

        is_daily = error.reason in DAILY_LIMIT_REASONS
        is_configuration = error.reason in CONFIGURATION_REASONS
        is_transient = (
            error.status_code == 429
            or (error.status_code is not None and error.status_code >= 500)
            or error.reason in TRANSIENT_REASONS
        )
        provider = _provider_state(db)

        if is_daily:
            retry_at = next_pacific_reset(now)
            provider.status = "blocked"
            provider.blocked_until = retry_at
            provider.block_reason = error.reason
        elif is_configuration:
            retry_at = now + timedelta(minutes=15)
            provider.status = "blocked"
            provider.blocked_until = retry_at
            provider.block_reason = error.reason
        elif is_transient:
            attempt = max(job.attempt_count for job in jobs)
            fallback = now + timedelta(seconds=min(1800, 60 * (2 ** max(0, attempt - 1))))
            retry_at = _retry_after(error.retry_after, now=now) or fallback
            provider.status = "blocked"
            provider.blocked_until = retry_at
            provider.block_reason = error.reason
        else:
            retry_at = None

        for job in jobs:
            publication = db.get(Publication, job.publication_id)
            should_retry = is_daily or is_configuration or (
                is_transient and job.attempt_count < MAX_TRANSIENT_ATTEMPTS
            )
            job.state = "retry_wait" if should_retry else "failed"
            job.available_at = retry_at or now
            job.lease_until = None
            job.dispatch_id = None
            job.last_error_code = error.reason
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


def _apply_success(command: YouTubeEnrichmentCommand, response, session_factory) -> None:
    videos = {video.id: video for video in response.items}
    item_by_job = {item.job_id: item for item in command.items}
    db = session_factory()
    try:
        jobs = list(
            db.scalars(
                select(YouTubeEnrichmentJob)
                .where(
                    YouTubeEnrichmentJob.dispatch_id == command.dispatch_id,
                    YouTubeEnrichmentJob.state == "processing",
                )
                .with_for_update()
            )
        )
        now = utc_now()
        for job in jobs:
            command_item = item_by_job.get(job.id)
            publication = db.get(Publication, job.publication_id)
            if not command_item or not publication or publication.external_id != command_item.video_id:
                job.state = "failed"
                job.last_error_code = "stale_command"
                job.lease_until = None
                job.dispatch_id = None
                if publication:
                    publication.enrichment_status = PublicationEnrichmentStatus.FAILED
                    publication.enrichment_error_code = "stale_command"
                continue

            video = videos.get(command_item.video_id)
            publication.enrichment_status = PublicationEnrichmentStatus.SUCCEEDED
            publication.enrichment_error_code = None
            publication.enriched_at = now
            if video is None:
                publication.availability = PublicationAvailability.UNAVAILABLE
                publication.external_title = None
                publication.external_author_id = None
                publication.external_author_name = None
                publication.external_published_at = None
                publication.external_duration_seconds = None
                publication.external_thumbnail_url = None
                publication.external_etag = None
            else:
                thumbnails = video.snippet.thumbnails
                thumbnail = next(
                    (
                        str(thumbnails[name].url)
                        for name in ("maxres", "standard", "high", "medium", "default")
                        if name in thumbnails
                    ),
                    None,
                )
                publication.availability = PublicationAvailability.AVAILABLE
                publication.external_title = video.snippet.title
                publication.external_author_id = video.snippet.channelId
                publication.external_author_name = video.snippet.channelTitle
                publication.external_published_at = video.snippet.publishedAt
                publication.external_duration_seconds = _duration_seconds(
                    video.contentDetails.duration if video.contentDetails else None
                )
                publication.external_thumbnail_url = thumbnail
                publication.external_etag = video.etag
            job.state = "succeeded"
            job.last_error_code = None
            job.lease_until = None
            job.dispatch_id = None

        provider = _provider_state(db)
        provider.status = "available"
        provider.blocked_until = None
        provider.block_reason = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_youtube_enrichment(
    command: YouTubeEnrichmentCommand,
    settings: YouTubeWorkerSettings,
    session_factory,
    client: YouTubeClient | None = None,
) -> None:
    jobs = _claim(command, session_factory)
    if not jobs:
        return
    api_key = settings.youtube_api_key.get_secret_value() if settings.youtube_api_key else None
    if not api_key:
        _record_error(
            command,
            YouTubeClientError(None, "youtube_api_key_missing"),
            session_factory,
        )
        return
    client = client or YouTubeClient(
        api_key=api_key,
        timeout_seconds=settings.youtube_request_timeout_seconds,
    )
    try:
        response = client.fetch_videos([item.video_id for item in command.items])
    except YouTubeClientError as error:
        _record_error(command, error, session_factory)
        return
    _apply_success(command, response, session_factory)
