"""Twitter/X via Nitter-style RSS: parse only; missing fields stay None (never invented)."""

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
