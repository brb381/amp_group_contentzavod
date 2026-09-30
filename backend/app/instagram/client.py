import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from pydantic import ValidationError

from app.instagram.schemas import InstagramPublicStats, InstagramPublicVideo
from app.public_page import MAX_PUBLIC_PAGE_BYTES, nonnegative_int, walk_json

ALLOWED_HOSTS = {"instagram.com", "www.instagram.com"}
SERVER_JS_MARKER = "s.handle("
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
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
        if value:
            return str(value)
    elif caption:
        return str(caption)

    edges = (item.get("edge_media_to_caption") or {}).get("edges") or []
    if edges and isinstance(edges[0], dict):
        node = edges[0].get("node") or {}
        value = node.get("text") if isinstance(node, dict) else None
        if value:
            return str(value)
    return None


def _thumbnail(item: dict[str, Any]) -> str | None:
    candidates = (item.get("image_versions2") or {}).get("candidates") or []
    if candidates and isinstance(candidates[0], dict):
        url = candidates[0].get("url")
        if url:
            return str(url)
    value = item.get("display_uri") or item.get("display_url") or item.get("thumbnail_src")
    return str(value) if value else None


def _direct_play_count(item: dict[str, Any]) -> int | None:
    play_count = next(
        (
            value
            for key in ("play_count", "view_count", "video_play_count", "video_view_count")
            if (value := nonnegative_int(item.get(key))) is not None
        ),
        None,
    )
    if play_count is None:
        return None

    engagement_counts = []
    for key in ("edge_liked_by", "edge_media_preview_like", "edge_media_to_comment"):
        edge = item.get(key)
        if isinstance(edge, dict) and (count := nonnegative_int(edge.get("count"))) is not None:
            engagement_counts.append(count)
    if engagement_counts and play_count < max(engagement_counts):
        return None
    return play_count


def _serverjs_documents(body: bytes) -> list[object]:
    try:
        page = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InstagramClientError(None, "instagram_response_invalid") from error

    decoder = json.JSONDecoder()
    documents: list[object] = []
    position = 0
    while (marker := page.find(SERVER_JS_MARKER, position)) >= 0:
        start = marker + len(SERVER_JS_MARKER)
        try:
            document, end = decoder.raw_decode(page, start)
        except json.JSONDecodeError:
            position = start
            continue
        documents.append(document)
        position = end
    return documents


def _embed_media(body: bytes, *, shortcode: str) -> dict[str, Any]:
    for document in _serverjs_documents(body):
        for value in walk_json(document):
            if not isinstance(value, dict):
                continue
            context_json = value.get("contextJSON")
            if not isinstance(context_json, str):
                continue
            try:
                context = json.loads(context_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(context, dict):
                continue
            gql_data = context.get("gql_data")
            if not isinstance(gql_data, dict) or "shortcode_media" not in gql_data:
                continue
            media = gql_data["shortcode_media"]
            if media is None:
                raise InstagramClientError(404, "instagram_not_found")
            if not isinstance(media, dict):
                continue
            if str(media.get("shortcode") or media.get("code") or "") != shortcode:
                raise InstagramClientError(None, "instagram_response_invalid")
            return media
    raise InstagramClientError(None, "instagram_response_invalid")


class InstagramClient:
    def __init__(self, *, timeout_seconds: int, opener=None) -> None:
        self._timeout_seconds = timeout_seconds
        self._opener = opener or build_opener()

    @staticmethod
    def _source(source_url: str) -> tuple[str, str]:
        parsed = urlsplit(source_url)
        if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in ALLOWED_HOSTS:
            raise InstagramClientError(None, "instagram_url_invalid")
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2 or parts[0].casefold() not in {"p", "reel", "reels", "tv"}:
            raise InstagramClientError(None, "instagram_url_invalid")
        shortcode = parts[1]
        embed_url = f"https://www.instagram.com/{parts[0]}/{shortcode}/embed/captioned/"
        return shortcode, embed_url

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

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Dpr": "1",
            "Sec-Ch-Prefers-Color-Scheme": "light",
            "Sec-Ch-Ua": '"Chromium";v="140", "Not=A?Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "iframe",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": USER_AGENT,
            "Viewport-Width": "1280",
        }

    def _metadata(self, source_url: str) -> tuple[str, dict[str, Any]]:
        shortcode, embed_url = self._source(source_url)
        body = self._read(Request(embed_url, headers=self._headers()))
        return shortcode, _embed_media(body, shortcode=shortcode)

    @staticmethod
    def _video(
        item: dict[str, Any], *, shortcode: str, play_count: int
    ) -> InstagramPublicVideo:
        user = item.get("user") or item.get("owner") or {}
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
        shortcode, item = self._metadata(source_url)
        play_count = _direct_play_count(item)
        return self._video(item, shortcode=shortcode, play_count=play_count or 0)

    def fetch_public_stats(self, source_url: str) -> InstagramPublicVideo:
        shortcode, item = self._metadata(source_url)
        play_count = _direct_play_count(item)
        if play_count is None:
            raise InstagramClientError(None, "instagram_response_invalid")
        return self._video(item, shortcode=shortcode, play_count=play_count)
