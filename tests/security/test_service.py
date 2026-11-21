"""Time-dependent auth behaviour (lockout, session expiry) and the user command-line tool."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from bip.config import load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.security.auth import AuthError, AuthService
from bip.tools import users as users_tool
from tests.security.helpers import PASSWORD

T0 = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def auth(engine: Engine, clock: Clock) -> AuthService:
    cfg = load_settings(
        data_root=Path("/unused"),
        env="test",
        auth={"login_max_failures": 3, "login_lockout_minutes": 10, "idle_timeout_minutes": 30},
    ).auth
    service = AuthService(Repository(engine), cfg, clock=clock)
    service.create_user("alice", PASSWORD, "admin")
    return service


def fail(auth: AuthService, times: int, password: str = "wrong-wrong-wrong") -> None:  # noqa: S107
    for _ in range(times):
        with pytest.raises(AuthError):
            auth.authenticate("alice", password, "10.0.0.1")


def test_account_locks_after_repeated_failures_and_recovers(
    auth: AuthService, clock: Clock
) -> None:
    fail(auth, 3)
    with pytest.raises(AuthError):  # the right password does not help while locked
        auth.authenticate("alice", PASSWORD, "10.0.0.1")
    clock.advance(minutes=9)
    with pytest.raises(AuthError):
        auth.authenticate("alice", PASSWORD, "10.0.0.1")
    clock.advance(minutes=2)
    assert auth.authenticate("alice", PASSWORD, "10.0.0.1").username == "alice"


def test_lockout_gives_fresh_attempts_afterwards(auth: AuthService, clock: Clock) -> None:
    fail(auth, 3)
    clock.advance(minutes=11)
    fail(auth, 2)  # two failures after the lock ended do not lock again
    assert auth.authenticate("alice", PASSWORD, "10.0.0.1")


def test_success_resets_the_failure_count(auth: AuthService) -> None:
    fail(auth, 2)
    auth.authenticate("alice", PASSWORD, "10.0.0.1")
    fail(auth, 2)
    assert auth.authenticate("alice", PASSWORD, "10.0.0.1")


def test_lockout_is_recorded_in_the_audit_log(auth: AuthService) -> None:
    fail(auth, 3)
    details = [e["detail"] for e in auth.recent_audit(limit=20, offset=0)["items"]]
    assert any("locked for 10 minutes" in d for d in details if d)


def test_session_expires_absolutely(auth: AuthService, clock: Clock) -> None:
    user = auth.authenticate("alice", PASSWORD, None)
    token, session = auth.start_session(user, None)
    assert session.expires_at == T0 + timedelta(hours=12)
    for _ in range(12):  # active every 30 minutes... for 11.5 hours
        clock.advance(minutes=29)
        assert auth.resolve(token) is not None
        if clock.now >= T0 + timedelta(hours=11):
            break
    clock.advance(hours=1.5)
    assert auth.resolve(token) is None


def test_session_expires_when_idle(auth: AuthService, clock: Clock) -> None:
    token, _ = auth.start_session(auth.authenticate("alice", PASSWORD, None), None)
    clock.advance(minutes=20)
    assert auth.resolve(token) is not None  # touched
    clock.advance(minutes=31)
    assert auth.resolve(token) is None
    assert auth.resolve(token) is None  # and stays gone


def test_unknown_and_oversized_tokens_resolve_to_nothing(auth: AuthService) -> None:
    assert auth.resolve("") is None
    assert auth.resolve("x" * 500) is None
    assert auth.resolve("not-a-real-token") is None


def test_audit_pruning_follows_retention(engine: Engine, clock: Clock) -> None:
    cfg = load_settings(
        data_root=Path("/unused"), env="test", auth={"audit_retention_days": 7}
    ).auth
    service = AuthService(Repository(engine), cfg, clock=clock)
    service.audit("a", "old", None, "ok")
    clock.advance(days=8)
    service.audit("a", "new", None, "ok")
    assert service.prune_audit() == 1
    assert [e["action"] for e in service.recent_audit(limit=10, offset=0)["items"]] == ["new"]


# ------------------------------------------------------------------ command-line tool


def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_ENV", "test")
    monkeypatch.setenv("BIP_DATA_ROOT", str(tmp_path / "data"))


def test_cli_creates_lists_and_resets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_env(tmp_path, monkeypatch)
    env = {"BIP_NEW_PASSWORD": PASSWORD}
    assert users_tool.main(["create", "Admin", "--role", "admin"], env) == 0
    assert "created admin (admin)" in capsys.readouterr().out
    assert users_tool.main(["list"], env) == 0
    assert "admin\tadmin\tenabled" in capsys.readouterr().out
    assert (
        users_tool.main(["set-password", "admin"], {"BIP_NEW_PASSWORD": "another-long-pass"}) == 0
    )
    assert users_tool.main(["set-password", "ghost"], env) == 1
    assert PASSWORD not in capsys.readouterr().out


def test_cli_refuses_weak_or_duplicate_passwords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_env(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="at least 12"):
        users_tool.main(["create", "bob"], {"BIP_NEW_PASSWORD": "short"})
    assert (
        users_tool.main(["create", "bob", "--role", "viewer"], {"BIP_NEW_PASSWORD": PASSWORD}) == 0
    )
    assert (
        users_tool.main(["create", "bob", "--role", "viewer"], {"BIP_NEW_PASSWORD": PASSWORD}) == 1
    )
    assert "already exists" in capsys.readouterr().err


def test_cli_reads_the_password_from_a_file_and_rejects_two_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_env(tmp_path, monkeypatch)
    secret = tmp_path / "pw"
    secret.write_text(PASSWORD + "\n", encoding="utf-8")
    assert users_tool.main(["create", "carol"], {"BIP_NEW_PASSWORD_FILE": str(secret)}) == 0
    with pytest.raises(SystemExit, match="only one"):
        users_tool.main(
            ["create", "dave"], {"BIP_NEW_PASSWORD": PASSWORD, "BIP_NEW_PASSWORD_FILE": str(secret)}
        )


def test_cli_takes_no_password_argument() -> None:
    with pytest.raises(SystemExit):
        users_tool.main(["create", "eve", "--password", PASSWORD], {})


def test_passwords_never_reach_the_database_in_clear(engine: Engine, auth: AuthService) -> None:
    with engine.connect() as conn:
        dump = " ".join(
            str(row)
            for table in ("users", "audit_log", "sessions")
            for row in conn.execute(text(f"SELECT * FROM {table}"))  # noqa: S608
        )
    assert PASSWORD not in dump
