from datetime import datetime, timedelta, timezone

from app.instagram.client import InstagramClientError
from app.external_jobs import MAX_EXTERNAL_JOB_ATTEMPTS
from app.instagram.service import (
    blocks_provider,
    is_retryable_error,
    retry_at_for_error,
    should_retry_error,
)


def test_instagram_unauthorized_response_uses_provider_backoff():
    error = InstagramClientError(401, "instagram_http_error")

    assert is_retryable_error(error)
    assert blocks_provider(error)


def test_instagram_unauthorized_response_has_shared_minimum_backoff():
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    error = InstagramClientError(401, "instagram_http_error", retry_after="60")

    assert retry_at_for_error(error, now=now, attempt_count=1) == now + timedelta(
        minutes=15
    )


def test_instagram_rate_limit_respects_retry_after():
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    error = InstagramClientError(429, "instagram_http_error", retry_after="600")

    assert retry_at_for_error(error, now=now, attempt_count=1) == now + timedelta(
        minutes=10
    )


def test_instagram_provider_throttle_does_not_exhaust_publication_job():
    error = InstagramClientError(401, "instagram_http_error")

    assert should_retry_error(error, attempt_count=MAX_EXTERNAL_JOB_ATTEMPTS)


def test_instagram_content_error_still_has_bounded_retries():
    error = InstagramClientError(None, "instagram_response_invalid")

    assert not should_retry_error(error, attempt_count=MAX_EXTERNAL_JOB_ATTEMPTS)


def test_invalid_single_response_retries_without_blocking_all_jobs():
    error = InstagramClientError(None, "instagram_response_invalid")

    assert is_retryable_error(error)
    assert not blocks_provider(error)


def test_not_found_response_is_permanent():
    error = InstagramClientError(404, "instagram_not_found")

    assert not is_retryable_error(error)
    assert not blocks_provider(error)
