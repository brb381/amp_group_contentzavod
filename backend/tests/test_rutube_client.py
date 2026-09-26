import json

import pytest

from app.rutube.client import RutubeClient, RutubeClientError


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body


def _payload(video_id: str) -> dict:
    return {
        "id": video_id,
        "title": "RUTUBE video",
        "thumbnail_url": "https://pic.rutube.ru/video/test.jpg",
        "duration": 123,
        "created_ts": "2026-09-20T10:30:00Z",
        "author": {"id": 42, "name": "Test channel"},
        "hits": 456789,
        "ignored": "forward-compatible",
    }


def test_client_reads_public_video_metadata_and_views(monkeypatch):
    video_id = "be034b80d968da98c0faf371c5090d73"

    def fake_urlopen(request, timeout):
        assert request.full_url == (
            f"https://rutube.ru/api/video/{video_id}/?format=json"
        )
        assert request.headers["Referer"] == "https://rutube.ru/"
        assert timeout == 5
        return FakeResponse(json.dumps(_payload(video_id)).encode())

    monkeypatch.setattr("app.rutube.client.urlopen", fake_urlopen)

    result = RutubeClient(timeout_seconds=5).fetch_video(video_id)

    assert result.id == video_id
    assert result.hits == 456789
    assert result.author is not None
    assert result.author.name == "Test channel"


def test_client_rejects_response_for_another_video(monkeypatch):
    requested_id = "be034b80d968da98c0faf371c5090d73"
    monkeypatch.setattr(
        "app.rutube.client.urlopen",
        lambda request, timeout: FakeResponse(
            json.dumps(_payload("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")).encode()
        ),
    )

    with pytest.raises(RutubeClientError) as error:
        RutubeClient(timeout_seconds=5).fetch_video(requested_id)

    assert error.value.reason == "rutube_video_id_mismatch"
