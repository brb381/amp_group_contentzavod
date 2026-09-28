import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.instagram.schemas import InstagramPublicStats, InstagramPublicVideo
from app.public_page import MAX_PUBLIC_PAGE_BYTES, nonnegative_int, parse_public_page, walk_json

ALLOWED_HOSTS = {"instagram.com", "www.instagram.com"}


@dataclass(frozen=True)
class InstagramClientError(Exception):
    status_code: int | None
    reason: str
    retry_after: str | None = None


def _page_video(body: bytes, external_id: str) -> InstagramPublicVideo:
    page = parse_public_page(body)
    matches = (
        item for document in page.documents for item in walk_json(document)
        if isinstance(item, dict)
        and external_id in {str(item.get(key, "")) for key in ("shortcode", "code", "id")}
    )
    candidate = next(matches, None)
    if candidate is None:
        raise ValueError("matching Instagram video is absent")
    views = next((
        value for key in ("video_view_count", "video_play_count", "play_count", "view_count")
        if (value := nonnegative_int(candidate.get(key))) is not None
    ), None)
    owner = candidate.get("owner") or candidate.get("user") or {}
    owner = owner if isinstance(owner, dict) else {}
    username = owner.get("username")
    author = owner.get("full_name") or username or "Instagram"
    caption = candidate.get("caption") or {}
    title = page.meta.get("og:title") or (
        caption.get("text") if isinstance(caption, dict) else None
    )
    thumbnail = page.meta.get("og:image") or candidate.get("display_url")
    if views is None or not title or not thumbnail:
        raise ValueError("Instagram public video data is incomplete")
    author_url = (
        f"https://www.instagram.com/{username}/"
        if username else f"https://www.instagram.com/reel/{external_id}/"
    )
    return InstagramPublicVideo(
        id=external_id, title=str(title)[:500], author_name=str(author)[:500],
        author_url=author_url, thumbnail_url=str(thumbnail),
        stats=InstagramPublicStats(playCount=views),
    )


class InstagramClient:
    def __init__(self, *, timeout_seconds: int) -> None:
        self._timeout_seconds = timeout_seconds

    def _fetch(self, source_url: str) -> InstagramPublicVideo:
        parsed = urlsplit(source_url)
        if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in ALLOWED_HOSTS:
            raise InstagramClientError(None, "instagram_url_invalid")
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
            reason = (
                "instagram_not_found"
                if error.code in {400, 404, 410}
                else "instagram_http_error"
            )
            raise InstagramClientError(
                error.code, reason, error.headers.get("Retry-After")
            ) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise InstagramClientError(None, "instagram_unreachable") from error
        if len(body) > MAX_PUBLIC_PAGE_BYTES:
            raise InstagramClientError(None, "instagram_response_too_large")
        try:
            return _page_video(body, parsed.path.strip("/").split("/")[-1])
        except (UnicodeDecodeError, TypeError, ValueError, ValidationError) as error:
            raise InstagramClientError(None, "instagram_response_invalid") from error

    def fetch_publication(self, source_url: str) -> InstagramPublicVideo:
        return self._fetch(source_url)

    def fetch_public_stats(self, source_url: str) -> InstagramPublicVideo:
        return self._fetch(source_url)
