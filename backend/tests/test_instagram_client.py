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


class FakeOpener:
    def __init__(self, *responses: bytes) -> None:
        self._responses = list(responses)
        self.requests = []

    def open(self, request, timeout: int):
        self.requests.append(request)
        return FakeResponse(self._responses.pop(0))


def _embed(
    shortcode: str,
    *,
    play_count: int | None = 456_789,
    like_count: int | None = None,
) -> bytes:
    media = {
        "__typename": "GraphVideo",
        "id": "987654321",
        "shortcode": shortcode,
        "display_url": "https://cdninstagram.com/preview.jpg",
        "owner": {"id": "42", "username": "creator", "full_name": "Author"},
        "edge_media_to_caption": {"edges": [{"node": {"text": "Test reel"}}]},
    }
    if play_count is not None:
        media["video_view_count"] = play_count
    if like_count is not None:
        media["edge_liked_by"] = {"count": like_count}
    context = {"gql_data": {"shortcode_media": media}}
    server_data = {
        "define": [
            [
                "PolarisEmbedInit",
                [],
                {"contextJSON": json.dumps(context, separators=(",", ":"))},
                1,
            ]
        ]
    }
    return (
        "<html><script>requireLazy([],function(){s.handle("
        + json.dumps(server_data, separators=(",", ":"))
        + ");});</script></html>"
    ).encode()


def test_client_reads_public_embed_metadata_and_views_in_one_request():
    opener = FakeOpener(_embed("ABC_123"))

    result = InstagramClient(timeout_seconds=5, opener=opener).fetch_public_stats(
        "https://www.instagram.com/reel/ABC_123/"
    )

    assert result.id == "ABC_123"
    assert result.stats.playCount == 456_789
    assert result.author_name == "Author"
    assert str(result.thumbnail_url) == "https://cdninstagram.com/preview.jpg"
    assert len(opener.requests) == 1
    assert opener.requests[0].full_url.endswith("/reel/ABC_123/embed/captioned/")
    assert opener.requests[0].get_header("Sec-fetch-dest") == "iframe"


def test_enrichment_does_not_require_view_counter():
    opener = FakeOpener(_embed("ABC_123", play_count=None))

    result = InstagramClient(timeout_seconds=5, opener=opener).fetch_publication(
        "https://www.instagram.com/reel/ABC_123/"
    )

    assert result.stats.playCount == 0
    assert len(opener.requests) == 1


def test_view_collection_requires_public_view_counter():
    opener = FakeOpener(_embed("ABC_123", play_count=None))

    with pytest.raises(InstagramClientError) as error:
        InstagramClient(timeout_seconds=5, opener=opener).fetch_public_stats(
            "https://www.instagram.com/reel/ABC_123/"
        )

    assert error.value.reason == "instagram_response_invalid"


def test_view_collection_rejects_counter_below_public_engagement():
    opener = FakeOpener(_embed("ABC_123", play_count=1, like_count=11))

    with pytest.raises(InstagramClientError) as error:
        InstagramClient(timeout_seconds=5, opener=opener).fetch_public_stats(
            "https://www.instagram.com/reel/ABC_123/"
        )

    assert error.value.reason == "instagram_response_invalid"


def test_client_rejects_payload_for_another_reel():
    opener = FakeOpener(_embed("OTHER"))

    with pytest.raises(InstagramClientError) as error:
        InstagramClient(timeout_seconds=5, opener=opener).fetch_publication(
            "https://www.instagram.com/reel/ABC_123/"
        )

    assert error.value.reason == "instagram_response_invalid"


def test_client_rejects_page_without_embed_context():
    opener = FakeOpener(b"<html>login page</html>")

    with pytest.raises(InstagramClientError) as error:
        InstagramClient(timeout_seconds=5, opener=opener).fetch_publication(
            "https://www.instagram.com/reel/ABC_123/"
        )

    assert error.value.reason == "instagram_response_invalid"
    assert error.value.status_code is None


def test_client_ignores_unrelated_context_before_media():
    body = _embed("ABC_123")
    unrelated = json.dumps(
        {
            "define": [
                [
                    "OtherInit",
                    [],
                    {"contextJSON": json.dumps({"feature": True})},
                    1,
                ]
            ]
        }
    ).encode()
    body = b"<script>s.handle(" + unrelated + b");</script>" + body

    result = InstagramClient(
        timeout_seconds=5, opener=FakeOpener(body)
    ).fetch_publication("https://www.instagram.com/reel/ABC_123/")

    assert result.id == "ABC_123"


def test_client_treats_explicitly_missing_embed_media_as_not_found():
    context = {"gql_data": {"shortcode_media": None}}
    server_data = {
        "define": [
            [
                "PolarisEmbedInit",
                [],
                {"contextJSON": json.dumps(context, separators=(",", ":"))},
                1,
            ]
        ]
    }
    body = b"<script>s.handle(" + json.dumps(server_data).encode() + b");</script>"

    with pytest.raises(InstagramClientError) as error:
        InstagramClient(
            timeout_seconds=5, opener=FakeOpener(body)
        ).fetch_publication("https://www.instagram.com/reel/ABC_123/")

    assert error.value.reason == "instagram_not_found"
    assert error.value.status_code == 404
