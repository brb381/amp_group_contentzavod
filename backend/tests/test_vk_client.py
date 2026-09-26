import json

import pytest

from app.content.url_parser import parse_publication_url
from app.platforms import Platform
from app.vk.client import VKClient, VKClientError


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body


def test_vk_video_and_clip_urls_have_one_identity():
    video = parse_publication_url(Platform.VK, "https://vkvideo.ru/video-42_123?utm_source=x")
    clip = parse_publication_url(Platform.VK, "https://vk.com/clip-42_123")

    assert video.external_id == "-42_123"
    assert video.normalized_url == "https://vk.com/video-42_123"
    assert clip.external_id == video.external_id
    assert clip.normalized_url == video.normalized_url

    feed = parse_publication_url(
        Platform.VK,
        "https://vk.com/video?z=video-42_123%2Flist-feed",
    )
    assert feed.external_id == video.external_id
    assert feed.normalized_url == video.normalized_url


def test_public_player_parser_reads_structured_view_count(monkeypatch):
    source_url = "https://vk.com/video-42_123"
    player_url = "https://vk.com/video_ext.php?oid=-42&id=123&hash=abc"
    oembed = {
        "response": {
            "version": "1.0",
            "type": "video",
            "html": f'<iframe src="{player_url}"></iframe>',
            "title": "Test video",
            "author_name": "Test author",
            "provider_name": "VK Video",
            "thumbnail_url": "https://sun9.example.test/preview.jpg",
        }
    }
    prefetch = {
        "apiPrefetchCache": [
            {
                "method": "video.get",
                "response": {
                    "count": 1,
                    "items": [{"id": 123, "owner_id": -42, "views": 456789}],
                },
            }
        ]
    }
    player = (
        "<html><script>;window.cur = Object.assign(window.cur || {}, "
        + json.dumps(prefetch)
        + ");</script></html>"
    ).encode()

    def fake_urlopen(request, timeout):
        if "video.getOembed" in request.full_url:
            return FakeResponse(json.dumps(oembed).encode())
        assert request.full_url == player_url
        return FakeResponse(player)

    monkeypatch.setattr("app.vk.client.urlopen", fake_urlopen)

    result = VKClient(timeout_seconds=5).fetch_public_stats(source_url)

    assert result.external_id == "-42_123"
    assert result.views == 456789


def test_public_player_rejects_untrusted_oembed_iframe(monkeypatch):
    oembed = {
        "response": {
            "html": '<iframe src="https://attacker.example/player"></iframe>',
            "title": "Test video",
            "author_name": "Test author",
            "provider_name": "VK Video",
            "thumbnail_url": "https://sun9.example.test/preview.jpg",
        }
    }
    monkeypatch.setattr(
        "app.vk.client.urlopen",
        lambda request, timeout: FakeResponse(json.dumps(oembed).encode()),
    )

    with pytest.raises(VKClientError) as error:
        VKClient(timeout_seconds=5).fetch_public_stats("https://vk.com/video-42_123")

    assert error.value.reason == "vk_response_invalid"
