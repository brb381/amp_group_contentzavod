import json
from urllib.parse import parse_qs

import pytest

from app.instagram.client import CLIPS_DOC_ID, MEDIA_DOC_ID, InstagramClient, InstagramClientError


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


def _metadata(shortcode: str) -> bytes:
    return json.dumps(
        {
            "data": {
                "xdt_api__v1__media__shortcode__web_info": {
                    "items": [
                        {
                            "code": shortcode,
                            "image_versions2": {
                                "candidates": [{"url": "https://cdninstagram.com/preview.jpg"}]
                            },
                            "user": {
                                "pk": "42",
                                "username": "creator",
                                "full_name": "Author",
                            },
                            "caption": {"text": "Test reel"},
                        }
                    ]
                }
            }
        }
    ).encode()


def _clips(shortcode: str, play_count: int) -> bytes:
    return json.dumps(
        {
            "data": {
                "xdt_api__v1__clips__user__connection_v2": {
                    "edges": [
                        {"node": {"media": {"code": shortcode, "play_count": play_count}}}
                    ]
                }
            }
        }
    ).encode()


def _doc_id(request) -> str:
    return parse_qs(request.data.decode())["doc_id"][0]


def test_client_reads_public_reel_metadata_and_views():
    opener = FakeOpener(b"page shell", _metadata("ABC_123"), _clips("ABC_123", 456_789))

    result = InstagramClient(timeout_seconds=5, opener=opener).fetch_public_stats(
        "https://www.instagram.com/reel/ABC_123/"
    )

    assert result.id == "ABC_123"
    assert result.stats.playCount == 456_789
    assert result.author_name == "Author"
    assert str(result.thumbnail_url) == "https://cdninstagram.com/preview.jpg"
    assert [_doc_id(request) for request in opener.requests[1:]] == [MEDIA_DOC_ID, CLIPS_DOC_ID]


def test_enrichment_does_not_require_view_counter():
    opener = FakeOpener(b"page shell", _metadata("ABC_123"))

    result = InstagramClient(timeout_seconds=5, opener=opener).fetch_publication(
        "https://www.instagram.com/reel/ABC_123/"
    )

    assert result.stats.playCount == 0
    assert len(opener.requests) == 2


def test_client_rejects_payload_for_another_reel():
    opener = FakeOpener(b"page shell", _metadata("OTHER"))

    with pytest.raises(InstagramClientError) as error:
        InstagramClient(timeout_seconds=5, opener=opener).fetch_public_stats(
            "https://www.instagram.com/reel/ABC_123/"
        )

    assert error.value.reason == "instagram_response_invalid"


def test_client_rejects_stats_when_reel_is_absent_from_public_clips():
    opener = FakeOpener(b"page shell", _metadata("ABC_123"), _clips("OTHER", 123))

    with pytest.raises(InstagramClientError) as error:
        InstagramClient(timeout_seconds=5, opener=opener).fetch_public_stats(
            "https://www.instagram.com/reel/ABC_123/"
        )

    assert error.value.reason == "instagram_response_invalid"


def test_client_does_not_treat_graphql_error_as_deleted_reel():
    error_payload = json.dumps({"errors": [{"message": "Please wait"}]}).encode()
    opener = FakeOpener(b"page shell", error_payload)

    with pytest.raises(InstagramClientError) as error:
        InstagramClient(timeout_seconds=5, opener=opener).fetch_publication(
            "https://www.instagram.com/reel/ABC_123/"
        )

    assert error.value.reason == "instagram_response_invalid"
    assert error.value.status_code is None
