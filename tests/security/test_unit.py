"""Password hashing, rate limiter and the access policy table."""

from __future__ import annotations

import pytest

from bip.api.access import allows, required_role
from bip.security.passwords import hash_password, password_problem, verify_password
from bip.security.ratelimit import RateLimiter


def test_password_hash_verifies_and_is_salted() -> None:
    a, b = hash_password("correct-horse-battery"), hash_password("correct-horse-battery")
    assert a != b  # random salt
    assert a.startswith("scrypt$")
    assert verify_password("correct-horse-battery", a)
    assert not verify_password("correct-horse-batterz", a)
    assert "correct-horse" not in a


@pytest.mark.parametrize(
    "stored", ["", "x", "scrypt$1$2", "md5$a$b$c$d$e", "scrypt$x$8$1$AA==$AA=="]
)
def test_malformed_hashes_never_verify(stored: str) -> None:
    assert verify_password("anything", stored) is False


@pytest.mark.parametrize(
    ("password", "username", "problem"),
    [
        ("short", "alice", "at least 12"),
        ("alice-is-great-1", "alice", "username"),
        ("passwordpassword", "bob", "easy to guess"),
        ("aaaaaaaaaaaaaaaa", "bob", "easy to guess"),
        ("x" * 300, "bob", "at most"),
        ("correct-horse-battery", "alice", None),
    ],
)
def test_password_policy(password: str, username: str, problem: str | None) -> None:
    result = password_problem(password, username)
    assert (result is None) if problem is None else (problem in (result or ""))


def test_rate_limiter_window_and_retry_after() -> None:
    now = [0.0]
    limiter = RateLimiter(3, 60.0, clock=lambda: now[0])
    assert [limiter.hit("a")[0] for _ in range(3)] == [True, True, True]
    allowed, retry = limiter.hit("a")
    assert allowed is False
    assert 59 <= retry <= 60
    assert limiter.hit("b")[0] is True  # other keys are independent
    now[0] = 61.0
    assert limiter.hit("a")[0] is True


def test_rate_limiter_memory_is_bounded() -> None:
    limiter = RateLimiter(5, 60.0, max_keys=100)
    for i in range(1_000):
        limiter.hit(f"10.0.{i // 250}.{i % 250}")
    assert limiter.tracked_keys <= 100


def test_rate_limiter_zero_means_off() -> None:
    limiter = RateLimiter(0)
    assert all(limiter.hit("a")[0] for _ in range(1_000))


@pytest.mark.parametrize(
    ("method", "path", "role"),
    [
        ("GET", "/health", None),
        ("POST", "/auth/login", None),
        ("GET", "/auth/status", None),
        ("GET", "/wallets/ethereum/0xabc", "viewer"),
        ("GET", "/openapi.json", "viewer"),
        ("POST", "/auth/logout", "viewer"),
        ("POST", "/auth/password", "viewer"),
        ("POST", "/watchlists", "analyst"),
        ("DELETE", "/watchlists/3/entries/4", "analyst"),
        ("PATCH", "/investigations/2", "analyst"),
        ("POST", "/signals/9/promote", "analyst"),
        ("POST", "/alert-rules", "analyst"),
        ("POST", "/discovery/run", "analyst"),
        ("POST", "/research/runs", "analyst"),
        ("POST", "/ml/labels", "analyst"),
        ("POST", "/ingestion/wallet/ethereum/0xabc", "admin"),
        ("POST", "/chains/ethereum/latest-block", "admin"),
        ("POST", "/providers/probe-runs", "admin"),
        ("PATCH", "/providers/publicnode-eth", "admin"),
        ("POST", "/providers/publicnode-eth/health-check", "admin"),
        ("POST", "/intelligence/verification/ethereum/0xabc", "admin"),
        ("POST", "/ml/training-runs", "admin"),
        ("GET", "/users", "admin"),
        ("POST", "/users", "admin"),
        ("GET", "/audit", "admin"),
        ("POST", "/something/new", "admin"),  # unknown mutating route fails closed
        ("DELETE", "/something/new/1", "admin"),
    ],
)
def test_access_policy(method: str, path: str, role: str | None) -> None:
    assert required_role(method, path) == role


def test_role_ordering() -> None:
    assert allows("admin", "viewer")
    assert allows("admin", "admin")
    assert allows("analyst", "viewer")
    assert not allows("analyst", "admin")
    assert not allows("viewer", "analyst")
    assert not allows("nonsense", "viewer")
