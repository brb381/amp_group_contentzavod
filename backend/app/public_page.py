import json
from dataclasses import dataclass
from html.parser import HTMLParser


MAX_PUBLIC_PAGE_BYTES = 5_000_000


@dataclass(frozen=True)
class PublicPage:
    meta: dict[str, str]
    documents: list[object]


class _PublicPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.scripts: list[str] = []
        self._json_script = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").casefold()
            content = values.get("content")
            if key and content:
                self.meta[key] = content
        elif tag == "script" and (values.get("type") or "").casefold() in {
            "application/json",
            "application/ld+json",
        }:
            self._json_script = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._json_script:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json_script:
            self.scripts.append("".join(self._parts))
            self._json_script = False
            self._parts = []


def parse_public_page(body: bytes) -> PublicPage:
    parser = _PublicPageParser()
    parser.feed(body.decode("utf-8"))
    documents = []
    for script in parser.scripts:
        try:
            documents.append(json.loads(script))
        except json.JSONDecodeError:
            continue
    return PublicPage(meta=parser.meta, documents=documents)


def walk_json(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def nonnegative_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
