"""Regression tests for the independent audit: web security and input handling (F6-F8, F18-F20)."""
import pytest
from fastapi.testclient import TestClient

from conftest import FakeFetcher
from shopping.web import app as app_module
from shopping.web.app import create_app
from watchtower import config
from watchtower.collector import run_source
from watchtower.models import Listing, Source, utcnow

TOKEN = "unit-test-token-0123456789abcdef"


@pytest.fixture
def seeded(db):
    t = utcnow()
    run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=2, now_fn=lambda: t)
    return db


@pytest.fixture
def client(seeded):
    return TestClient(create_app(seeded), base_url="http://testserver")


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    return TOKEN


# ---------------- F6/F8: admin fails closed, token never in URL ----------------
def test_admin_disabled_without_token_even_from_loopback(seeded, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", None)
    c = TestClient(create_app(seeded), client=("127.0.0.1", 5000))
    assert c.get("/admin", headers={"accept": "application/json"}).status_code == 403
    assert c.post("/admin/collect/books_toscrape").status_code == 403
    assert c.get("/admin", headers={"X-Forwarded-For": "8.8.8.8"}).status_code in (303, 403)  # proxy spoofing gains nothing
    assert "Admin is disabled" in c.get("/admin/login").text


def test_admin_login_flow_cookie_flags_and_no_query_token(client, token):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/login"
    assert client.get(f"/admin?token={TOKEN}", follow_redirects=False).status_code == 303  # URL tokens do not authenticate
    bad = client.post("/admin/login", data={"token": "wrong"}, follow_redirects=False)
    assert bad.headers["location"].endswith("err=1") and "wt_admin" not in bad.headers.get("set-cookie", "")
    ok = client.post("/admin/login", data={"token": TOKEN}, follow_redirects=False)
    cookie = ok.headers["set-cookie"]
    assert "wt_admin=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/admin" in cookie
    assert TOKEN not in cookie  # cookie is a derived value, never the secret
    assert client.get("/admin").status_code == 200
    client.post("/admin/logout")
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_admin_header_token_for_api_clients(client, token):
    assert client.post("/admin/source/books_toscrape/disable", headers={"X-Admin-Token": "nope"}).status_code == 403
    r = client.post("/admin/source/books_toscrape/disable", headers={"X-Admin-Token": TOKEN}, follow_redirects=False)
    assert r.status_code == 303


def test_admin_collect_respects_disabled_and_runs_in_background(client, token, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "run_source", lambda db, key, **k: calls.append(key))
    client.post("/admin/login", data={"token": TOKEN})
    client.post("/admin/source/books_toscrape/disable")
    r = client.post("/admin/collect/books_toscrape", follow_redirects=False)
    assert "disabled" in r.headers["location"].lower() and calls == []
    client.post("/admin/source/books_toscrape/enable")
    client.post("/admin/collect/books_toscrape")
    assert calls == ["books_toscrape"]


# ---------------- F7: CSRF ----------------
def test_cross_origin_posts_are_refused(client, token):
    lid = client.get("/api/v1/listings").json()["items"][0]["id"]
    evil = {"Origin": "https://evil.example"}
    assert client.post(f"/watch/{lid}", data={"target": ""}, headers=evil, follow_redirects=False).status_code == 403
    assert client.post("/watch/%d" % lid, data={}, headers={"Referer": "https://evil.example/page"}).status_code == 403
    assert client.post(f"/watch/{lid}", data={}, headers={"Origin": "null"}).status_code == 403
    client.post("/admin/login", data={"token": TOKEN})
    assert client.post("/admin/source/books_toscrape/disable", headers=evil).status_code == 403  # even with a valid cookie
    assert client.post("/admin/login", data={"token": TOKEN}, headers=evil).status_code == 403  # login CSRF
    assert client.post(f"/watch/{lid}", data={}, headers={"Origin": "http://testserver"}, follow_redirects=False).status_code == 303


# ---------------- public watchlist isolation ----------------
def test_watchlists_are_per_browser(seeded):
    a, b = TestClient(create_app(seeded)), TestClient(create_app(seeded))
    lid = a.get("/api/v1/listings").json()["items"][0]["id"]
    a.post(f"/watch/{lid}", data={"target": "10"})
    assert "Remove" in a.get("/watchlist").text and "Nothing watched yet" in b.get("/watchlist").text
    assert a.cookies.get("wt_owner") != b.cookies.get("wt_owner")
    r = TestClient(create_app(seeded)).get("/watchlist", headers={"Cookie": "wt_owner=../../etc/passwd"})
    assert "wt_owner=../" not in r.headers["set-cookie"] and len(r.headers["set-cookie"].split("wt_owner=")[1].split(";")[0]) == 32


def test_watch_rejects_nonfinite_and_out_of_range_targets(client):
    lid = client.get("/api/v1/listings").json()["items"][0]["id"]
    for bad in ("inf", "nan", "-1", "0", "1e400", "abc"):
        r = client.post(f"/watch/{lid}", data={"target": bad}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("msg=invalid"), bad
        assert "Invalid target price" in client.get(r.headers["location"]).text, bad  # the message is shown, from a fixed code


# ---------------- rate limiting, headers, CORS ----------------
def test_rate_limit_and_login_bruteforce_limit(seeded, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 5)
    c = TestClient(create_app(seeded))
    codes = [c.get("/health").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
    assert c.get("/health").headers["retry-after"] == "60"
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 120)
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    d = TestClient(create_app(seeded))
    r = [d.post("/admin/login", data={"token": "x%d" % i}, follow_redirects=False).status_code for i in range(12)]
    assert r[:10] == [303] * 10 and 429 in r[10:]


def test_security_headers_and_no_cors(client):
    r = client.get("/", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    assert "default-src 'self'" in r.headers["content-security-policy"] and "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    pre = client.options("/api/v1/listings", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in pre.headers


# ---------------- F19/F20: input handling ----------------
@pytest.mark.parametrize("path", ["/search?min_price=inf", "/search?max_price=nan", "/research?q=laptop&ship=inf",
                                  "/api/v1/listings?max_price=1e400", "/api/v1/listings?min_price=-5", "/search?page=0",
                                  "/api/v1/listings?per_page=1000", "/listing/1?ship=inf"])
def test_f19_bad_numbers_are_422_not_500(client, path):
    r = client.get(path)
    assert r.status_code == 422, (path, r.status_code)
    if path.startswith("/api"):
        assert r.json()["error"] == "invalid parameters"
    else:
        assert "Some inputs were not valid" in r.text and "Traceback" not in r.text


def test_f20_like_wildcards_are_literal(client):
    assert client.get("/api/v1/listings?q=%25").json()["total"] == 0
    assert client.get("/api/v1/listings?q=_").json()["total"] == 0
    assert client.get("/api/v1/listings?q=" + "a" * 500).status_code == 422
    assert client.get("/api/v1/listings?q=lenovo").json()["total"] > 0  # normal search still works


# ---------------- F18: outbound links ----------------
def test_f18_go_refuses_non_http_targets(seeded):
    with seeded.session() as s:
        l = s.query(Listing).first()
        l.url = "javascript:alert(1)"
        lid = l.id
    c = TestClient(create_app(seeded))
    r = c.get(f"/go/{lid}", follow_redirects=False)
    assert r.status_code == 404 and "location" not in r.headers


def test_f18_ingest_rejects_offsite_and_non_http_urls(db):
    from conftest import FIX
    html = (FIX / "books_p1.html").read_text().replace('href="a-light-in-the-attic_1000/index.html"', 'href="javascript:alert(1)"')
    BOOKS = "https://books.toscrape.com/catalogue/page-1.html"
    from watchtower.models import CollectionRun
    rid = run_source(db, "books_toscrape", fetcher=FakeFetcher({BOOKS: html.replace('<li class="next"><a href="page-2.html">next</a></li>', "")}),
                     now_fn=utcnow)
    with db.session() as s:
        assert s.query(Listing).count() <= 20 and s.get(CollectionRun, rid).items_invalid >= 1
        assert all(l.url.startswith("https://books.toscrape.com/") for l in s.query(Listing))


# ---------------- errors ----------------
def test_error_pages_are_friendly(client):
    r = client.get("/listing/999999")
    assert r.status_code == 404 and "Page not found" in r.text and "Traceback" not in r.text
    j = client.get("/api/v1/listings/999999")
    assert j.status_code == 404 and j.json() == {"error": "listing not found"}
    assert client.get("/nope").status_code == 404


def test_weak_admin_token_disables_admin(seeded, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "short")
    c = TestClient(create_app(seeded))
    assert c.post("/admin/login", data={"token": "short"}, follow_redirects=False).status_code == 303
    assert c.get("/admin", headers={"accept": "application/json"}).status_code == 403  # even with the "right" weak token
    assert c.get("/admin", headers={"accept": "application/json", "X-Admin-Token": "short"}).status_code == 403


def test_listings_api_data_class_keeps_demo_rows_out_of_real_view(client):
    """Demo/sandbox rows are excluded by data=real; the default (no parameter) is unchanged for existing callers."""
    everything = client.get("/api/v1/listings").json()["total"]
    demo = client.get("/api/v1/listings?data=demo").json()["total"]
    real = client.get("/api/v1/listings?data=real").json()["total"]
    assert everything > 0 and demo == everything   # the seeded catalogue is demo data
    assert real == 0                              # so a real-data view must be empty, not padded with demo rows
    assert client.get("/api/v1/listings?data=bogus").status_code == 200  # unknown value falls back to 'all' semantics
