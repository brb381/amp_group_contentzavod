import json

import pytest

from app.dzen.client import DzenClient, DzenClientError


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


def _html(video_id: str) -> bytes:
    payload = {
        "@type": "VideoObject",
        "@id": f"https://dzen.ru/video/watch/{video_id}",
        "name": "Видео в Дзене",
        "thumbnailUrl": "https://avatars.dzeninfra.ru/preview.jpg",
        "author": {"name": "Канал", "url": "https://dzen.ru/id/test"},
        "interactionStatistic": {
            "interactionType": "https://schema.org/WatchAction",
            "userInteractionCount": 98765,
        },
    }
    return (
        '<meta property="og:title" content="Видео в Дзене">'
        '<meta property="og:image" content="https://avatars.dzeninfra.ru/preview.jpg">'
        f'<script type="application/ld+json">{json.dumps(payload)}</script>'
    ).encode()


def test_client_reads_public_video_object(monkeypatch):
    monkeypatch.setattr(
        "app.dzen.client.urlopen",
        lambda request, timeout: FakeResponse(_html("abc123")),
    )

    result = DzenClient(timeout_seconds=5).fetch_public_stats(
        "https://dzen.ru/video/watch/abc123"
    )

    assert result.id == "abc123"
    assert result.stats.playCount == 98765
    assert result.author_name == "Канал"


def test_client_rejects_untrusted_domain():
    with pytest.raises(DzenClientError) as error:
        DzenClient(timeout_seconds=5).fetch_public_stats(
            "https://example.com/video/watch/abc123"
        )

    assert error.value.reason == "dzen_url_invalid"
