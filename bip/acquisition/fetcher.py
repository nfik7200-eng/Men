"""Polite universal fetcher: robots.txt, per-domain token buckets, conditional GET, provenance.

The network is abstracted behind :class:`Transport` so tests use local fakes (never live quota).
A 200 response is not proof of complete data: callers validate bodies (see feeds/exchanges).
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "BIP-research-fetcher/0.1 (+robots-respecting; contact via owner)"
Outcome = Literal["ok", "not_modified", "robots_blocked", "error"]


@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class Transport(Protocol):
    def get(self, url: str, headers: dict[str, str], timeout: float) -> RawResponse: ...


class UrllibTransport:
    """Stdlib transport (no new dependency). Never follows auth/captcha; errors map to status."""

    def get(self, url: str, headers: dict[str, str], timeout: float) -> RawResponse:
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("only http(s) URLs are allowed")
        req = urllib.request.Request(url, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return RawResponse(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
        except urllib.error.HTTPError as e:
            return RawResponse(e.code, {k.lower(): v for k, v in e.headers.items()}, b"")


@dataclass(frozen=True, slots=True)
class Provenance:
    request_id: str
    source_url: str
    fetched_at: str
    status: int | None
    content_sha256: str | None
    etag: str | None
    last_modified: str | None
    cost_estimate_usd: float = 0.0
    provider_id: str = "web"
    method: str = "GET"


@dataclass(frozen=True, slots=True)
class FetchResult:
    outcome: Outcome
    provenance: Provenance
    body: bytes | None = None
    reason: str | None = None


class TokenBucket:
    """Token bucket; ``rate`` tokens/second, burst ``capacity``. Clock/sleep are injectable."""

    def __init__(
        self,
        rate: float,
        capacity: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self.rate, self.capacity = rate, capacity
        self._clock, self._sleep = clock, sleep
        self._tokens = capacity
        self._last = clock()

    def acquire(self) -> float:
        """Block until a token is available; return seconds waited."""
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now
        waited = 0.0
        if self._tokens < 1.0:
            waited = (1.0 - self._tokens) / self.rate
            self._sleep(waited)
            self._last = self._clock()
            self._tokens = 1.0
        self._tokens -= 1.0
        return waited


@dataclass(slots=True)
class _CacheEntry:
    etag: str | None
    last_modified: str | None
    body: bytes


@dataclass
class Fetcher:
    transport: Transport = field(default_factory=UrllibTransport)
    default_rate: float = 1.0  # requests/second per domain
    domain_rates: dict[str, float] = field(default_factory=lambda: {})
    timeout: float = 15.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _buckets: dict[str, TokenBucket] = field(default_factory=lambda: {}, init=False)
    _robots: dict[str, RobotFileParser | None] = field(default_factory=lambda: {}, init=False)
    _cache: dict[str, _CacheEntry] = field(default_factory=lambda: {}, init=False)

    def _bucket(self, host: str) -> TokenBucket:
        if host not in self._buckets:
            rate = self.domain_rates.get(host, self.default_rate)
            self._buckets[host] = TokenBucket(rate, 1.0, self.clock, self.sleep)
        return self._buckets[host]

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._bucket(parts.netloc).acquire()
            rp = RobotFileParser()
            try:
                resp = self.transport.get(
                    f"{origin}/robots.txt", {"User-Agent": USER_AGENT}, self.timeout
                )
            except Exception:  # noqa: BLE001
                self._robots[origin] = None  # unreachable robots: fail closed below
            else:
                if resp.status in (401, 403):
                    rp.parse(["User-agent: *", "Disallow: /"])
                elif resp.status == 200:
                    rp.parse(resp.body.decode("utf-8", "replace").splitlines())
                else:
                    rp.parse(["User-agent: *", "Allow: /"])  # no robots file published
                self._robots[origin] = rp
        rp_cached = self._robots[origin]
        return rp_cached is not None and rp_cached.can_fetch(USER_AGENT, url)

    def fetch(
        self, url: str, *, provider_id: str = "web", cost_estimate_usd: float = 0.0
    ) -> FetchResult:
        rid = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()

        def prov(
            status: int | None, body: bytes | None, etag: str | None, lm: str | None
        ) -> Provenance:
            digest = hashlib.sha256(body).hexdigest() if body is not None else None
            return Provenance(
                rid, url, now, status, digest, etag, lm, cost_estimate_usd, provider_id
            )

        if not self._allowed(url):
            return FetchResult("robots_blocked", prov(None, None, None, None), None, "robots.txt")
        headers = {"User-Agent": USER_AGENT}
        cached = self._cache.get(url)
        if cached:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = cached.last_modified
        self._bucket(urlsplit(url).netloc).acquire()
        try:
            resp = self.transport.get(url, headers, self.timeout)
        except Exception as e:  # noqa: BLE001
            return FetchResult("error", prov(None, None, None, None), None, type(e).__name__)
        if resp.status == 304 and cached:
            return FetchResult(
                "not_modified", prov(304, cached.body, cached.etag, cached.last_modified), None
            )
        if resp.status != 200:
            return FetchResult(
                "error", prov(resp.status, None, None, None), None, f"http_{resp.status}"
            )
        etag, lm = resp.headers.get("etag"), resp.headers.get("last-modified")
        self._cache[url] = _CacheEntry(etag, lm, resp.body)
        return FetchResult("ok", prov(200, resp.body, etag, lm), resp.body)
