"""Users, sessions and the audit log.

Design choices that matter:

* One generic failure for every login problem (unknown user, wrong password, disabled, locked), and
  the same amount of work for each, so the response does not reveal which usernames exist.
* Failed logins lock the account for a while. The counter resets when the lock is applied, so the
  user gets fresh attempts once it ends.
* Sessions are server-side. Only a SHA-256 of the bearer token is stored, so a database copy does
  not yield usable cookies. They expire absolutely and when idle, and a password change ends them.
* The last enabled administrator cannot be demoted or disabled.
* The audit log records who did what and with what outcome, never request bodies or secrets.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection

from bip.config.settings import AuthSettings
from bip.db import schema
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.security.passwords import burn_time, hash_password, password_problem, verify_password
from bip.security.secrets import redact_text

ROLES = ("viewer", "analyst", "admin")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.\-]{2,63}$")
_TOUCH_INTERVAL = timedelta(seconds=60)


class AuthError(Exception):
    """Login failed. The message is deliberately generic."""

    def __init__(self) -> None:
        super().__init__("invalid username or password")


@dataclass(frozen=True, slots=True)
class User:
    id: int
    username: str
    role: str
    enabled: bool
    created_at: datetime
    last_login_at: datetime | None
    locked_until: datetime | None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }


@dataclass(frozen=True, slots=True)
class SessionInfo:
    user: User
    csrf_token: str
    expires_at: datetime


def _user(row: Any) -> User:
    return User(
        id=int(row.id),
        username=str(row.username),
        role=str(row.role),
        enabled=bool(row.enabled),
        created_at=row.created_at,
        last_login_at=row.last_login_at,
        locked_until=row.locked_until,
    )


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(
        self,
        repo: Repository,
        cfg: AuthSettings,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._repo = repo
        self._cfg = cfg
        self._clock = clock

    # ------------------------------------------------------------------ users

    def has_users(self) -> bool:
        with self._repo.transaction() as conn:
            return conn.execute(select(func.count()).select_from(schema.users)).scalar_one() > 0

    def create_user(
        self, username: str, password: str, role: str, *, actor: str | None = None
    ) -> User:
        name = username.strip().lower()
        if not _USERNAME.fullmatch(name):
            raise InvalidRecordError(
                "username must be 3-64 characters: lower-case letters, digits, '_', '.' or '-'"
            )
        if role not in ROLE_RANK:
            raise InvalidRecordError(f"role must be one of {', '.join(ROLES)}")
        problem = password_problem(password, name)
        if problem:
            raise InvalidRecordError(problem)
        u = schema.users
        with self._repo.transaction() as conn:
            if conn.execute(select(u.c.id).where(u.c.username == name)).first() is not None:
                raise InvalidRecordError(f"user {name!r} already exists")
            pk = conn.execute(
                insert(u).values(
                    username=name,
                    password_hash=hash_password(password),
                    role=role,
                    enabled=True,
                    created_at=self._clock(),
                    failed_logins=0,
                )
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            row = conn.execute(select(u).where(u.c.id == pk[0])).one()
        self.audit(actor, "user.create", f"{name} ({role})", "ok")
        return _user(row)

    def list_users(self) -> list[User]:
        u = schema.users
        with self._repo.transaction() as conn:
            return [_user(r) for r in conn.execute(select(u).order_by(u.c.id)).all()]

    def get_user(self, user_id: int) -> User:
        u = schema.users
        with self._repo.transaction() as conn:
            row = conn.execute(select(u).where(u.c.id == user_id)).first()
        if row is None:
            raise RecordNotFoundError(f"user {user_id} not found")
        return _user(row)

    def update_user(
        self,
        user_id: int,
        *,
        role: str | None = None,
        enabled: bool | None = None,
        actor: str | None = None,
    ) -> User:
        if role is not None and role not in ROLE_RANK:
            raise InvalidRecordError(f"role must be one of {', '.join(ROLES)}")
        u = schema.users
        with self._repo.transaction() as conn:
            current = conn.execute(select(u).where(u.c.id == user_id)).first()
            if current is None:
                raise RecordNotFoundError(f"user {user_id} not found")
            new_role = role if role is not None else str(current.role)
            new_enabled = enabled if enabled is not None else bool(current.enabled)
            loses_admin = (
                current.role == "admin"
                and current.enabled
                and (new_role != "admin" or not new_enabled)
            )
            if loses_admin and self._enabled_admins(conn) <= 1:
                raise InvalidRecordError("the last enabled administrator cannot be changed")
            conn.execute(
                update(u).where(u.c.id == user_id).values(role=new_role, enabled=new_enabled)
            )
            if not new_enabled or new_role != current.role:
                conn.execute(delete(schema.sessions).where(schema.sessions.c.user_id == user_id))
            row = conn.execute(select(u).where(u.c.id == user_id)).one()
        self.audit(
            actor, "user.update", f"{row.username} role={new_role} enabled={new_enabled}", "ok"
        )
        return _user(row)

    @staticmethod
    def _enabled_admins(conn: Connection) -> int:
        u = schema.users
        return int(
            conn.execute(
                select(func.count()).select_from(u).where(u.c.role == "admin", u.c.enabled)
            ).scalar_one()
        )

    def set_password(self, user_id: int, new_password: str, *, actor: str | None) -> None:
        user = self.get_user(user_id)
        problem = password_problem(new_password, user.username)
        if problem:
            raise InvalidRecordError(problem)
        u = schema.users
        with self._repo.transaction() as conn:
            conn.execute(
                update(u)
                .where(u.c.id == user_id)
                .values(
                    password_hash=hash_password(new_password), failed_logins=0, locked_until=None
                )
            )
            conn.execute(delete(schema.sessions).where(schema.sessions.c.user_id == user_id))
        self.audit(actor, "user.password", user.username, "ok")

    def change_own_password(self, user_id: int, current: str, new: str) -> None:
        u = schema.users
        with self._repo.transaction() as conn:
            row = conn.execute(select(u).where(u.c.id == user_id)).first()
        if row is None or not verify_password(current, str(row.password_hash)):
            self.audit(str(row.username) if row else None, "user.password", "self", "denied")
            raise InvalidRecordError("the current password is not correct")
        self.set_password(user_id, new, actor=str(row.username))

    # ------------------------------------------------------------------ login

    def authenticate(self, username: str, password: str, ip: str | None) -> User:
        """Return the user on success. Raise :class:`AuthError` for every kind of failure."""
        name = username.strip().lower()
        now = self._clock()
        u = schema.users
        with self._repo.transaction() as conn:
            row = conn.execute(select(u).where(u.c.username == name)).first()
        if row is None:
            burn_time(password)
            self.audit(name[:64] or None, "login", None, "denied", ip, "unknown user")
            raise AuthError
        if not row.enabled:
            burn_time(password)
            self.audit(name, "login", None, "denied", ip, "account disabled")
            raise AuthError
        if row.locked_until is not None and row.locked_until > now:
            burn_time(password)
            self.audit(name, "login", None, "denied", ip, "account locked")
            raise AuthError
        if not verify_password(password, str(row.password_hash)):
            self._record_failure(int(row.id), name, int(row.failed_logins), now, ip)
            raise AuthError
        with self._repo.transaction() as conn:
            conn.execute(
                update(u)
                .where(u.c.id == row.id)
                .values(failed_logins=0, locked_until=None, last_login_at=now)
            )
            fresh = conn.execute(select(u).where(u.c.id == row.id)).one()
        self.audit(name, "login", None, "ok", ip)
        return _user(fresh)

    def _record_failure(
        self, user_id: int, name: str, failed: int, now: datetime, ip: str | None
    ) -> None:
        failed += 1
        locked: datetime | None = None
        detail = f"wrong password ({failed} of {self._cfg.login_max_failures})"
        if failed >= self._cfg.login_max_failures:
            locked = now + timedelta(minutes=self._cfg.login_lockout_minutes)
            failed = 0
            detail = f"wrong password; locked for {self._cfg.login_lockout_minutes} minutes"
        u = schema.users
        with self._repo.transaction() as conn:
            conn.execute(
                update(u).where(u.c.id == user_id).values(failed_logins=failed, locked_until=locked)
            )
        self.audit(name, "login", None, "denied", ip, detail)

    # ------------------------------------------------------------------ sessions

    def start_session(self, user: User, ip: str | None) -> tuple[str, SessionInfo]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = self._clock()
        expires = now + timedelta(hours=self._cfg.session_ttl_hours)
        s = schema.sessions
        with self._repo.transaction() as conn:
            conn.execute(delete(s).where(s.c.expires_at < now))  # opportunistic cleanup
            conn.execute(
                insert(s).values(
                    token_hash=_digest(token),
                    user_id=user.id,
                    csrf_token=csrf,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=expires,
                    ip=ip,
                )
            )
        return token, SessionInfo(user=user, csrf_token=csrf, expires_at=expires)

    def resolve(self, token: str) -> SessionInfo | None:
        """The live session for ``token``; ``None`` if unknown, expired, idle or revoked."""
        if not token or len(token) > 200:
            return None
        s, u = schema.sessions, schema.users
        now = self._clock()
        with self._repo.transaction() as conn:
            row = conn.execute(
                select(
                    s.c.id,
                    s.c.csrf_token,
                    s.c.expires_at,
                    s.c.last_seen_at,
                    u.c.id.label("uid"),
                    u.c.username,
                    u.c.role,
                    u.c.enabled,
                    u.c.created_at,
                    u.c.last_login_at,
                    u.c.locked_until,
                )
                .join(u, u.c.id == s.c.user_id)
                .where(s.c.token_hash == _digest(token))
            ).first()
            if row is None:
                return None
            idle = timedelta(minutes=self._cfg.idle_timeout_minutes)
            if not row.enabled or row.expires_at <= now or row.last_seen_at + idle <= now:
                conn.execute(delete(s).where(s.c.id == row.id))
                return None
            if now - row.last_seen_at >= _TOUCH_INTERVAL:
                conn.execute(update(s).where(s.c.id == row.id).values(last_seen_at=now))
        user = User(
            id=int(row.uid),
            username=str(row.username),
            role=str(row.role),
            enabled=True,
            created_at=row.created_at,
            last_login_at=row.last_login_at,
            locked_until=row.locked_until,
        )
        return SessionInfo(user=user, csrf_token=str(row.csrf_token), expires_at=row.expires_at)

    def end_session(self, token: str) -> None:
        with self._repo.transaction() as conn:
            conn.execute(
                delete(schema.sessions).where(schema.sessions.c.token_hash == _digest(token))
            )

    # ------------------------------------------------------------------ audit

    def audit(
        self,
        actor: str | None,
        action: str,
        target: str | None,
        outcome: str,
        ip: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._repo.transaction() as conn:
            conn.execute(
                insert(schema.audit_log).values(
                    at=self._clock(),
                    actor=actor[:64] if actor else None,
                    action=action[:80],
                    target=redact_text(target)[:200] if target else None,
                    outcome=outcome[:16],
                    ip=ip[:64] if ip else None,
                    detail=redact_text(detail)[:300] if detail else None,
                )
            )

    def recent_audit(
        self, *, limit: int, offset: int, actor: str | None = None, action: str | None = None
    ) -> dict[str, Any]:
        a = schema.audit_log
        conds = [a.c.id > 0]
        if actor:
            conds.append(a.c.actor == actor)
        if action:
            conds.append(a.c.action == action)
        with self._repo.transaction() as conn:
            total = int(
                conn.execute(select(func.count()).select_from(a).where(*conds)).scalar_one()
            )
            rows = conn.execute(
                select(a).where(*conds).order_by(a.c.id.desc()).limit(limit).offset(offset)
            ).all()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": r.id,
                    "at": r.at.isoformat(),
                    "actor": r.actor,
                    "action": r.action,
                    "target": r.target,
                    "outcome": r.outcome,
                    "ip": r.ip,
                    "detail": r.detail,
                }
                for r in rows
            ],
        }

    def prune_audit(self) -> int:
        cutoff = self._clock() - timedelta(days=self._cfg.audit_retention_days)
        with self._repo.transaction() as conn:
            result = conn.execute(delete(schema.audit_log).where(schema.audit_log.c.at < cutoff))
        return int(result.rowcount or 0)
