"""Web feeds (roadmap Stage 14, part): owner-configured RSS/Atom feeds, fetched politely.

Every network call goes through a fake transport here (never live). Feed items are stored as
verified observations of what a feed published, never as verified facts about their content.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from bip.acquisition.feeds import FeedParseError, parse_feed
from bip.acquisition.fetcher import RawResponse
from bip.api.access import required_role
from bip.api.app import create_app
from bip.config import load_settings
from bip.core.exceptions import ConfigurationError
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.rpc.transport import HttpsTransport, TransportError
from bip.web.service import FeedService
from tests.api.conftest import roomy_guard

API = "/api/v1"
FEED = "https://blog.example.org/feed.xml"
ATOM = "https://github.com/ethereum/go-ethereum/releases.atom"
ROBOTS_OK = RawResponse(200, {}, b"User-agent: *\nAllow: /\n")

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title>
<item><guid>a1</guid><title>Upgrade scheduled</title><link>https://blog.example.org/a1</link>
<pubDate>Mon, 23 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>No guid here</title><link>https://blog.example.org/a2</link></item>
</channel></rss>"""

ATOM_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Releases</title>
<entry><id>tag:github.com,2008:Repository/1/v1.15.0</id><title>v1.15.0</title>
<link rel="alternate" href="https://github.com/ethereum/go-ethereum/releases/tag/v1.15.0"/>
<updated>2026-11-20T09:00:00Z</updated><author><name>maintainer</name></author></entry>
</feed>"""


class FakeTransport:
    def __init__(self, routes: dict[str, RawResponse]) -> None:
        self.routes = dict(routes)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str], timeout: float) -> RawResponse:
        self.calls.append((url, headers))
        return self.routes.get(url, RawResponse(404, {}, b""))


# ---------- parsing ----------


def test_rss_and_atom_parse_with_missing_fields_left_empty() -> None:
    rss = parse_feed(RSS, FEED, "r1")
    assert [i.guid for i in rss] == ["a1", None]
    assert rss[1].published is None
    atom = parse_feed(ATOM_BODY, ATOM, "r2")
    assert atom[0].title == "v1.15.0"
    assert atom[0].link == "https://github.com/ethereum/go-ethereum/releases/tag/v1.15.0"
    assert atom[0].published == "2026-11-20T09:00:00Z"
    assert atom[0].author == "maintainer"


@pytest.mark.parametrize(
    "body",
    [b"<!DOCTYPE x [<!ENTITY a 'b'>]><rss/>", b"not xml", b"<html></html>"],
)
def test_unsafe_or_unknown_documents_are_refused(body: bytes) -> None:
    with pytest.raises(FeedParseError):
        parse_feed(body, FEED, "r")


# ---------- settings ----------


def test_feeds_must_be_public_https_urls(tmp_path: Path) -> None:
    for bad in (
        "http://blog.example.org/feed",
        "https://127.0.0.1/feed",
        "ftp://x.org/f",
        "wss://blog.example.org/feed",  # validate_url allows wss; feeds must still be https
    ):
        with pytest.raises(ConfigurationError):
            load_settings(data_root=tmp_path, env="test", web={"feeds": [bad]})
    with pytest.raises(ConfigurationError):
        load_settings(data_root=tmp_path, env="test", web={"contact": "x\ny"})
    ok = load_settings(
        data_root=tmp_path, env="test", web={"feeds": [FEED], "contact": "ops@example.org"}
    )
    assert ok.web.feeds == (FEED,)


def test_the_transport_refuses_private_destinations_and_plain_http() -> None:
    t = HttpsTransport(resolver=lambda _h, _p: ["10.0.0.5"])
    with pytest.raises(TransportError, match="unsafe_destination"):
        t.get("https://intranet.example.org/feed", {}, timeout_s=1, max_bytes=1000)
    with pytest.raises(TransportError):
        t.get("http://blog.example.org/feed", {}, timeout_s=1, max_bytes=1000)
    with pytest.raises(TransportError, match="only https"):
        t.get("wss://blog.example.org/feed", {}, timeout_s=1, max_bytes=1000)


# ---------- service ----------


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


def service(
    engine: Engine, transport: FakeTransport, feeds: list[str], **web: object
) -> FeedService:
    settings = load_settings(data_root=Path("/unused"), env="test", web={"feeds": feeds, **web})
    return FeedService(Repository(engine), settings.web, transport)


def test_refresh_stores_new_items_once_with_provenance(engine: Engine) -> None:
    t = FakeTransport(
        {
            "https://blog.example.org/robots.txt": ROBOTS_OK,
            FEED: RawResponse(200, {"etag": '"v1"'}, RSS),
        }
    )
    svc = service(engine, t, [FEED])
    first = svc.refresh()
    assert first["feeds"][0]["outcome"] == "ok"
    assert first["feeds"][0]["new_items"] == 2
    again = svc.refresh()
    assert again["feeds"][0]["new_items"] == 0  # stored once (guid, else link)
    items = svc.items(limit=10)["items"]
    assert {i["title"] for i in items} == {"Upgrade scheduled", "No guid here"}
    item = next(i for i in items if i["guid"] == "a1")
    assert item["feed_url"] == FEED
    assert item["request_id"]
    assert len(item["content_sha256"]) == 64
    assert item["evidence_class"] == "verified_observation"
    state = svc.feeds()["items"][0]
    assert (state["url"], state["last_outcome"], state["items_stored"]) == (FEED, "ok", 2)


def test_conditional_get_uses_the_stored_etag(engine: Engine) -> None:
    t = FakeTransport(
        {
            "https://blog.example.org/robots.txt": ROBOTS_OK,
            FEED: RawResponse(200, {"etag": '"v1"'}, RSS),
        }
    )
    svc = service(engine, t, [FEED])
    svc.refresh()
    t.routes[FEED] = RawResponse(304, {}, b"")
    fresh = FeedService(svc.repo, svc.cfg, t)  # a new process: the validator comes from the DB
    out = fresh.refresh()
    assert out["feeds"][0]["outcome"] == "not_modified"
    feed_calls = [h for u, h in t.calls if u == FEED]
    assert feed_calls[-1].get("If-None-Match") == '"v1"'


def test_robots_disallow_is_respected(engine: Engine) -> None:
    t = FakeTransport(
        {
            "https://blog.example.org/robots.txt": RawResponse(
                200, {}, b"User-agent: *\nDisallow: /\n"
            ),
            FEED: RawResponse(200, {}, RSS),
        }
    )
    out = service(engine, t, [FEED]).refresh()
    assert out["feeds"][0]["outcome"] == "robots_blocked"
    assert [u for u, _ in t.calls] == ["https://blog.example.org/robots.txt"]


def test_failures_are_recorded_not_stored_as_data(engine: Engine) -> None:
    t = FakeTransport(
        {
            "https://blog.example.org/robots.txt": ROBOTS_OK,
            FEED: RawResponse(200, {}, b"<html>nope</html>"),
        }
    )
    svc = service(engine, t, [FEED, ATOM])
    out = svc.refresh()
    by = {f["url"]: f for f in out["feeds"]}
    assert by[FEED]["outcome"] == "invalid_feed"
    assert by[ATOM]["outcome"] in {"error", "robots_blocked"}
    assert svc.items(limit=10)["items"] == []


def test_the_user_agent_names_the_owner_contact(engine: Engine) -> None:
    t = FakeTransport(
        {"https://blog.example.org/robots.txt": ROBOTS_OK, FEED: RawResponse(200, {}, RSS)}
    )
    service(engine, t, [FEED], contact="ops@example.org").refresh()
    assert all("ops@example.org" in h["User-Agent"] for _u, h in t.calls)


def test_items_per_feed_are_capped(engine: Engine) -> None:
    many = (
        b"<rss><channel>"
        + b"".join(f"<item><guid>g{i}</guid><title>t{i}</title></item>".encode() for i in range(30))
        + b"</channel></rss>"
    )
    t = FakeTransport(
        {"https://blog.example.org/robots.txt": ROBOTS_OK, FEED: RawResponse(200, {}, many)}
    )
    out = service(engine, t, [FEED], max_items_per_feed=10).refresh()
    assert out["feeds"][0]["new_items"] == 10


# ---------- API ----------


def test_api_lists_feeds_and_items_and_refresh_is_admin(tmp_path: Path) -> None:
    settings = load_settings(data_root=tmp_path / "data", env="test", web={"feeds": [FEED]})
    engine = create_db_engine(settings)
    t = FakeTransport(
        {"https://blog.example.org/robots.txt": ROBOTS_OK, FEED: RawResponse(200, {}, RSS)}
    )
    app = create_app(
        settings,
        engine=engine,
        disk_guard=roomy_guard(settings.resolved_data_root),
        web_transport=t,
    )
    try:
        with TestClient(app) as c:
            assert c.get(f"{API}/web/feeds").json()["items"][0]["last_outcome"] is None
            r = c.post(f"{API}/web/feeds/refresh", headers={"X-BIP-Client": "web"})
            assert r.status_code == 200, r.text
            assert r.json()["feeds"][0]["new_items"] == 2
            items = c.get(f"{API}/web/items", params={"limit": 5}).json()
            assert items["total"] == 2
            assert c.get(f"{API}/web/items", params={"feed": FEED}).json()["total"] == 2
    finally:
        engine.dispose()
    assert required_role("POST", "/web/feeds/refresh") == "admin"
    assert required_role("GET", "/web/items") == "viewer"
