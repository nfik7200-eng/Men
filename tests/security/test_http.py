"""The protection layer over real HTTP: sessions, CSRF, origin, roles, throttling, audit."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bip.api.app import create_app
from bip.config import load_settings
from bip.core.exceptions import ConfigurationError
from tests.security.helpers import API, PASSWORD, Rig


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    r.user("alice", "admin")
    r.user("ana", "analyst")
    r.user("vic", "viewer")
    yield r
    r.dispose()


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict[str, str]:
    r = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


# ------------------------------------------------------------------ public surface


def test_everything_but_the_allow_list_needs_a_session(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        assert c.get(f"{API}/health").status_code == 200
        assert c.get(f"{API}/auth/status").json()["authenticated"] is False
        for path in ("/wallets", "/providers", "/signals", "/settings", "/openapi.json", "/docs"):
            r = c.get(f"{API}{path}")
            assert r.status_code == 401, path
            assert r.json()["error"]["code"] == "unauthenticated"
        assert c.post(f"{API}/watchlists", json={"name": "x"}).status_code == 401


def test_status_reports_setup_when_no_user_exists(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    try:
        with TestClient(rig.app) as c:
            status = c.get(f"{API}/auth/status").json()
        assert status == {
            "mode": "required",
            "setup_required": True,
            "authenticated": False,
            "user": None,
        }
    finally:
        rig.dispose()


def test_disabled_mode_keeps_the_local_prototype_behaviour(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="disabled")
    try:
        with TestClient(rig.app) as c:
            assert c.get(f"{API}/wallets").status_code == 200
            assert c.post(f"{API}/watchlists", json={"name": "local"}).status_code == 201
            assert c.get(f"{API}/auth/status").json()["mode"] == "disabled"
            assert c.get(f"{API}/auth/me").json() == {
                "mode": "disabled",
                "user": None,
                "csrf_token": None,
            }
            login_attempt = c.post(f"{API}/auth/login", json={"username": "a", "password": "b"})
            assert login_attempt.status_code == 409
            created = c.post(
                f"{API}/users", json={"username": "eve", "password": PASSWORD, "role": "admin"}
            )
            assert created.status_code == 409  # no open user creation
    finally:
        rig.dispose()


# ------------------------------------------------------------------ login and sessions


def test_login_failures_are_indistinguishable(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        wrong = c.post(
            f"{API}/auth/login", json={"username": "alice", "password": "nope-nope-nope"}
        )
        unknown = c.post(f"{API}/auth/login", json={"username": "ghost", "password": PASSWORD})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()
        assert wrong.json()["error"]["code"] == "invalid_credentials"
        assert "set-cookie" not in wrong.headers


@pytest.mark.parametrize("secure", [True, False])
def test_session_cookie_flags(tmp_path: Path, secure: bool) -> None:
    rig = Rig(tmp_path, cookie_secure=secure)
    rig.user()
    try:
        with TestClient(rig.app) as c:
            r = c.post(f"{API}/auth/login", json={"username": "alice", "password": PASSWORD})
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "path=/" in cookie
        assert ("secure" in cookie) is secure
        assert "max-age=43200" in cookie
    finally:
        rig.dispose()


def test_session_token_is_not_stored(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        c.post(f"{API}/auth/login", json={"username": "alice", "password": PASSWORD})
        token = c.cookies.get("bip_session")
    assert token
    with rig.engine.connect() as conn:
        stored = conn.execute(text("SELECT token_hash FROM sessions")).scalars().all()
        rows = conn.execute(text("SELECT username, password_hash FROM users")).all()
    assert token not in stored
    assert all(len(h) == 64 for h in stored)
    assert all(PASSWORD not in h and h.startswith("scrypt$") for _, h in rows)


def test_logout_ends_the_session_and_clears_the_cookie(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        csrf = login(c, "alice")
        assert c.get(f"{API}/wallets").status_code == 200
        out = c.post(f"{API}/auth/logout", headers=csrf)
        assert out.status_code == 204
        assert "bip_session=" in out.headers["set-cookie"]
        assert c.get(f"{API}/wallets").status_code == 401


def test_a_forged_or_replayed_token_is_rejected(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        c.cookies.set("bip_session", "forged" * 8)
        assert c.get(f"{API}/wallets").status_code == 401
        c.cookies.clear()
        login(c, "alice")
        old = c.cookies.get("bip_session")
        login(c, "alice")  # signing in again replaces the session
        assert c.cookies.get("bip_session") != old
    with TestClient(rig.app) as other:
        other.cookies.set("bip_session", old or "")
        assert other.get(f"{API}/wallets").status_code == 401


def test_login_throttle_returns_429_with_retry_after(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        codes = [
            c.post(f"{API}/auth/login", json={"username": "ghost", "password": "x"}).status_code
            for _ in range(12)
        ]
        assert codes[:10] == [401] * 10
        assert 429 in codes[10:]
        throttled = c.post(f"{API}/auth/login", json={"username": "ghost", "password": "x"})
        assert throttled.status_code == 429
        assert int(throttled.headers["retry-after"]) >= 1
        assert throttled.json()["error"]["code"] == "rate_limited"


def test_forwarded_for_is_ignored_unless_a_proxy_is_trusted(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        codes = [
            c.post(
                f"{API}/auth/login",
                json={"username": "ghost", "password": "x"},
                headers={"X-Forwarded-For": f"203.0.113.{i}"},
            ).status_code
            for i in range(12)
        ]
    assert 429 in codes  # spoofed addresses did not buy extra attempts


def test_api_wide_rate_limit(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rate_limit_per_minute=5)
    rig.user()
    try:
        with TestClient(rig.app) as c:
            codes = [c.get(f"{API}/auth/status").status_code for _ in range(7)]
        assert codes == [200] * 5 + [429] * 2
    finally:
        rig.dispose()


# ------------------------------------------------------------------ CSRF and origin


def test_mutations_need_the_csrf_token(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        csrf = login(c, "ana")
        body = {"name": "w"}
        missing = c.post(f"{API}/watchlists", json=body)
        assert (missing.status_code, missing.json()["error"]["code"]) == (403, "csrf_failed")
        wrong = c.post(f"{API}/watchlists", json=body, headers={"X-CSRF-Token": "x" * 32})
        assert wrong.status_code == 403
        assert c.post(f"{API}/watchlists", json=body, headers=csrf).status_code == 201
        assert c.get(f"{API}/watchlists").status_code == 200  # reads never need it


def test_foreign_origin_cannot_change_state_even_with_a_valid_token(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        csrf = login(c, "ana")
        evil = c.post(
            f"{API}/watchlists",
            json={"name": "x"},
            headers={**csrf, "Origin": "https://evil.example"},
        )
        assert (evil.status_code, evil.json()["error"]["code"]) == (403, "origin_denied")
        ok = c.post(
            f"{API}/watchlists",
            json={"name": "y"},
            headers={**csrf, "Origin": "http://localhost:5173"},
        )
        assert ok.status_code == 201


def test_csrf_token_belongs_to_one_session(rig: Rig) -> None:
    with TestClient(rig.app) as a, TestClient(rig.app) as b:
        csrf_a = login(a, "ana")
        login(b, "ana")
        assert b.post(f"{API}/watchlists", json={"name": "z"}, headers=csrf_a).status_code == 403


# ------------------------------------------------------------------ roles


def test_viewer_can_read_but_not_write(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        csrf = login(c, "vic")
        assert c.get(f"{API}/signals").status_code == 200
        r = c.post(f"{API}/watchlists", json={"name": "v"}, headers=csrf)
        assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden")
        assert c.get(f"{API}/users").status_code == 403


def test_analyst_can_work_on_stored_data_but_not_contact_providers(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        csrf = login(c, "ana")
        assert c.post(f"{API}/watchlists", json={"name": "a"}, headers=csrf).status_code == 201
        assert c.post(f"{API}/discovery/run", json={}, headers=csrf).status_code == 201
        for path in (
            "/ingestion/wallet/ethereum/0x00000000000000000000000000000000000000aa",
            "/chains/ethereum/latest-block",
            "/providers/probe-runs",
            "/ml/training-runs",
        ):
            r = c.post(f"{API}{path}", json={}, headers=csrf)
            assert r.status_code == 403, path
        assert rig.eth.calls == []  # nothing reached a provider


def test_admin_reaches_provider_actions(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        csrf = login(c, "alice")
        r = c.post(f"{API}/chains/ethereum/latest-block", headers=csrf)
        assert r.status_code not in (401, 403)


# ------------------------------------------------------------------ user management


def test_admin_manages_users_and_the_last_admin_is_protected(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        csrf = login(c, "alice")
        weak = c.post(
            f"{API}/users",
            json={"username": "bob", "password": "short", "role": "viewer"},
            headers=csrf,
        )
        assert weak.status_code == 422
        made = c.post(
            f"{API}/users",
            json={"username": "bob", "password": PASSWORD, "role": "viewer"},
            headers=csrf,
        )
        assert made.status_code == 201
        assert "password" not in made.text
        dup = c.post(
            f"{API}/users",
            json={"username": "BOB", "password": PASSWORD, "role": "viewer"},
            headers=csrf,
        )
        assert dup.status_code == 422
        promoted = c.patch(
            f"{API}/users/{made.json()['id']}", json={"role": "analyst"}, headers=csrf
        )
        assert promoted.json()["role"] == "analyst"
        me = c.get(f"{API}/auth/me").json()["user"]["id"]
        demote = c.patch(f"{API}/users/{me}", json={"role": "viewer"}, headers=csrf)
        assert demote.status_code == 422
        assert "last enabled administrator" in demote.json()["error"]["message"]


def test_disabling_a_user_ends_their_sessions(rig: Rig) -> None:
    with TestClient(rig.app) as admin, TestClient(rig.app) as victim:
        csrf = login(admin, "alice")
        login(victim, "vic")
        assert victim.get(f"{API}/wallets").status_code == 200
        target = next(u for u in rig.auth.list_users() if u.username == "vic")
        admin.patch(f"{API}/users/{target.id}", json={"enabled": False}, headers=csrf)
        assert victim.get(f"{API}/wallets").status_code == 401
        denied = victim.post(f"{API}/auth/login", json={"username": "vic", "password": PASSWORD})
        assert denied.status_code == 401


def test_changing_a_password_needs_the_current_one_and_ends_sessions(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        csrf = login(c, "ana")
        bad = c.post(
            f"{API}/auth/password",
            json={"current_password": "wrong-wrong-wrong", "new_password": "another-long-pass"},
            headers=csrf,
        )
        assert bad.status_code == 422
        ok = c.post(
            f"{API}/auth/password",
            json={"current_password": PASSWORD, "new_password": "another-long-pass"},
            headers=csrf,
        )
        assert ok.status_code == 204
        assert c.get(f"{API}/wallets").status_code == 401
        again = c.post(
            f"{API}/auth/login", json={"username": "ana", "password": "another-long-pass"}
        )
        assert again.status_code == 200
        old = c.post(f"{API}/auth/login", json={"username": "ana", "password": PASSWORD})
        assert old.status_code == 401


# ------------------------------------------------------------------ audit


def test_audit_log_records_events_without_secrets(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        c.post(f"{API}/auth/login", json={"username": "alice", "password": "wrong-wrong-wrong"})
        csrf = login(c, "alice")
        c.post(f"{API}/watchlists", json={"name": "audited"}, headers=csrf)
        c.post(f"{API}/watchlists", json={"name": "no csrf"})
        audit = c.get(f"{API}/audit", params={"limit": 100}).json()
    entries = {(e["actor"], e["action"], e["outcome"]) for e in audit["items"]}
    assert ("alice", "login", "denied") in entries
    assert ("alice", "login", "ok") in entries
    assert ("alice", "POST /watchlists", "ok") in entries
    assert ("alice", "POST /watchlists", "denied") in entries  # the CSRF failure
    dump = str(audit)
    assert PASSWORD not in dump
    assert "wrong-wrong-wrong" not in dump
    assert csrf["X-CSRF-Token"] not in dump


def test_audit_is_admin_only(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        login(c, "ana")
        assert c.get(f"{API}/audit").status_code == 403


# ------------------------------------------------------------------ CORS and startup guard


def test_cors_allows_credentials_and_csrf_header_only_when_auth_is_required(
    rig: Rig, tmp_path: Path
) -> None:
    preflight = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-csrf-token,content-type",
    }
    with TestClient(rig.app) as c:
        r = c.options(f"{API}/watchlists", headers=preflight)
        assert r.status_code == 200
        assert r.headers["access-control-allow-credentials"] == "true"
        assert "x-csrf-token" in r.headers["access-control-allow-headers"].lower()
        foreign = c.options(
            f"{API}/watchlists", headers={**preflight, "Origin": "https://evil.example"}
        )
        assert "access-control-allow-origin" not in foreign.headers
    off = Rig(tmp_path / "off", mode="disabled")
    try:
        with TestClient(off.app) as c:
            r = c.options(f"{API}/watchlists", headers=preflight)
            assert "access-control-allow-credentials" not in r.headers
    finally:
        off.dispose()


def test_unauthenticated_errors_still_carry_cors_headers(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        r = c.get(f"{API}/wallets", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.parametrize(
    ("host", "auth", "allowed"),
    [
        ("127.0.0.1", {"mode": "disabled"}, True),
        ("localhost", {"mode": "disabled"}, True),
        ("0.0.0.0", {"mode": "disabled"}, False),  # noqa: S104 - the point of the test
        ("192.168.1.10", {"mode": "disabled"}, False),
        ("0.0.0.0", {"mode": "required"}, True),  # noqa: S104
        ("0.0.0.0", {"mode": "disabled", "allow_insecure_bind": True}, True),  # noqa: S104
    ],
)
def test_unauthenticated_api_refuses_to_bind_to_a_reachable_address(
    tmp_path: Path, host: str, auth: dict[str, object], allowed: bool
) -> None:
    settings = load_settings(data_root=tmp_path / "d", env="test", api={"host": host}, auth=auth)
    if allowed:
        create_app(settings)
    else:
        with pytest.raises(ConfigurationError, match="authentication is off"):
            create_app(settings)


# ------------------------------------------- local mode (auth disabled): cross-site protection

PROOF = {"X-BIP-Client": "web"}


def test_local_mode_writes_need_proof_an_exact_origin_and_a_local_host(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="disabled")
    with TestClient(rig.app, base_url="http://127.0.0.1:8000") as c:
        c.headers.pop("X-BIP-Client", None)  # this test controls the proof header itself
        url = f"{API}/relationships/rebuild"
        # fail closed: a write with no proof is refused, even from a local script
        bare = c.post(url)
        assert bare.status_code == 403
        assert bare.json()["error"]["code"] == "client_proof_missing"
        assert c.post(url, headers={"X-BIP-Client": ""}).status_code == 403  # empty is no proof
        # a page on another site: browsers cannot add the header cross-site without a preflight
        # CORS refuses, and an Origin that is not on the exact allowlist is refused outright
        evil = c.post(url, headers={**PROOF, "Origin": "https://evil.example"})
        assert evil.status_code == 403
        assert evil.json()["error"]["code"] == "origin_denied"
        near = c.post(url, headers={**PROOF, "Origin": "http://127.0.0.1:5173.evil.example"})
        assert near.status_code == 403  # exact match only, no prefix tricks
        assert c.post(url, headers={**PROOF, "Sec-Fetch-Site": "cross-site"}).status_code == 403
        # host spoofing / DNS rebinding: the attacker's name resolves to 127.0.0.1
        rebound = c.get(f"{API}/signals", headers={"Host": "evil.example:8000"})
        assert rebound.status_code == 403
        assert rebound.json()["error"]["code"] == "host_denied"
        spoofed = c.post(url, headers={**PROOF, "Host": "evil.example"})
        assert spoofed.status_code == 403
        # the preflight a cross-site page would need is not granted to a foreign origin
        pre = c.options(
            url,
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-bip-client",
            },
        )
        assert "access-control-allow-origin" not in pre.headers
        # what keeps working: the local frontend, same-origin calls and scripts that send proof
        ok = {**PROOF, "Origin": "http://127.0.0.1:5173"}
        assert c.post(url, headers=ok).status_code == 201
        assert c.post(url, headers={**PROOF, "Origin": "http://127.0.0.1:8000"}).status_code == 201
        assert c.post(url, headers={**PROOF, "Sec-Fetch-Site": "same-origin"}).status_code == 201
        assert c.post(url, headers=PROOF).status_code == 201  # curl with -H 'X-BIP-Client: cli'
        # reads stay open on a local host
        assert c.get(f"{API}/signals").status_code == 200
        assert c.get(f"{API}/signals", headers={"Host": "localhost:8000"}).status_code == 200
        assert c.get(f"{API}/signals", headers={"Host": "[::1]:8000"}).status_code == 200
    rig.dispose()


def test_local_mode_accepts_hosts_the_operator_lists(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="disabled", settings={"api": {"allowed_hosts": ["bip.lan"]}})
    with TestClient(rig.app, base_url="http://bip.lan") as c:
        assert c.get(f"{API}/signals").status_code == 200
        write = c.post(
            f"{API}/relationships/rebuild", headers={**PROOF, "Origin": "http://bip.lan"}
        )
        assert write.status_code == 201
    rig.dispose()


def test_allowed_hosts_must_be_plain_names(tmp_path: Path) -> None:
    for bad in (["*.lan"], ["bip.lan:8000"], ["BIP.LAN"]):
        with pytest.raises(ConfigurationError):
            load_settings(data_root=tmp_path, env="test", api={"allowed_hosts": bad})


def test_required_mode_is_unchanged_by_the_local_guard(rig: Rig) -> None:
    """Sign-in mode keeps its own checks (session, CSRF, origin); the proof header is not needed."""
    with TestClient(rig.app) as c:
        c.headers.pop("X-BIP-Client", None)
        assert c.post(f"{API}/relationships/rebuild").status_code == 401


def test_heavy_rebuilds_refuse_to_run_twice_at_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="disabled")
    with TestClient(rig.app, base_url="http://127.0.0.1:8000") as c:
        ctx = rig.app.state.bip
        assert ctx.discovery_lock.acquire(blocking=False)
        try:
            busy = c.post(f"{API}/relationships/rebuild")
            assert busy.status_code == 409
            assert busy.json()["error"]["code"] == "discovery_busy"
        finally:
            ctx.discovery_lock.release()
        assert ctx.analysis_lock.acquire(blocking=False)
        try:
            assert c.post(f"{API}/intelligence/opportunities/refresh/ethereum").status_code == 409
        finally:
            ctx.analysis_lock.release()
        assert c.post(f"{API}/relationships/rebuild").status_code == 201
        assert c.post(f"{API}/intelligence/opportunities/refresh/ethereum").status_code == 200
    rig.dispose()


def test_a_normal_launch_marks_an_unmarked_database_as_real_network_data(tmp_path: Path) -> None:
    """Ground truth needs an explicit origin. Tools that serve fixtures mark their databases
    themselves (``offline_fixture``); a normal launch marks an unmarked one ``live_network``,
    and a test-environment app marks nothing."""
    from bip.db.engine import create_db_engine
    from bip.db.repository import Repository
    from tests.api.conftest import roomy_guard

    for env, expected in (("development", "live_network"), ("test", None)):
        settings = load_settings(data_root=tmp_path / env, env=env)
        engine = create_db_engine(settings)
        app = create_app(
            settings,
            engine=engine,
            disk_guard=roomy_guard(settings.resolved_data_root),
            environ={},
        )
        with TestClient(app, base_url="http://127.0.0.1:8000"):
            pass
        assert Repository(engine).get_meta("data_origin") == expected
        engine.dispose()
    settings = load_settings(data_root=tmp_path / "fixture", env="development")
    engine = create_db_engine(settings)
    app = create_app(
        settings,
        engine=engine,
        disk_guard=roomy_guard(settings.resolved_data_root),
        environ={},
        data_origin="offline_fixture",
    )
    with TestClient(app, base_url="http://127.0.0.1:8000"):
        pass
    assert Repository(engine).get_meta("data_origin") == "offline_fixture"  # never overwritten
    engine.dispose()


def test_an_unmarked_database_that_already_holds_data_is_not_assumed_real(
    tmp_path: Path,
) -> None:
    """A seeded or copied database launched normally must not become ground truth: only an empty
    one is marked live_network automatically; otherwise the operator says so explicitly."""
    from bip.db.engine import create_db_engine
    from bip.db.repository import Repository
    from tests.api.conftest import roomy_guard
    from tests.discovery.seed import NOW, Seed, eth_addr, eth_tx

    def launch(root: Path, **cfg: object) -> str | None:
        settings = load_settings(data_root=root, env="development", **cfg)
        engine = create_db_engine(settings)
        if not (root / "seeded").exists():
            Seed(engine).tx(eth_tx(1), eth_addr(1), NOW)  # synthetic rows, origin unknown
            (root / "seeded").touch()
        app = create_app(
            settings, engine=engine, disk_guard=roomy_guard(settings.resolved_data_root), environ={}
        )
        with TestClient(app, base_url="http://127.0.0.1:8000"):
            pass
        origin = Repository(engine).get_meta("data_origin")
        engine.dispose()
        return origin

    root = tmp_path / "seeded-db"
    root.mkdir()
    assert launch(root) == "unknown"
    assert launch(root, data_origin="live_network") == "unknown"  # an existing marker stays
    fresh = tmp_path / "explicit"
    fresh.mkdir()
    assert launch(fresh, data_origin="live_network") == "live_network"  # the operator's word


def test_the_socket_exemption_needs_a_configured_socket(tmp_path: Path) -> None:
    """A connection without a server address (as some ASGI servers report TCP) must not skip the
    host check unless the operator configured a Unix socket."""
    import asyncio

    from bip.api.protection import ProtectionMiddleware

    async def app(scope: object, receive: object, send: object) -> None:  # pragma: no cover
        raise AssertionError("must not be reached")

    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b""}

    settings = load_settings(data_root=tmp_path, env="test", auth={"mode": "disabled"})
    mw = ProtectionMiddleware(app, settings=settings, prefix=API)  # type: ignore[arg-type]
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"{API}/signals",
        "headers": [(b"host", b"evil.example")],
        "server": None,
        "client": ("127.0.0.1", 5000),
    }
    asyncio.run(mw(scope, receive, send))  # type: ignore[arg-type]
    assert sent[0]["status"] == 403
