import json
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.tiktok.schemas import TikTokOEmbedResponse, TikTokPublicVideo


@dataclass(frozen=True)
class TikTokClientError(Exception):
    status_code: int | None
    reason: str
    retry_after: str | None = None


class _RehydrationScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capturing = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == "__UNIVERSAL_DATA_FOR_REHYDRATION__":
            self._capturing = True

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capturing:
            self._capturing = False


class TikTokClient:
    def __init__(self, *, timeout_seconds: int) -> None:
        self._timeout_seconds = timeout_seconds

    def fetch_publication(self, source_url: str) -> TikTokOEmbedResponse:
        query = urlencode({"url": source_url})
        request = Request(
            f"https://www.tiktok.com/oembed?{query}",
            headers={"Accept": "application/json", "User-Agent": "AMPContentFactory/1.0"},
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read()
        except HTTPError as error:
            raise TikTokClientError(
                status_code=error.code,
                reason="tiktok_not_found" if error.code in {400, 404} else "tiktok_http_error",
                retry_after=error.headers.get("Retry-After"),
            ) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise TikTokClientError(None, "tiktok_unreachable") from error

        try:
            response = TikTokOEmbedResponse.model_validate_json(body)
        except ValidationError as error:
            raise TikTokClientError(None, "tiktok_response_invalid") from error
        if response.provider_name.casefold() != "tiktok":
            raise TikTokClientError(None, "tiktok_response_invalid")
        return response

    def fetch_public_stats(self, source_url: str) -> TikTokPublicVideo:
        request = Request(
            source_url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
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
            raise TikTokClientError(
                error.code,
                "tiktok_not_found" if error.code in {400, 404} else "tiktok_http_error",
                error.headers.get("Retry-After"),
            ) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise TikTokClientError(None, "tiktok_unreachable") from error

        try:
            parser = _RehydrationScriptParser()
            parser.feed(body.decode("utf-8"))
            payload = json.loads("".join(parser.parts))
            video = payload["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
            return TikTokPublicVideo.model_validate(video)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValidationError) as error:
            raise TikTokClientError(None, "tiktok_response_invalid") from error
