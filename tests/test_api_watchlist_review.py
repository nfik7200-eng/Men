"""JSON API: per-browser watchlist, admin-only match review and admin status. Same security as the HTML routes."""
import pytest
from fastapi.testclient import TestClient

from conftest import FakeFetcher
from shopping.web.app import create_app
from watchtower import config
from watchtower.collector import run_source
from watchtower.models import Listing, MatchCandidate, Product, Source, utcnow

TOKEN = "unit-test-token-0123456789abcdef"


@pytest.fixture
def seeded(db):
    t = utcnow()
    run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=2, now_fn=lambda: t)
    return db


@pytest.fixture
def app(seeded):
    return create_app(seeded)


@pytest.fixture
def client(app):
    """A browser: it has visited a page, so it holds the owner cookie that the watchlist API needs."""
    c = TestClient(app, base_url="http://testserver")
    assert c.get("/").status_code == 200
    return c


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    return TOKEN


def any_listing_id(db) -> int:
    with db.session() as s:
        return s.query(Listing.id).order_by(Listing.id).first()[0]


def test_watchlist_refuses_a_caller_without_a_browser_session(app):
    """An API client that never visited a page has no owner cookie. A write would vanish, so it is refused clearly."""
    bare = TestClient(app, base_url="http://testserver")
    assert bare.get("/api/v1/watchlist").status_code == 401
    assert bare.post("/api/v1/watchlist", json={"listing_id": 1}).status_code == 401
    assert bare.delete("/api/v1/watchlist/1").status_code == 401


def test_watchlist_starts_empty_and_reports_the_plan_limit(client):
    body = client.get("/api/v1/watchlist").json()
    assert body["items"] == [] and isinstance(body["limit"], int) and body["limit"] > 0


def test_add_list_and_remove_a_watch_item(client, seeded):
    lid = any_listing_id(seeded)
    r = client.post("/api/v1/watchlist", json={"listing_id": lid, "target_price": None})
    assert r.status_code == 201
    items = client.get("/api/v1/watchlist").json()["items"]
    assert [i["listing_id"] for i in items] == [lid]
    assert client.delete(f"/api/v1/watchlist/{lid}").status_code == 200
    assert client.get("/api/v1/watchlist").json()["items"] == []
    assert client.delete(f"/api/v1/watchlist/{lid}").status_code == 404


def test_target_price_is_stored_in_the_listings_own_currency(client, seeded):
    lid = any_listing_id(seeded)
    with seeded.session() as s:
        s.get(Listing, lid).currency = "JPY"   # zero-decimal: a target of 1500 is 1500 yen, not 150000
        s.commit()
    r = client.post("/api/v1/watchlist", json={"listing_id": lid, "target_price": 1500})
    assert r.status_code == 201 and r.json()["target_price_minor"] == 1500


@pytest.mark.parametrize("bad", ["12", -5, 0, 1e12, True])
def test_bad_target_is_refused(client, seeded, bad):
    lid = any_listing_id(seeded)
    r = client.post("/api/v1/watchlist", json={"listing_id": lid, "target_price": bad})
    assert r.status_code == 422


def test_unknown_listing_and_non_json_body_are_refused(client):
    assert client.post("/api/v1/watchlist", json={"listing_id": 999999}).status_code == 404
    assert client.post("/api/v1/watchlist", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post("/api/v1/watchlist", json={"listing_id": "7"}).status_code == 422


def test_cross_origin_watch_write_is_refused(client, seeded):
    lid = any_listing_id(seeded)
    r = client.post("/api/v1/watchlist", json={"listing_id": lid}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_match_review_requires_the_admin_token(client, token):
    assert client.get("/api/v1/match-candidates", follow_redirects=False).status_code == 403
    assert client.get("/api/v1/match-candidates", headers={"X-Admin-Token": "nope"}).status_code in (401, 403)
    assert client.get("/api/v1/match-candidates", headers={"X-Admin-Token": TOKEN}).status_code == 200


def test_match_decision_validates_input_and_missing_candidates(client, token):
    h = {"X-Admin-Token": TOKEN}
    assert client.post("/api/v1/match-candidates/1/decision", json={"decision": "maybe"}, headers=h).status_code == 422
    assert client.post("/api/v1/match-candidates/999999/decision", json={"decision": "reject"}, headers=h).status_code == 404


def test_admin_status_requires_the_token_and_lists_sources(client, token):
    r = client.get("/api/v1/admin/status", follow_redirects=False)
    assert r.status_code == 403 and r.headers.get("content-type", "").startswith("application/json")  # JSON refusal, no redirect
    body = client.get("/api/v1/admin/status", headers={"X-Admin-Token": TOKEN}).json()
    assert body["sources"] and "pending_review" in body and isinstance(body["runs"], list)


def test_watch_creation_over_the_api_is_rate_limited_like_the_html_route(client, seeded):
    """POST /api/v1/watchlist shares the strict per-client watch bucket (30 per hour), not the general 120 per minute."""
    lid = any_listing_id(seeded)
    codes = [client.post("/api/v1/watchlist", json={"listing_id": lid}).status_code for _ in range(31)]
    assert codes[:30] == [201] * 30
    assert codes[30] == 429


def test_a_decided_candidate_cannot_be_decided_again(client, token, seeded):
    from watchtower.models import Listing, MatchCandidate, Product
    with seeded.session() as s:
        lst = s.query(Listing).order_by(Listing.id).first()
        p = Product(title="Other product")
        s.add(p)
        s.flush()
        c = MatchCandidate(listing_id=lst.id, product_id=p.id, score=0.5, reasons=[], status="pending")
        s.add(c)
        s.commit()
        cid = c.id
    h = {"X-Admin-Token": TOKEN}
    first = client.post(f"/api/v1/match-candidates/{cid}/decision", json={"decision": "reject"}, headers=h)
    assert first.status_code == 200
    second = client.post(f"/api/v1/match-candidates/{cid}/decision", json={"decision": "accept"}, headers=h)
    assert second.status_code == 409
