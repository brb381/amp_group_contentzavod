import json

import pytest

from app.instagram.client import InstagramClient, InstagramClientError


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


def _html(shortcode: str) -> bytes:
    payload = {
        "items": [{
            "code": shortcode,
            "video_play_count": 456_789,
            "display_url": "https://cdninstagram.com/preview.jpg",
            "owner": {"username": "creator", "full_name": "Автор"},
            "caption": {"text": "Тестовый ролик"},
        }]
    }
    return (
        '<meta property="og:title" content="Тестовый ролик">'
        '<meta property="og:image" content="https://cdninstagram.com/preview.jpg">'
        f'<script type="application/json">{json.dumps(payload)}</script>'
    ).encode()


def test_client_reads_matching_public_reel(monkeypatch):
    monkeypatch.setattr(
        "app.instagram.client.urlopen",
        lambda request, timeout: FakeResponse(_html("ABC_123")),
    )

    result = InstagramClient(timeout_seconds=5).fetch_public_stats(
        "https://www.instagram.com/reel/ABC_123/"
    )

    assert result.id == "ABC_123"
    assert result.stats.playCount == 456_789
    assert result.author_name == "Автор"


def test_client_rejects_payload_for_another_reel(monkeypatch):
    monkeypatch.setattr(
        "app.instagram.client.urlopen",
        lambda request, timeout: FakeResponse(_html("OTHER")),
    )

    with pytest.raises(InstagramClientError) as error:
        InstagramClient(timeout_seconds=5).fetch_public_stats(
            "https://www.instagram.com/reel/ABC_123/"
        )

    assert error.value.reason == "instagram_response_invalid"
