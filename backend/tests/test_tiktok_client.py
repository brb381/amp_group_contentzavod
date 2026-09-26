import json

from app.tiktok.client import TikTokClient


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body


def test_public_page_parser_reads_structured_view_count(monkeypatch):
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "itemInfo": {
                    "itemStruct": {
                        "id": "750000000000000001",
                        "stats": {"playCount": 123456},
                    }
                }
            }
        }
    }
    html = (
        '<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(payload)
        + "</script></html>"
    ).encode()
    monkeypatch.setattr(
        "app.tiktok.client.urlopen",
        lambda request, timeout: FakeResponse(html),
    )

    result = TikTokClient(timeout_seconds=5).fetch_public_stats(
        "https://www.tiktok.com/@creator/video/750000000000000001"
    )

    assert result.id == "750000000000000001"
    assert result.stats.playCount == 123456
