"""ASGI protection layer: throttling, authentication, CSRF and origin checks, roles, audit.

Order of checks for a request under the API prefix:

1. ``OPTIONS`` passes (CORS preflight is answered by the CORS middleware outside this one).
2. Throttle by client address (a stricter bucket for login): 429 with ``Retry-After``.
3. With ``auth.mode = required`` and a non-public route a live session is needed (401). Unsafe
   methods also need a matching ``X-CSRF-Token`` and, when the browser sent an ``Origin``, an
   allowed origin (403). The session's role must satisfy :func:`bip.api.access.required_role`
   (403).
4. Mutating calls are written to the audit log with the outcome (never the body).

The middleware holds no state except the two limiters, so it is safe to build once per app.
"""

from __future__ import annotations

import ipaddress
import secrets
from http.cookies import CookieError, SimpleCookie

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bip.api.access import SAFE_METHODS, allows, required_role
from bip.api.errors import error_response
from bip.config.settings import Settings
from bip.security.auth import AuthService
from bip.security.ratelimit import RateLimiter

COOKIE_NAME = "bip_session"
CSRF_HEADER = "x-csrf-token"
_AUTH_AUDITED_ELSEWHERE = frozenset({"/auth/login", "/auth/logout", "/auth/password"})


def client_ip(scope: Scope, *, trust_proxy: bool) -> str:
    """The caller's address. ``X-Forwarded-For`` counts only when a trusted proxy is configured,
    and then only its last hop (the one the proxy itself observed)."""
    peer = scope.get("client")
    address = str(peer[0]) if peer else "unknown"
    if not trust_proxy:
        return address
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            last = value.decode("latin-1").split(",")[-1].strip()
            try:
                return str(ipaddress.ip_address(last))
            except ValueError:
                return address
    return address


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _session_token(scope: Scope) -> str | None:
    raw = _header(scope, b"cookie")
    if not raw:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(raw)
    except CookieError:
        return None
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else None


class ProtectionMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings, prefix: str) -> None:
        self.app = app
        self.cfg = settings.auth
        self.prefix = prefix
        self.origins = frozenset(settings.api.cors_origins)
        self.limiter = RateLimiter(self.cfg.rate_limit_per_minute)
        self.login_limiter = RateLimiter(self.cfg.login_rate_limit_per_minute)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        rel = path[len(self.prefix) :] or "/"
        method: str = scope["method"]
        ip = client_ip(scope, trust_proxy=self.cfg.trust_proxy_headers)
        state = scope.setdefault("state", {})
        state["client_ip"] = ip
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        allowed, retry = self.limiter.hit(ip)
        if rel == "/auth/login" and method == "POST":
            login_ok, login_retry = self.login_limiter.hit(ip)
            allowed, retry = allowed and login_ok, max(retry, login_retry)
        if not allowed:
            response = error_response(429, "rate_limited", "Too many requests. Slow down.")
            response.headers["Retry-After"] = str(max(1, int(retry + 0.999)))
            await response(scope, receive, send)
            return

        actor: str | None = "local" if self.cfg.mode == "disabled" else None
        if self.cfg.mode == "required":
            needed = required_role(method, rel)
            if needed is not None:
                denied = await self._authorize(scope, rel, method, needed, ip)
                if denied is not None:
                    await denied(scope, receive, send)
                    return
                actor = state["user"].username

        if method in SAFE_METHODS or rel in _AUTH_AUDITED_ELSEWHERE:
            await self.app(scope, receive, send)
            return
        await self._audited(scope, receive, send, actor, method, rel, ip)

    async def _authorize(
        self, scope: Scope, rel: str, method: str, needed: str, ip: str
    ) -> JSONResponse | None:
        """Return a response to send instead of the request, or ``None`` to proceed."""
        auth: AuthService = scope["app"].state.bip.auth
        token = _session_token(scope)
        session = await run_in_threadpool(auth.resolve, token) if token else None
        if session is None:
            return error_response(401, "unauthenticated", "Sign in to use this endpoint.")
        if method not in SAFE_METHODS:
            origin = _header(scope, b"origin")
            if origin is not None and origin not in self._allowed_origins(scope):
                await run_in_threadpool(
                    auth.audit,
                    session.user.username,
                    f"{method} {rel}"[:80],
                    None,
                    "denied",
                    ip,
                    "origin not allowed",
                )
                return error_response(403, "origin_denied", "This origin may not make changes.")
            supplied = _header(scope, CSRF_HEADER.encode()) or ""
            if not secrets.compare_digest(supplied, session.csrf_token):
                await run_in_threadpool(
                    auth.audit,
                    session.user.username,
                    f"{method} {rel}"[:80],
                    None,
                    "denied",
                    ip,
                    "missing or wrong CSRF token",
                )
                return error_response(403, "csrf_failed", "The CSRF token is missing or wrong.")
        if not allows(session.user.role, needed):
            await run_in_threadpool(
                auth.audit,
                session.user.username,
                f"{method} {rel}"[:80],
                None,
                "denied",
                ip,
                f"requires {needed}",
            )
            return error_response(403, "forbidden", f"This action needs the {needed} role.")
        state = scope.setdefault("state", {})
        state["user"] = session.user
        state["session"] = session
        return None

    def _allowed_origins(self, scope: Scope) -> frozenset[str]:
        host = _header(scope, b"host")
        own: set[str] = {f"{scope.get('scheme', 'http')}://{host}"} if host else set()
        return self.origins | own

    async def _audited(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        actor: str | None,
        method: str,
        rel: str,
        ip: str,
    ) -> None:
        seen: dict[str, int] = {"status": 500}

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                seen["status"] = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            context = getattr(scope["app"].state, "bip", None)
            if context is not None:
                service: AuthService = context.auth
                status = seen["status"]
                outcome = "ok" if status < 400 else "denied" if status in (401, 403) else "error"
                await run_in_threadpool(
                    service.audit,
                    actor,
                    f"{method} {rel}"[:80],
                    None,
                    outcome,
                    ip,
                    f"status {status}",
                )
