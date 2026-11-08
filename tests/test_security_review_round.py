"""Security review regression tests: Origin enforcement on every new state-changing route, owner isolation, admin
CSRF on JSON endpoints, and safe response headers."""
import pytest
from fastapi.testclient import TestClient

from shopping.web.app import create_app
from tests._ml_helpers import mk_listing, mk_source
from watchtower import config, monitor, notifications as notes
from watchtower.models import Listing, MatchCandidate, Product

TOKEN = "unit-test-token-0123456789abcdef"
EVIL = {"Origin": "https://evil.example"}
SAME = {"Origin": "http://testserver"}


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    return TOKEN


@pytest.fixture
def web(db):
    notes.migrate(db.engine, apply=True)
    with db.session() as s:
        src = mk_source(s, "secrev")
        lid = mk_listing(s, src, "x1", "Secure widget", price=800).id
        s.commit()
    client = TestClient(create_app(db), base_url="http://testserver")
    assert client.get("/").status_code == 200  # owner cookie
    return db, client, lid


def test_cross_origin_writes_are_refused_on_every_new_state_route(web, token):
    db, client, lid = web
    with db.session() as s:
        monitor.add_watch(s, lid, client.cookies.get("wt_owner"), target_price_minor=900)
        s.commit()
    nid = client.get("/api/v1/notifications").json()["items"][0]["id"] if client.get("/api/v1/notifications").json()["items"] else None
    checks = [
        ("post", "/api/v1/watchlist", {"json": {"listing_id": lid}}),
        ("delete", f"/api/v1/watchlist/{lid}", {}),
        ("post", "/api/v1/notifications/read-all", {}),
        ("post", "/notifications/read-all", {"data": {}}),
        ("post", "/api/v1/match-candidates/1/decision", {"json": {"decision": "reject"}, "headers": {"X-Admin-Token": TOKEN}}),
    ]
    if nid is not None:
        checks.append(("post", f"/api/v1/notifications/{nid}/read", {}))
    for method, path, kw in checks:
        headers = {**kw.pop("headers", {}), **EVIL}
        r = getattr(client, method)(path, headers=headers, **kw)
        assert r.status_code == 403, (method, path, r.status_code)


def test_an_admin_cookie_cannot_be_used_cross_origin(web, token):
    db, client, _ = web
    login = client.post("/admin/login", data={"token": TOKEN}, headers=SAME, follow_redirects=False)
    assert login.status_code in (303, 302, 200)
    r = client.post("/api/v1/match-candidates/1/decision", json={"decision": "reject"}, headers=EVIL)
    assert r.status_code == 403  # the cookie alone must not authorise a cross-site write


def test_one_browser_cannot_delete_another_browsers_watch_item(web):
    db, owner_client, lid = web
    with db.session() as s:
        monitor.add_watch(s, lid, owner_client.cookies.get("wt_owner"), target_price_minor=900)
        s.commit()
    other = TestClient(create_app(db), base_url="http://testserver")
    assert other.get("/").status_code == 200
    assert other.delete(f"/api/v1/watchlist/{lid}", headers=SAME).status_code == 404
    assert [i["listing_id"] for i in owner_client.get("/api/v1/watchlist").json()["items"]] == [lid]


def test_a_forged_owner_cookie_is_replaced_not_trusted(web):
    db, _, _ = web
    forged = TestClient(create_app(db), base_url="http://testserver")
    r = forged.get("/", headers={"Cookie": "wt_owner=../../admin"})
    issued = r.headers.get("set-cookie", "")
    assert "wt_owner=" in issued and "../../admin" not in issued
    import re
    assert re.search(r"wt_owner=[0-9a-f]{32}", issued)  # a fresh, well-formed owner replaces the forged value


def test_responses_carry_the_security_headers(web):
    _, client, _ = web
    r = client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    sign_in = client.get("/admin/login")
    assert sign_in.headers["cache-control"] == "no-store"  # the sign-in page must never be cached
