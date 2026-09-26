import json
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.vk.schemas import VKOEmbedResponse, VKPublicVideo


@dataclass(frozen=True)
class VKClientError(Exception):
    status_code: int | None
    reason: str
    retry_after: str | None = None


class _IframeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.source_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "iframe" and self.source_url is None:
            self.source_url = dict(attrs).get("src")


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._capturing = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self._capturing = True

    def handle_data(self, data: str) -> None:
        if self._capturing and "apiPrefetchCache" in data:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._capturing = False


def _embed_url(html: str) -> str:
    parser = _IframeParser()
    parser.feed(html)
    if not parser.source_url:
        raise ValueError("VK oEmbed response has no iframe")
    parsed = urlsplit(parser.source_url)
    if parsed.scheme != "https" or parsed.hostname != "vk.com":
        raise ValueError("VK oEmbed response has an untrusted iframe URL")
    return parser.source_url


def _video_from_player_html(body: bytes) -> VKPublicVideo:
    parser = _ScriptParser()
    try:
        html = body.decode("utf-8")
    except UnicodeDecodeError:
        html = body.decode("windows-1251")
    parser.feed(html)
    marker = "Object.assign(window.cur || {}, "
    for script in parser.parts:
        start = script.find(marker)
        if start < 0:
            continue
        payload, _ = json.JSONDecoder().raw_decode(script[start + len(marker) :])
        for entry in payload.get("apiPrefetchCache", []):
            if entry.get("method") != "video.get":
                continue
            items = entry.get("response", {}).get("items", [])
            if items:
                return VKPublicVideo.model_validate(items[0])
    raise ValueError("VK player response has no video payload")


class VKClient:
    def __init__(self, *, timeout_seconds: int) -> None:
        self._timeout_seconds = timeout_seconds

    def _read(self, url: str, *, accept: str) -> bytes:
        request = Request(
            url,
            headers={
                "Accept": accept,
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140 Safari/537.36"
                ),
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                return response.read()
        except HTTPError as error:
            raise VKClientError(
                error.code,
                "vk_not_found" if error.code in {400, 404} else "vk_http_error",
                error.headers.get("Retry-After"),
            ) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise VKClientError(None, "vk_unreachable") from error

    def fetch_publication(self, source_url: str) -> VKOEmbedResponse:
        query = urlencode({"url": source_url, "v": "5.199"})
        body = self._read(
            f"https://api.vk.com/method/video.getOembed?{query}",
            accept="application/json",
        )
        try:
            payload = json.loads(body)
            if "error" in payload:
                code = int(payload["error"].get("error_code", 0))
                if code in {6, 29}:
                    raise VKClientError(429, "vk_rate_limited")
                if code in {10}:
                    raise VKClientError(503, "vk_api_unavailable")
                reason = "vk_not_found" if code in {100, 104, 113} else "vk_api_error"
                raise VKClientError(404 if reason == "vk_not_found" else None, reason)
            response = VKOEmbedResponse.model_validate(payload["response"])
        except VKClientError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, ValidationError) as error:
            raise VKClientError(None, "vk_response_invalid") from error
        if response.provider_name.casefold() != "vk video":
            raise VKClientError(None, "vk_response_invalid")
        return response

    def fetch_public_stats(self, source_url: str) -> VKPublicVideo:
        response = self.fetch_publication(source_url)
        try:
            player_url = _embed_url(response.html)
            body = self._read(player_url, accept="text/html,application/xhtml+xml")
            return _video_from_player_html(body)
        except VKClientError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, ValidationError) as error:
            raise VKClientError(None, "vk_response_invalid") from error
