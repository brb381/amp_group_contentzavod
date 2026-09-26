import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.rutube.schemas import RutubeVideo


@dataclass(frozen=True)
class RutubeClientError(Exception):
    status_code: int | None
    reason: str
    retry_after: str | None = None


class RutubeClient:
    def __init__(self, *, timeout_seconds: int) -> None:
        self._timeout_seconds = timeout_seconds

    def fetch_video(self, external_id: str) -> RutubeVideo:
        safe_id = quote(external_id, safe="")
        request = Request(
            f"https://rutube.ru/api/video/{safe_id}/?format=json",
            headers={
                "Accept": "application/json",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": "https://rutube.ru/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140 Safari/537.36"
                ),
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read()
        except HTTPError as error:
            raise RutubeClientError(
                error.code,
                "rutube_not_found" if error.code in {400, 404} else "rutube_http_error",
                error.headers.get("Retry-After"),
            ) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise RutubeClientError(None, "rutube_unreachable") from error

        try:
            video = RutubeVideo.model_validate_json(body)
        except ValidationError as error:
            raise RutubeClientError(None, "rutube_response_invalid") from error
        if video.id != external_id:
            raise RutubeClientError(None, "rutube_video_id_mismatch")
        return video