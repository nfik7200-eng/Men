"""Who may call what. One table, fail-closed.

* Public: health and the login/status endpoints. Nothing else answers without a session.
* ``viewer``: every read.
* ``analyst``: changes to BIP's own analysis data (watchlists, cases, findings, labels, signals,
  alerts, discovery and AI research over stored data).
* ``admin``: anything that contacts a provider, changes provider state, trains a model, or manages
  users and the audit trail, **and any mutating route not listed below**. A route added later is
  therefore admin-only until someone decides otherwise.
"""

from __future__ import annotations

import re

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

PUBLIC_PATHS = frozenset({"/health", "/auth/login", "/auth/status"})
"""Relative to the API prefix."""

SELF_SERVICE = frozenset({"/auth/logout", "/auth/password", "/auth/me"})
"""Any signed-in user, whatever the method."""

ADMIN_ANY = re.compile(r"^/(users|audit)(/|$)")

ANALYST_WRITE = tuple(
    re.compile(p)
    for p in (
        r"^/watchlists(/|$)",
        r"^/investigations(/|$)",
        r"^/intelligence/analyze/",
        r"^/intelligence/findings/",
        r"^/intelligence/opportunities/",
        r"^/intelligence/watchlists/",
        r"^/ml/classify/",
        r"^/ml/labels(/|$)",
        r"^/research/",
        r"^/signals/",
        r"^/alerts/",
        r"^/alert-rules(/|$)",
        r"^/discovery/run$",
        r"^/relationships/rebuild$",
    )
)

ROLE_RANK = {"viewer": 0, "analyst": 1, "admin": 2}


def required_role(method: str, path: str) -> str | None:
    """Role needed for ``method`` on ``path`` (relative to the API prefix); ``None`` if public."""
    if path in PUBLIC_PATHS:
        return None
    if path in SELF_SERVICE:
        return "viewer"
    if ADMIN_ANY.match(path):
        return "admin"
    if method.upper() in SAFE_METHODS:
        return "viewer"
    if any(p.match(path) for p in ANALYST_WRITE):
        return "analyst"
    return "admin"


def allows(role: str, needed: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[needed]
