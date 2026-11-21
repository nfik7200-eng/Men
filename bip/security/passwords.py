"""Password hashing and policy, with the standard library only (``hashlib.scrypt``).

Hashes are self-describing (``scrypt$n$r$p$salt$hash``) so the cost can be raised later and old
hashes still verify. Verification is constant-time. Nothing here logs or returns a password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_N, _R, _P = 2**14, 8, 1
_DKLEN = 32
MIN_LENGTH = 12
MAX_LENGTH = 256
_COMMON = frozenset(
    {
        "password1234",
        "passwordpassword",
        "123456789012",
        "qwertyuiop12",
        "letmein123456",
        "administrator",
        "changemechangeme",
    }
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, *, n: int = _N, r: int = _R, p: int = _P) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=_DKLEN)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """True only when ``password`` matches ``stored``. Malformed hashes never match."""
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=_DKLEN,
        )
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError, MemoryError):
        return False


_DUMMY = hash_password("not-a-real-password-for-timing")


def burn_time(password: str) -> None:
    """Spend the cost of one verification, so an unknown user takes as long as a known one."""
    verify_password(password, _DUMMY)


def password_problem(password: str, username: str) -> str | None:
    """Why ``password`` is not acceptable, or ``None`` if it is."""
    if len(password) < MIN_LENGTH:
        return f"password must be at least {MIN_LENGTH} characters"
    if len(password) > MAX_LENGTH:
        return f"password must be at most {MAX_LENGTH} characters"
    lowered = password.lower()
    if username and username.lower() in lowered:
        return "password must not contain the username"
    if lowered in _COMMON or len(set(password)) < 5:
        return "password is too easy to guess"
    return None
