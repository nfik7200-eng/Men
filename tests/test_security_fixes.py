"""Independent security review (completion programme): findings A3-A9, B1, B3, B5 — each reproduced before its fix."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from shopping.web import security
from shopping.web.app import create_app
from watchtower import config
from watchtower.collector import ensure_sources
from watchtower.models import Listing

TOKEN = "security-fixes-token-0123456789"


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    ensure_sources(db)
    return TestClient(create_app(db), raise_server_exceptions=False), db


@pytest.mark.parametrize("path", ["/go/99999999999999999999", "/listing/99999999999999999999", "/product/99999999999999999999",
                                  "/api/v1/listings/99999999999999999999", "/api/v1/listings/99999999999999999999/history"])
def test_a3_huge_integer_ids_are_a_404_not_a_500(client, path):
    c, _ = client
    assert c.get(path).status_code in (404, 422)


def test_a3_huge_ids_on_post_routes(client):
    c, _ = client
    assert c.post("/watch/99999999999999999999/remove", follow_redirects=False).status_code in (303, 404, 422)


def test_a4_non_ascii_admin_cookie_is_refused_not_a_crash(client):
    c, _ = client
    r = c.get("/admin", headers={"Cookie": "wt_admin=é".encode("utf-8")}, follow_redirects=False)
    assert r.status_code in (303, 403)


def test_b1_admin_cookie_is_signed_expires_and_is_not_static(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    v1 = security.admin_cookie_value(TOKEN, now=1_000_000)
    v2 = security.admin_cookie_value(TOKEN, now=1_000_100)
    assert v1 != v2 and security.cookie_valid(v1, TOKEN, now=1_000_000 + 60)
    assert not security.cookie_valid(v1, TOKEN, now=1_000_000 + 9 * 3600)  # server-side expiry (8 h), not advisory
    assert not security.cookie_valid(v1, TOKEN, now=1_000_000 - 3600)  # from the future
    ts, mac = v1.split(".")
    assert not security.cookie_valid(f"{int(ts) + 5}.{mac}", TOKEN, now=1_000_000 + 60)  # tampered timestamp
    assert not security.cookie_valid(v1, TOKEN + "x", now=1_000_000 + 60)  # token rotation invalidates every session
    assert not security.cookie_valid("garbage", TOKEN) and not security.cookie_valid("", TOKEN) and not security.cookie_valid("é.é", TOKEN)


def test_b1_login_still_works_end_to_end(client):
    c, _ = client
    assert c.post("/admin/login", data={"token": TOKEN}, follow_redirects=False).status_code == 303
    assert c.get("/admin").status_code == 200
    assert c.post("/admin/logout", follow_redirects=False).status_code in (200, 303)


def test_a5_watch_creation_is_rate_limited_per_client(client, db):
    c, _ = client
    with db.session() as s:
        from test_review_queue import _raw  # noqa
    # no listing needed: the request is refused by the limiter before it can create rows once the budget is spent
    codes = [c.post("/watch/1", data={"target": "10"}, follow_redirects=False).status_code for _ in range(40)]
    assert 429 in codes and codes.index(429) <= 31


def test_a5_repeated_outbound_clicks_are_rate_limited(client):
    c, _ = client
    codes = [c.get("/go/1", follow_redirects=False).status_code for _ in range(80)]
    assert 429 in codes


def test_a7_api_docs_and_openapi_are_not_public(client):
    c, _ = client
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(p).status_code == 404


def test_a7_watchlist_message_is_a_fixed_code_not_attacker_text(client):
    c, _ = client
    html = c.get("/watchlist?msg=Your account is locked, visit evil.example").text
    assert "evil.example" not in html


def test_a9_schedule_interval_must_be_finite_and_budget_an_integer(tmp_path):
    from watchtower import schedule
    def cfg(v):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"enabled": False, "sources": {"adafruit": v}}).replace("null", "NaN"))
        return p
    for bad in ({"min_interval_hours": "NaN-token", "max_pages": 5}, {"min_interval_hours": float("nan"), "max_pages": 5},
                {"min_interval_hours": float("inf"), "max_pages": 5}, {"min_interval_hours": 24, "max_pages": True}):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"enabled": False, "sources": {"adafruit": bad}}))
        with pytest.raises(schedule.ScheduleError):
            schedule.load(p)


@pytest.mark.parametrize("host", ["2130706433", "0x7f.1", "127.1", "0", "localhost.", "LOCALHOST", "0177.0.0.1", "[::1]", "169.254.169.254"])
def test_b3_numeric_and_odd_loopback_forms_are_refused_even_without_an_allowlist(host):
    import httpx
    from watchtower.fetcher import FetchDisallowed, Fetcher
    f = Fetcher(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))), min_delay=0, sleep=lambda s: None, allowed_hosts=None)
    with pytest.raises(FetchDisallowed):
        f._check(f"http://{host}/")


def test_b5_a_refused_manual_collection_tells_the_admin_why(client, monkeypatch):
    c, db = client
    monkeypatch.setattr(config, "CONTACT", "set-WATCHTOWER_CONTACT")  # policy gate will refuse live sources
    assert c.post("/admin/login", data={"token": TOKEN}, follow_redirects=False).status_code == 303
    c.post("/admin/collect/adafruit", follow_redirects=False)
    time.sleep(0.3)
    html = c.get("/admin").text
    assert "Last manual collection" in html and "adafruit" in html.lower() and ("contact" in html.lower() or "not approved" in html.lower())
