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


def _empty_db():
    from watchtower.db import Database
    d = Database("sqlite:///:memory:")
    d.init()
    return d


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
    client.get("/notifications")  # writes the alert row so the per-id check always runs
    nid = client.get("/api/v1/notifications").json()["items"][0]["id"]
    checks = [
        ("post", "/api/v1/watchlist", {"json": {"listing_id": lid}}),
        ("delete", f"/api/v1/watchlist/{lid}", {}),
        ("post", "/api/v1/notifications/read-all", {}),
        ("post", "/notifications/read-all", {"data": {}}),
        ("post", "/api/v1/match-candidates/1/decision", {"json": {"decision": "reject"}, "headers": {"X-Admin-Token": TOKEN}}),
    ]
    checks.append(("post", f"/api/v1/notifications/{nid}/read", {}))
    for method, path, kw in checks:
        extra = dict(kw.get("headers", {}))            # e.g. the admin token; the Origin is the only difference
        attack = getattr(client, method)(path, headers={**extra, **EVIL}, **{k: v for k, v in kw.items() if k != "headers"})
        assert attack.status_code == 403, (method, path, attack.status_code)
        # control: the same request from the same site is not refused by the Origin check
        control = getattr(client, method)(path, headers={**extra, **SAME}, **{k: v for k, v in kw.items() if k != "headers"})
        assert control.status_code != 403, (method, path, control.status_code)


def test_an_admin_cookie_cannot_be_used_cross_origin(web, token):
    db, client, _ = web
    login = client.post("/admin/login", data={"token": TOKEN}, headers=SAME, follow_redirects=False)
    assert "err=" not in login.headers.get("location", "")          # a failed login redirects to ?err=1
    assert "wt_admin=" in login.headers.get("set-cookie", "")        # and a successful one sets the admin session
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


def test_the_admin_session_is_scoped_to_the_admin_pages_and_their_json_only(token):
    client = TestClient(create_app(_empty_db()), base_url="http://testserver")
    login = client.post("/admin/login", data={"token": TOKEN}, headers={"Origin": "http://testserver"}, follow_redirects=False)
    cookies = [c for c in login.headers.get_list("set-cookie") if c.startswith("wt_admin=")]
    import re
    paths = {re.search(r"[Pp]ath=([^;]+)", c).group(1) for c in cookies}
    assert paths == {"/admin", "/api/v1/match-candidates", "/api/v1/admin"}     # never the public pages
    assert all("httponly" in c.lower() and "samesite=strict" in c.lower() for c in cookies)


def test_the_admin_json_accepts_the_admin_session_cookie_from_the_same_site(token):
    db = _empty_db()
    client = TestClient(create_app(db), base_url="http://testserver")
    assert client.post("/admin/login", data={"token": TOKEN}, headers={"Origin": "http://testserver"}, follow_redirects=False).status_code == 303
    assert client.get("/api/v1/match-candidates", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.get("/api/v1/match-candidates").status_code == 200   # the cookie is sent on same-site requests


def test_a_failed_commit_on_the_json_match_decision_is_not_reported_as_success(token):
    from sqlalchemy import event
    from fastapi.testclient import TestClient
    from shopping.web.app import create_app
    from tests._ml_helpers import mk_listing, mk_source
    from watchtower.models import MatchCandidate, Product
    db = _empty_db()
    with db.session() as s:
        src = mk_source(s, "decsrc")
        l = mk_listing(s, src, "d1", "Decision board", price=500)
        p = Product(title="Other")
        s.add(p); s.flush()
        s.add(MatchCandidate(listing_id=l.id, product_id=p.id, score=0.5, reasons=[], status="pending"))
        s.commit()
        cid = s.query(MatchCandidate.id).first()[0]
    client = TestClient(create_app(db), base_url="http://testserver", raise_server_exceptions=False)
    h = {"X-Admin-Token": TOKEN, "Origin": "http://testserver"}

    def boom(session):
        raise RuntimeError("injected commit failure")
    event.listen(db.Session, "before_commit", boom)
    try:
        r = client.post(f"/api/v1/match-candidates/{cid}/decision", json={"decision": "reject"}, headers=h)
    finally:
        event.remove(db.Session, "before_commit", boom)
    assert r.status_code >= 500, "a decision that did not commit must not be reported as recorded"
    with db.session() as s:
        assert s.get(MatchCandidate, cid).status == "pending"


def test_the_deals_page_states_the_configured_history_rule(token):
    from fastapi.testclient import TestClient
    from shopping.web.app import create_app
    from watchtower import config
    client = TestClient(create_app(_empty_db()), base_url="http://testserver")
    body = client.get("/deals").text
    assert f"{config.MIN_HISTORY_SPAN_DAYS}+ days" in body
    assert "7+ days" not in body or config.MIN_HISTORY_SPAN_DAYS == 7
