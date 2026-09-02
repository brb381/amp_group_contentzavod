import json
import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.youtube.schemas import YouTubeViewCountsResponse, YouTubeVideosResponse


@dataclass(frozen=True)
class YouTubeClientError(Exception):
    status_code: int | None
    reason: str
    retry_after: str | None = None


def _error_reason(body: bytes) -> str:
    try:
        payload = json.loads(body)
        errors = payload.get("error", {}).get("errors", [])
        if errors and isinstance(errors[0].get("reason"), str):
            return errors[0]["reason"]
        status = payload.get("error", {}).get("status")
        if isinstance(status, str):
            return status
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        pass
    return "youtube_http_error"


class YouTubeClient:
    def __init__(self, *, api_key: str, timeout_seconds: int) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def fetch_videos(self, video_ids: list[str]) -> YouTubeVideosResponse:
        body = self._fetch(video_ids, part="snippet,contentDetails")
        try:
            return YouTubeVideosResponse.model_validate_json(body)
        except ValidationError as error:
            raise YouTubeClientError(status_code=None, reason="youtube_response_invalid") from error

    def fetch_view_counts(self, video_ids: list[str]) -> YouTubeViewCountsResponse:
        body = self._fetch(video_ids, part="statistics")
        try:
            return YouTubeViewCountsResponse.model_validate_json(body)
        except ValidationError as error:
            raise YouTubeClientError(status_code=None, reason="youtube_response_invalid") from error

    def _fetch(self, video_ids: list[str], *, part: str) -> bytes:
        query = urlencode(
            {
                "part": part,
                "id": ",".join(video_ids),
                "key": self._api_key,
            }
        )
        request = Request(
            f"https://www.googleapis.com/youtube/v3/videos?{query}",
            headers={"Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read()
        except HTTPError as error:
            body = error.read()
            raise YouTubeClientError(
                status_code=error.code,
                reason=_error_reason(body),
                retry_after=error.headers.get("Retry-After"),
            ) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise YouTubeClientError(status_code=None, reason="youtube_unreachable") from error

        return body
