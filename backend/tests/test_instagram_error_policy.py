from app.instagram.client import InstagramClientError
from app.instagram.service import blocks_provider, is_retryable_error


def test_instagram_unauthorized_response_uses_provider_backoff():
    error = InstagramClientError(401, "instagram_http_error")

    assert is_retryable_error(error)
    assert blocks_provider(error)


def test_invalid_single_response_retries_without_blocking_all_jobs():
    error = InstagramClientError(None, "instagram_response_invalid")

    assert is_retryable_error(error)
    assert not blocks_provider(error)


def test_not_found_response_is_permanent():
    error = InstagramClientError(404, "instagram_not_found")

    assert not is_retryable_error(error)
    assert not blocks_provider(error)
