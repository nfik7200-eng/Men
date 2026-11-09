"""Admin settings JSON: read-only settings and the per-source enabled flag. Token required, Origin-checked, strict input.
The scheduler is reported but has no write route at all."""
import pytest
from fastapi.testclient import TestClient

from conftest import FakeFetcher
from shopping.web.app import create_app
from watchtower import config
from watchtower.collector import run_source
from watchtower.models import Source, utcnow

TOKEN = "unit-test-token-0123456789abcdef"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    return TOKEN


@pytest.fixture
def client(db, token):
    t = utcnow()
    run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=2, now_fn=lambda: t)
    return TestClient(create_app(db), base_url="http://testserver")


H = {"X-Admin-Token": TOKEN}


def test_settings_require_the_admin_token(client):
    assert client.get("/api/v1/admin/settings").status_code == 403
    assert client.get("/api/v1/admin/settings", headers={"X-Admin-Token": "wrong"}).status_code == 403


def test_settings_show_sources_budgets_and_scheduler_and_never_the_token(client):
    r = client.get("/api/v1/admin/settings", headers=H)
    assert r.status_code == 200
    body = r.json()
    keys = {x["key"] for x in body["sources"]}
    assert "webscraper_io_static" in keys
    assert all({"enabled", "configured", "page_budget", "kind"} <= set(x) for x in body["sources"])
    assert body["scheduler"]["enabled"] is False  # disabled by default, and this API does not change it
    assert TOKEN not in r.text


def test_enabled_flag_changes_only_with_a_real_boolean(client, db):
    url = "/api/v1/admin/sources/webscraper_io_static/enabled"
    for bad in ({"enabled": "false"}, {"enabled": 0}, {"enabled": None}, {}, ["enabled"]):
        assert client.post(url, json=bad, headers=H).status_code == 422, bad
    with db.session() as s:
        assert s.query(Source).filter_by(key="webscraper_io_static").one().enabled is True  # nothing changed


def test_enabled_flag_persists_and_is_visible_in_settings(client, db):
    url = "/api/v1/admin/sources/webscraper_io_static/enabled"
    assert client.post(url, json={"enabled": False}, headers=H).json() == {"key": "webscraper_io_static", "enabled": False}
    with db.session() as s:
        assert s.query(Source).filter_by(key="webscraper_io_static").one().enabled is False
    row = next(x for x in client.get("/api/v1/admin/settings", headers=H).json()["sources"] if x["key"] == "webscraper_io_static")
    assert row["enabled"] is False
    client.post(url, json={"enabled": True}, headers=H)
    assert client.get("/api/v1/admin/settings", headers=H).json()["sources"]


def test_unknown_and_not_yet_registered_sources_are_refused(client):
    assert client.post("/api/v1/admin/sources/no_such_source/enabled", json={"enabled": False}, headers=H).status_code == 404
    # a known adapter that has never been collected is registered on demand and can be switched
    assert client.post("/api/v1/admin/sources/books_toscrape/enabled", json={"enabled": False}, headers=H).status_code == 200


def test_a_cross_origin_post_is_refused_even_with_the_token(client, db):
    evil = {**H, "Origin": "https://evil.example"}
    r = client.post("/api/v1/admin/sources/webscraper_io_static/enabled", json={"enabled": False}, headers=evil)
    assert r.status_code == 403
    with db.session() as s:
        assert s.query(Source).filter_by(key="webscraper_io_static").one().enabled is True


def test_there_is_no_scheduler_write_route(client):
    for method in ("post", "put", "patch"):
        r = getattr(client, method)("/api/v1/admin/scheduler", json={"enabled": True}, headers=H)
        assert r.status_code in (404, 405), method


def test_the_session_cookie_also_authorises_the_settings_api(client):
    assert client.post("/admin/login", data={"token": TOKEN}, follow_redirects=False).status_code == 303
    assert client.get("/api/v1/admin/settings").status_code == 200  # cookie path /api/v1/admin, set at sign-in
