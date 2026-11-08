"""Web security layer: admin auth (fail-closed), CSRF origin check, per-browser owner cookie, rate limiting,
security headers. See docs/AUDIT_REPORT.md (F6, F7, F8) for why each piece exists."""
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from watchtower import config

COOKIE_ADMIN = "wt_admin"
COOKIE_OWNER = "wt_owner"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
CSP = ("default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


COOKIE_TTL_S = 8 * 3600


def admin_cookie_value(token: str, now: float | None = None) -> str:
    """'<issued-at>.<HMAC(token, issued-at)>': never contains the token, differs per login, and EXPIRES server-side. Rotating
    WATCHTOWER_ADMIN_TOKEN invalidates every session (a static HMAC of the token could never be revoked)."""
    ts = int(time.time() if now is None else now)
    return f"{ts}.{hmac.new(token.encode(), f'wt-admin-session-v2.{ts}'.encode(), hashlib.sha256).hexdigest()}"


def cookie_valid(value: str | None, token: str, now: float | None = None) -> bool:
    try:
        ts_s, mac = (value or "").split(".", 1)
        ts = int(ts_s)
    except ValueError:
        return False
    age = (time.time() if now is None else now) - ts
    if not 0 <= age <= COOKIE_TTL_S:
        return False
    expected = hmac.new(token.encode(), f"wt-admin-session-v2.{ts}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac.encode("utf-8", "ignore"), expected.encode())  # non-ASCII input must not raise


def token_ok(candidate: str | None) -> bool:
    tok = config.ADMIN_TOKEN
    return bool(tok and candidate and hmac.compare_digest(candidate.encode(), tok.encode()))


def is_admin(request: Request) -> bool:
    tok = config.ADMIN_TOKEN
    if not tok or len(tok) < MIN_TOKEN_LEN:
        return False  # fail closed: no (or weak) token configured => admin disabled
    if token_ok(request.headers.get("x-admin-token")):
        return True
    return cookie_valid(request.cookies.get(COOKIE_ADMIN), tok)


MIN_TOKEN_LEN = 20


def require_admin(request: Request) -> None:
    if not config.ADMIN_TOKEN:
        raise HTTPException(403, "Admin is disabled. Set WATCHTOWER_ADMIN_TOKEN to enable it.")
    if len(config.ADMIN_TOKEN) < MIN_TOKEN_LEN:
        raise HTTPException(403, f"Admin is disabled: WATCHTOWER_ADMIN_TOKEN must be at least {MIN_TOKEN_LEN} characters.")
    if not is_admin(request):
        is_api = request.url.path.startswith("/api/")  # API callers get a JSON refusal, never a redirect to a sign-in page
        if request.method == "GET" and not is_api and "application/json" not in request.headers.get("accept", ""):
            raise HTTPException(303, headers={"Location": "/admin/login"})
        raise HTTPException(403, "Admin authentication required.")


def same_origin(request: Request) -> bool:
    """Browsers always send Origin on cross-site POSTs; reject any mismatch (CSRF). Non-browser clients send none."""
    host = request.headers.get("host", "")
    for h in ("origin", "referer"):
        v = request.headers.get(h)
        if v:
            return v != "null" and urlparse(v).netloc == host
    return True


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()  # rightmost = the hop our own proxy appended
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self):
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def allow(self, ip: str, bucket: str, limit: int, window: float = 60.0) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        q = self.hits[(ip, bucket)]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(self.hits) > 20000:  # bound memory: drop the stalest half, never reset everyone (login buckets included)
            for k in sorted(self.hits, key=lambda k: self.hits[k][-1] if self.hits[k] else 0)[:10000]:
                del self.hits[k]
        return True


def _bucket(path: str, method: str) -> tuple[str, int, float]:
    """(bucket name, limit, window seconds)"""
    base = config.RATE_LIMIT_PER_MIN
    if path == "/admin/login" and method == "POST":
        return "login", min(10, base) if base else 0, 60.0
    if path.startswith(("/research", "/api/v1/research")):
        return "research", min(20, base) if base else 0, 60.0
    if path.startswith("/watch/") and method == "POST":  # every cookie-less POST mints a new owner: bound row creation per client
        return "watch", min(30, base) if base else 0, 3600.0
    if path.startswith("/go/"):  # each outbound click writes a row (and any page can embed <img src=/go/N>)
        return "go", min(60, base) if base else 0, 60.0
    return "general", base, 60.0


def install(app: FastAPI) -> None:
    limiter = RateLimiter()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if request.method in UNSAFE and not same_origin(request):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        if not path.startswith("/static"):
            name, limit, window = _bucket(path, request.method)
            if not limiter.allow(client_ip(request), name, limit, window):
                return PlainTextResponse("Too many requests. Slow down.", status_code=429, headers={"Retry-After": "60"})
        owner = request.cookies.get(COOKIE_OWNER, "")
        fresh = not re.fullmatch(r"[0-9a-f]{32}", owner)
        if fresh:
            owner = secrets.token_hex(16)
        request.state.owner = owner
        resp = await call_next(request)
        if fresh and not path.startswith(("/static", "/api")):
            resp.set_cookie(COOKIE_OWNER, owner, max_age=365 * 86400, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https" or config.COOKIE_SECURE)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if path.startswith("/admin"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
