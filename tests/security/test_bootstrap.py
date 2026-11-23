"""Creating the first administrator from the sign-in page, guarded by a one-time setup code.

With sign-in required and no users, the server writes a random code to an owner-only file
(``<data_root>/config/first-admin-code``). Only someone who can read that file can claim the
first administrator account; afterwards the route is closed for good.
"""

from __future__ import annotations

import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bip.api.access import required_role
from tests.security.helpers import API, PASSWORD, Rig


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.dispose()


def code_file(rig: Rig) -> Path:
    return rig.settings.resolved_data_root / "config" / "first-admin-code"


def body(code: str, username: str = "owner", password: str = PASSWORD) -> dict[str, str]:
    return {"code": code, "username": username, "password": password}


def test_a_code_file_is_written_at_start_when_no_user_exists(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        status = c.get(f"{API}/auth/status").json()
        assert status["setup_required"] is True
        path = code_file(rig)
        assert path.is_file()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert len(path.read_text().strip()) >= 20


def test_the_right_code_creates_an_admin_signs_in_and_closes_the_route(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        code = code_file(rig).read_text().strip()
        r = c.post(f"{API}/auth/bootstrap", json=body(code))
        assert r.status_code == 201, r.text
        assert r.json()["user"]["role"] == "admin"
        assert r.json()["csrf_token"]
        assert c.get(f"{API}/auth/me").status_code == 200  # signed in by the same response
        assert not code_file(rig).exists()  # the code is single-use
        again = c.post(f"{API}/auth/bootstrap", json=body(code, "intruder"))
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "already_set_up"
        assert c.get(f"{API}/auth/status").json()["setup_required"] is False


def test_a_wrong_code_is_refused_and_never_echoed(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        r = c.post(f"{API}/auth/bootstrap", json=body("wrong-code-wrong-code"))
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_setup_code"
        assert "wrong-code" not in r.text
        assert not rig.auth.has_users()


def test_repeated_wrong_codes_rotate_the_code(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        first = code_file(rig).read_text().strip()
        for _ in range(5):
            c.post(f"{API}/auth/bootstrap", json=body("guess-guess-guess-guess"))
        assert code_file(rig).read_text().strip() != first
        assert c.post(f"{API}/auth/bootstrap", json=body(first)).status_code == 401


def test_a_weak_password_is_refused_and_the_code_survives(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        code = code_file(rig).read_text().strip()
        r = c.post(f"{API}/auth/bootstrap", json=body(code, password="short"))
        assert r.status_code == 422
        assert code_file(rig).exists()


def test_no_code_and_no_route_when_users_exist(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.user("alice", "admin")
    try:
        with TestClient(rig.app) as c:
            assert not code_file(rig).exists()
            assert c.post(f"{API}/auth/bootstrap", json=body("x" * 24)).status_code == 409
    finally:
        rig.dispose()


def test_bootstrap_is_refused_when_sign_in_is_off(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="disabled")
    try:
        with TestClient(rig.app) as c:
            assert not code_file(rig).exists()
            r = c.post(
                f"{API}/auth/bootstrap", json=body("x" * 24), headers={"X-BIP-Client": "web"}
            )
            assert r.status_code == 409
            assert r.json()["error"]["code"] == "auth_disabled"
    finally:
        rig.dispose()


def test_the_route_is_public_and_audited() -> None:
    assert required_role("POST", "/auth/bootstrap") is None


def test_a_user_created_on_the_server_meanwhile_closes_the_route(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        code = code_file(rig).read_text().strip()
        rig.user("alice", "admin")  # e.g. python -m bip.tools.users, while the API runs
        r = c.post(f"{API}/auth/bootstrap", json=body(code))
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "already_set_up"
