from datetime import datetime, timedelta, timezone

from app.scheduling.instagram import (
    INSTAGRAM_MIN_REQUEST_INTERVAL,
    _request_interval_elapsed,
)


class FakeDatabase:
    def __init__(self, *values):
        self.values = iter(values)

    def scalar(self, _query):
        return next(self.values)


def test_instagram_dispatch_is_allowed_without_previous_attempts():
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)

    assert _request_interval_elapsed(FakeDatabase(None, None), now)


def test_instagram_dispatch_waits_for_request_interval():
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    last_attempt = now - INSTAGRAM_MIN_REQUEST_INTERVAL + timedelta(seconds=1)

    assert not _request_interval_elapsed(
        FakeDatabase(last_attempt, None),
        now,
    )


def test_instagram_dispatch_resumes_after_request_interval():
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    last_attempt = now - INSTAGRAM_MIN_REQUEST_INTERVAL

    assert _request_interval_elapsed(
        FakeDatabase(None, last_attempt),
        now,
    )
