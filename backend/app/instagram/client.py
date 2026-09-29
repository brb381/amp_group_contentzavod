import json
import socket
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from pydantic import ValidationError

from app.instagram.schemas import InstagramPublicStats, InstagramPublicVideo
from app.public_page import MAX_PUBLIC_PAGE_BYTES, nonnegative_int

ALLOWED_HOSTS = {"instagram.com", "www.instagram.com"}
GRAPHQL_URL = "https://www.instagram.com/graphql/query"
MEDIA_DOC_ID = "27128499623469141"
CLIPS_DOC_ID = "27234427476213202"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class InstagramClientError(Exception):
    status_code: int | None
    reason: str
    retry_after: str | None = None


def _caption(item: dict[str, Any]) -> str | None:
    caption = item.get("caption")
    if isinstance(caption, dict):
        value = caption.get("text")
        return str(value) if value else None
    return str(caption) if caption else None


def _thumbnail(item: dict[str, Any]) -> str | None:
    candidates = (item.get("image_versions2") or {}).get("candidates") or []
    if candidates and isinstance(candidates[0], dict):
        url = candidates[0].get("url")
        if url:
            return str(url)
    value = item.get("display_uri") or item.get("display_url")
    return str(value) if value else None


def _direct_play_count(item: dict[str, Any]) -> int | None:
    return next(
        (
            value
            for key in ("play_count", "view_count", "video_play_count", "video_view_count")
            if (value := nonnegative_int(item.get(key))) is not None
        ),
        None,
    )


class InstagramClient:
    def __init__(self, *, timeout_seconds: int, opener=None) -> None:
        self._timeout_seconds = timeout_seconds
        self._cookies = CookieJar()
        self._opener = opener or build_opener(HTTPCookieProcessor(self._cookies))

    @staticmethod
    def _source(source_url: str) -> tuple[str, str]:
        parsed = urlsplit(source_url)
        if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in ALLOWED_HOSTS:
            raise InstagramClientError(None, "instagram_url_invalid")
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2 or parts[0].casefold() not in {"p", "reel", "reels", "tv"}:
            raise InstagramClientError(None, "instagram_url_invalid")
        return parts[1], f"https://www.instagram.com/{parts[0]}/{parts[1]}/"

    def _read(self, request: Request) -> bytes:
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                body = response.read(MAX_PUBLIC_PAGE_BYTES + 1)
        except HTTPError as error:
            reason = "instagram_not_found" if error.code in {400, 404, 410} else "instagram_http_error"
            raise InstagramClientError(
                error.code, reason, error.headers.get("Retry-After")
            ) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise InstagramClientError(None, "instagram_unreachable") from error
        if len(body) > MAX_PUBLIC_PAGE_BYTES:
            raise InstagramClientError(None, "instagram_response_too_large")
        return body

    def _headers(self, *, referer: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "User-Agent": USER_AGENT,
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def _graphql(self, doc_id: str, variables: dict[str, Any], *, referer: str) -> dict[str, Any]:
        csrf = next((cookie.value for cookie in self._cookies if cookie.name == "csrftoken"), "")
        body = urlencode(
            {
                "variables": json.dumps(variables, separators=(",", ":")),
                "doc_id": doc_id,
                "server_timestamps": "true",
            }
        ).encode("ascii")
        headers = self._headers(referer=referer)
        headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": csrf,
            }
        )
        raw = self._read(Request(GRAPHQL_URL, data=body, headers=headers))
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InstagramClientError(None, "instagram_response_invalid") from error
        if not isinstance(payload, dict):
            raise InstagramClientError(None, "instagram_response_invalid")
        return payload

    def _metadata(self, source_url: str) -> tuple[str, str, dict[str, Any]]:
        shortcode, canonical_url = self._source(source_url)
        self._read(Request(canonical_url, headers=self._headers()))
        payload = self._graphql(
            MEDIA_DOC_ID,
            {
                "shortcode": shortcode,
                "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False,
            },
            referer=canonical_url,
        )
        data = payload.get("data")
        if payload.get("errors") or not isinstance(data, dict):
            raise InstagramClientError(None, "instagram_response_invalid")
        web_info = data.get("xdt_api__v1__media__shortcode__web_info") or {}
        items = web_info.get("items") or []
        if not items or not isinstance(items[0], dict):
            raise InstagramClientError(404, "instagram_not_found")
        item = items[0]
        if str(item.get("code") or "") != shortcode:
            raise InstagramClientError(None, "instagram_response_invalid")
        return shortcode, canonical_url, item

    def _clips_play_count(
        self, item: dict[str, Any], *, shortcode: str, referer: str
    ) -> int | None:
        direct = _direct_play_count(item)
        if direct is not None:
            return direct
        user = item.get("user") or {}
        user_id = user.get("pk") if isinstance(user, dict) else None
        if not user_id:
            return None
        payload = self._graphql(
            CLIPS_DOC_ID,
            {
                "data": {
                    "include_feed_video": True,
                    "page_size": 12,
                    "target_user_id": str(user_id),
                }
            },
            referer=referer,
        )
        connection = (payload.get("data") or {}).get(
            "xdt_api__v1__clips__user__connection_v2"
        ) or {}
        for edge in connection.get("edges") or []:
            media = (edge.get("node") or {}).get("media") or {}
            if str(media.get("code") or "") == shortcode:
                return _direct_play_count(media)
        return None

    @staticmethod
    def _video(
        item: dict[str, Any], *, shortcode: str, play_count: int
    ) -> InstagramPublicVideo:
        user = item.get("user") or {}
        user = user if isinstance(user, dict) else {}
        username = str(user.get("username") or "").strip()
        author = str(user.get("full_name") or username or "Instagram").strip()
        title = _caption(item) or str(item.get("title") or "").strip()
        title = title or (f"Instagram publication: {username}" if username else "Instagram publication")
        thumbnail = _thumbnail(item)
        if not thumbnail:
            raise InstagramClientError(None, "instagram_response_invalid")
        author_url = (
            f"https://www.instagram.com/{username}/"
            if username
            else f"https://www.instagram.com/reel/{shortcode}/"
        )
        try:
            return InstagramPublicVideo(
                id=shortcode,
                title=title[:500],
                author_name=author[:500],
                author_url=author_url,
                thumbnail_url=thumbnail,
                stats=InstagramPublicStats(playCount=play_count),
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise InstagramClientError(None, "instagram_response_invalid") from error

    def fetch_publication(self, source_url: str) -> InstagramPublicVideo:
        shortcode, _referer, item = self._metadata(source_url)
        play_count = _direct_play_count(item) or 0
        return self._video(item, shortcode=shortcode, play_count=play_count)

    def fetch_public_stats(self, source_url: str) -> InstagramPublicVideo:
        shortcode, referer, item = self._metadata(source_url)
        play_count = self._clips_play_count(item, shortcode=shortcode, referer=referer)
        if play_count is None:
            raise InstagramClientError(None, "instagram_response_invalid")
        return self._video(item, shortcode=shortcode, play_count=play_count)
