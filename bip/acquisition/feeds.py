"""RSS 2.0 and Atom feed parsing; missing fields stay None (never invented).

``parse_feed`` accepts RSS or Atom. ``fetch_nitter_user`` (Nitter-style RSS for X/Twitter) is
kept for reference but is not wired into the application: Nitter scrapes a site whose terms may
not allow it (CLAUDE.md §2.4).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from bip.acquisition.fetcher import Fetcher, FetchResult

MAX_FEED_BYTES = 2_000_000


@dataclass(frozen=True, slots=True)
class FeedItem:
    guid: str | None
    title: str | None
    link: str | None
    published: str | None
    author: str | None
    source_url: str
    request_id: str


class FeedParseError(ValueError):
    pass


def parse_rss(body: bytes, source_url: str, request_id: str) -> list[FeedItem]:
    if len(body) > MAX_FEED_BYTES:
        raise FeedParseError("feed too large")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise FeedParseError("DTD/entities not allowed")
    try:
        root = ET.fromstring(body)  # noqa: S314
    except ET.ParseError as e:
        raise FeedParseError(str(e)) from e
    if root.tag != "rss":
        raise FeedParseError("not an RSS document")

    def txt(el: ET.Element, tag: str) -> str | None:
        child = el.find(tag)
        return child.text.strip() if child is not None and child.text else None

    dc = "{http://purl.org/dc/elements/1.1/}creator"
    return [
        FeedItem(
            txt(i, "guid"),
            txt(i, "title"),
            txt(i, "link"),
            txt(i, "pubDate"),
            txt(i, dc),
            source_url,
            request_id,
        )
        for i in root.iter("item")
    ]


def fetch_nitter_user(
    fetcher: Fetcher, instance: str, handle: str
) -> tuple[FetchResult, list[FeedItem]]:
    """``instance`` is a configured Nitter base URL; returns no items when blocked/failed."""
    url = f"{instance.rstrip('/')}/{handle}/rss"
    res = fetcher.fetch(url, provider_id="nitter_rss")
    if res.outcome != "ok" or res.body is None:
        return res, []
    return res, parse_rss(res.body, url, res.provenance.request_id)


_ATOM = "{http://www.w3.org/2005/Atom}"


def _check_document(body: bytes) -> ET.Element:
    if len(body) > MAX_FEED_BYTES:
        raise FeedParseError("feed too large")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise FeedParseError("DTD/entities not allowed")
    try:
        return ET.fromstring(body)  # noqa: S314 - DTDs and entities are refused above
    except ET.ParseError as e:
        raise FeedParseError(str(e)) from e


def _atom_text(el: ET.Element, tag: str) -> str | None:
    child = el.find(f"{_ATOM}{tag}")
    return child.text.strip() if child is not None and child.text else None


def _atom_link(entry: ET.Element) -> str | None:
    links = entry.findall(f"{_ATOM}link")
    for link in links:
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href")
    return links[0].get("href") if links else None


def parse_feed(body: bytes, source_url: str, request_id: str) -> list[FeedItem]:
    """RSS 2.0 or Atom; anything else is a :class:`FeedParseError`."""
    root = _check_document(body)
    if root.tag == "rss":
        return parse_rss(body, source_url, request_id)
    if root.tag != f"{_ATOM}feed":
        raise FeedParseError("not an RSS or Atom document")
    out: list[FeedItem] = []
    for entry in root.iter(f"{_ATOM}entry"):
        author = entry.find(f"{_ATOM}author")
        out.append(
            FeedItem(
                _atom_text(entry, "id"),
                _atom_text(entry, "title"),
                _atom_link(entry),
                _atom_text(entry, "published") or _atom_text(entry, "updated"),
                _atom_text(author, "name") if author is not None else None,
                source_url,
                request_id,
            )
        )
    return out
