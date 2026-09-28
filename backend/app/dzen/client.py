import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.dzen.schemas import DzenPublicStats, DzenPublicVideo
from app.public_page import MAX_PUBLIC_PAGE_BYTES, nonnegative_int, parse_public_page, walk_json

ALLOWED_HOSTS = {"dzen.ru", "www.dzen.ru", "zen.yandex.ru"}


@dataclass(frozen=True)
class DzenClientError(Exception):
    status_code: int | None
    reason: str
    retry_after: str | None = None


def _views(candidate: dict) -> int | None:
    for key in ("views", "viewsCount", "viewCount", "videoViews"):
        value = nonnegative_int(candidate.get(key))
        if value is not None:
            return value
    statistics = candidate.get("interactionStatistic")
    if not isinstance(statistics, list):
        statistics = [statistics]
    for statistic in statistics:
        if not isinstance(statistic, dict):
            continue
        kind = str(statistic.get("interactionType", "")).casefold()
        if "watch" in kind or "view" in kind:
            value = nonnegative_int(statistic.get("userInteractionCount"))
            if value is not None:
                return value
    return None


def _matches(candidate: dict, external_id: str) -> bool:
    identifiers = {str(candidate.get(key, "")) for key in ("id", "publicationId", "videoId")}
    urls = (candidate.get("url"), candidate.get("@id"), candidate.get("mainEntityOfPage"))
    return candidate.get("@type") == "VideoObject" or external_id in identifiers or any(
        isinstance(value, str) and external_id in value for value in urls
    )


def _page_video(body: bytes, external_id: str) -> DzenPublicVideo:
    page = parse_public_page(body)
    candidate = next((
        item for document in page.documents for item in walk_json(document)
        if isinstance(item, dict) and _matches(item, external_id) and _views(item) is not None
    ), None)
    if candidate is None:
        raise ValueError("matching Dzen video is absent")
    author = candidate.get("author") or {}
    author = author if isinstance(author, dict) else {}
    author_name = author.get("name") or candidate.get("authorName") or "Дзен"
    author_url = author.get("url") or f"https://dzen.ru/video/watch/{external_id}"
    title = page.meta.get("og:title") or candidate.get("name") or candidate.get("headline")
    thumbnail = page.meta.get("og:image") or candidate.get("thumbnailUrl")
    if isinstance(thumbnail, list):
        thumbnail = next((item for item in thumbnail if isinstance(item, str)), None)
    if isinstance(thumbnail, dict):
        thumbnail = thumbnail.get("url")
    views = _views(candidate)
    if views is None or not title or not thumbnail:
        raise ValueError("Dzen public video data is incomplete")
    return DzenPublicVideo(
        id=external_id, title=str(title)[:500], author_name=str(author_name)[:500],
        author_url=str(author_url), thumbnail_url=str(thumbnail),
        stats=DzenPublicStats(playCount=views),
    )


class DzenClient:
    def __init__(self, *, timeout_seconds: int) -> None:
        self._timeout_seconds = timeout_seconds

    def _fetch(self, source_url: str) -> DzenPublicVideo:
        parsed = urlsplit(source_url)
        if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in ALLOWED_HOSTS:
            raise DzenClientError(None, "dzen_url_invalid")
        request = Request(
            source_url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140 Safari/537.36"
                ),
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read(MAX_PUBLIC_PAGE_BYTES + 1)
        except HTTPError as error:
            reason = "dzen_not_found" if error.code in {400, 404, 410} else "dzen_http_error"
            raise DzenClientError(error.code, reason, error.headers.get("Retry-After")) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise DzenClientError(None, "dzen_unreachable") from error
        if len(body) > MAX_PUBLIC_PAGE_BYTES:
            raise DzenClientError(None, "dzen_response_too_large")
        try:
            return _page_video(body, parsed.path.strip("/").split("/")[-1])
        except (UnicodeDecodeError, TypeError, ValueError, ValidationError) as error:
            raise DzenClientError(None, "dzen_response_invalid") from error

    def fetch_publication(self, source_url: str) -> DzenPublicVideo:
        return self._fetch(source_url)

    def fetch_public_stats(self, source_url: str) -> DzenPublicVideo:
        return self._fetch(source_url)
