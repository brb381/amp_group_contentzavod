import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.content.models import PublicationParseStatus
from app.platforms import Platform


PLATFORM_DOMAINS = {
    Platform.YOUTUBE: ("youtube.com", "youtu.be"),
    Platform.VK: ("vk.com", "vkvideo.ru"),
    Platform.TIKTOK: ("tiktok.com",),
    Platform.INSTAGRAM: ("instagram.com",),
    Platform.DZEN: ("dzen.ru", "zen.yandex.ru"),
    Platform.RUTUBE: ("rutube.ru",),
}
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref", "source"}


class PublicationURLInvalid(ValueError):
    pass


@dataclass(frozen=True)
class ParsedPublicationURL:
    normalized_url: str
    external_id: str | None
    parse_status: PublicationParseStatus


def _host_allowed(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _external_id(platform: Platform, host: str, path: str, query: dict[str, str]) -> str | None:
    patterns = {
        Platform.TIKTOK: r"/(?:@[^/]+/)?video/(\d+)",
        Platform.INSTAGRAM: r"/reels?/([A-Za-z0-9_-]+)",
        Platform.VK: r"/clip(-?\d+_\d+)",
        Platform.DZEN: r"/video/watch/([A-Za-z0-9_-]+)",
        Platform.RUTUBE: r"/(?:video|shorts)/([A-Za-z0-9_-]+)",
    }
    if platform == Platform.YOUTUBE:
        if host == "youtu.be" or host.endswith(".youtu.be"):
            return path.strip("/").split("/")[0] or None
        match = re.match(r"/(?:shorts|embed)/([A-Za-z0-9_-]+)", path)
        return match.group(1) if match else query.get("v")
    pattern = patterns.get(platform)
    match = re.search(pattern, path) if pattern else None
    return match.group(1) if match else None


def _canonical_identity_url(platform: Platform, external_id: str) -> str:
    paths = {
        Platform.YOUTUBE: f"/shorts/{external_id}",
        Platform.VK: f"/clip{external_id}",
        Platform.TIKTOK: f"/video/{external_id}",
        Platform.INSTAGRAM: f"/reel/{external_id}",
        Platform.DZEN: f"/video/watch/{external_id}",
        Platform.RUTUBE: f"/video/{external_id}",
    }
    return f"https://{PLATFORM_DOMAINS[platform][0]}{paths[platform]}"


def parse_publication_url(platform: Platform, raw_url: str) -> ParsedPublicationURL:
    try:
        parsed = urlsplit(raw_url.strip())
        port = parsed.port
    except ValueError as error:
        raise PublicationURLInvalid("URL is malformed") from error
    if parsed.scheme.lower() not in {"http", "https"}:
        raise PublicationURLInvalid("Only HTTP and HTTPS URLs are supported")
    if parsed.username or parsed.password:
        raise PublicationURLInvalid("URL credentials are not allowed")
    if port not in {None, 80, 443}:
        raise PublicationURLInvalid("Non-standard URL ports are not allowed")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or not _host_allowed(host, PLATFORM_DOMAINS[platform]):
        raise PublicationURLInvalid("URL domain does not match the social account platform")

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    query = dict(query_items)
    external_id = _external_id(platform, host, path, query)
    if external_id:
        if len(external_id) > 255:
            raise PublicationURLInvalid("Publication identifier is too long")
        return ParsedPublicationURL(
            normalized_url=_canonical_identity_url(platform, external_id),
            external_id=external_id,
            parse_status=PublicationParseStatus.PARSED,
        )

    normalized_url = urlunsplit(("https", host, path, urlencode(sorted(query_items)), ""))
    short_link = platform == Platform.TIKTOK and host in {"vm.tiktok.com", "vt.tiktok.com"}
    return ParsedPublicationURL(
        normalized_url=normalized_url,
        external_id=None,
        parse_status=(
            PublicationParseStatus.PENDING if short_link else PublicationParseStatus.MANUAL_REVIEW
        ),
    )
