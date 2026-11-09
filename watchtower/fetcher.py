"""Polite, SSRF-safe HTTP fetching: host allowlist, robots.txt on every hop, per-host delay, retry/backoff.
Never bypasses blocks; never follows a redirect to a host it was not allowed to start from."""
import hashlib
import ipaddress
import socket
import re
import time
import zlib
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from . import config
from .robots import Robots

MAX_REDIRECTS = 4
_REDIRECT = (301, 302, 303, 307, 308)


class FetchBlocked(Exception):
    """robots.txt / allowlist / server refusal (401/403/451). We stop; we never work around it."""


class FetchDisallowed(FetchBlocked):
    """This ONE URL may not be fetched (robots.txt disallow, host outside the allowlist, unsupported/internal target).
    Unlike a server refusal (401/403/451) it says nothing about the rest of the site, so a crawl records it and goes on."""


class FetchError(Exception):
    pass


class FetchGone(FetchError):
    """404/410 for the URL itself: the page no longer exists (evidence of removal, unlike a missing sitemap entry)."""


@dataclass
class FetchResult:
    status: int  # 200 or 304
    text: str | None
    etag: str | None = None
    last_modified: str | None = None
    nbytes: int = 0

MAX_BYTES = 16 * 1024 * 1024
_OK_TYPES = ("text/", "application/xml", "application/json", "application/xhtml", "application/rss", "application/atom", "application/ld+json")


def _host_ok(host: str, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    return any(host == a or host.endswith("." + a) for a in allowed if a)


MAX_ROBOTS_BYTES = 512 * 1024
_CHUNK = 64 * 1024


def _decoded_chunks(resp: httpx.Response):
    """Yield the DECODED body in chunks of at most 64 KiB. gzip/deflate are inflated incrementally with a bounded
    zlib decompressor (httpx's own decoder inflates a whole network chunk at once, which a bomb abuses)."""
    if getattr(resp, "is_stream_consumed", False):  # body already materialised by the transport (in-memory test doubles)
        body = resp.content
        for i in range(0, len(body), _CHUNK):
            yield body[i:i + _CHUNK]
        return
    enc = (resp.headers.get("content-encoding") or "identity").strip().lower()
    if enc in ("identity", ""):
        yield from resp.iter_raw(_CHUNK)
        return
    if enc not in ("gzip", "x-gzip", "deflate"):
        raise FetchError(f"unsupported content-encoding {enc!r}")
    d = zlib.decompressobj(zlib.MAX_WBITS | 32)  # auto-detects gzip and zlib framing
    try:
        for raw in resp.iter_raw():
            data = raw
            while data:
                out = d.decompress(data, _CHUNK)
                if out:
                    yield out
                data = d.unconsumed_tail
    except zlib.error as e:
        raise FetchError(f"undecodable {enc} body: {e}") from e


class Fetcher:
    def __init__(self, client: httpx.Client | None = None, min_delay: float | None = None,
                 max_retries: int | None = None, sleep=time.sleep, allowed_hosts: set[str] | None = None,
                 respect_robots: bool = True):
        # follow_redirects stays OFF: redirects are re-validated hop by hop in get()
        self.client = client or httpx.Client(
            headers={"User-Agent": config.USER_AGENT, "Accept": "text/html,application/json"},
            timeout=config.HTTP_TIMEOUT_S, follow_redirects=False)
        self.min_delay = config.MIN_REQUEST_DELAY_S if min_delay is None else min_delay
        self.max_retries = config.MAX_RETRIES if max_retries is None else max_retries
        self._sleep = sleep
        self.allowed_hosts = allowed_hosts
        # robots.txt governs crawlers. Official, authenticated APIs are governed by their API agreement instead.
        self.respect_robots = respect_robots
        self._robots: dict[str, Robots | None] = {}
        self._robots_sha: dict[str, str | None] = {}
        self._robots_unreliable: dict[str, bool] = {}
        self._crawl_delay: dict[str, float] = {}
        self._last: dict[str, float] = {}

    # -- safety checks -------------------------------------------------
    def _check(self, url: str) -> None:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise FetchDisallowed(f"unsupported URL scheme/host: {url!r}")
        host = p.hostname.lower().rstrip(".")  # 'localhost.' is localhost
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".internal"):
            raise FetchDisallowed(f"internal host refused: {host}")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
            if re.fullmatch(r"[0-9a-fx.]+", host):  # numeric shorthand the OS resolver accepts: 2130706433, 0x7f.1, 127.1, 0177.0.0.1
                try:
                    ip = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
                except OSError:
                    ip = None
        if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                               or ip.is_multicast or ip.is_unspecified):
            raise FetchDisallowed(f"non-public IP refused: {host}")
        if not _host_ok(host, self.allowed_hosts):
            raise FetchDisallowed(f"host not in this source's allowlist: {host}")
        if self.respect_robots and not self.allowed(url):
            raise FetchDisallowed(f"robots.txt disallows {url}")

    def _robots_for(self, url: str):
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            r = None
            try:
                for attempt in range(3):  # a transient 5xx must not be mistaken for "disallow everything"
                    r = self._send("GET", f"{origin}/robots.txt", limit=MAX_ROBOTS_BYTES, truncate=True)
                    if r.status_code < 500:
                        break
                    self._sleep(2.0 * (attempt + 1))
            except httpx.HTTPError as e:
                raise FetchError(f"robots.txt unreachable for {origin}: {e}") from e
            self._robots_unreliable[origin] = False
            if r is None:  # every attempt raised: the caller treats an unreadable robots.txt as a refusal, not a crash
                raise FetchError(f"robots.txt unreadable for {origin}")
            if r.status_code == 200:
                rb = Robots(r.text)
                self._robots[origin] = rb
                self._robots_sha[origin] = hashlib.sha256(r.text.encode()).hexdigest()
                cd = rb.crawl_delay(config.USER_AGENT)
                if cd:
                    self._crawl_delay[p.netloc] = float(cd)  # honour the site's own request pacing
            elif r.status_code in (404, 410):
                self._robots[origin] = None  # no robots.txt => no published restrictions
                self._robots_sha[origin] = None
            else:  # 401/403 or persistent 5xx/redirect: conservative disallow-all, flagged as unreliable (not a policy decision)
                self._robots[origin] = Robots("User-agent: *\nDisallow: /")
                self._robots_sha[origin] = None
                self._robots_unreliable[origin] = True
        return self._robots[origin]

    def robots_unreliable(self, url: str) -> bool:
        """True when robots.txt could not be read properly (auth error / persistent 5xx): not evidence of a prohibition."""
        self._robots_for(url)
        p = urlparse(url)
        return self._robots_unreliable.get(f"{p.scheme}://{p.netloc}", False)

    def robots_info(self, url: str) -> tuple[str | None, float | None]:
        """(sha256 of robots.txt or None if absent, Crawl-delay seconds or None). Fetches robots.txt if not cached."""
        self._robots_for(url)
        p = urlparse(url)
        return self._robots_sha.get(f"{p.scheme}://{p.netloc}"), self._crawl_delay.get(p.netloc)

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(config.USER_AGENT, url)

    # -- bounded reads -----------------------------------------------
    def _send(self, method: str, url: str, headers: dict | None = None, data: dict | None = None,
              auth: tuple[str, str] | None = None, limit: int | None = None, truncate: bool = False) -> httpx.Response:
        """One request whose DECODED body is counted while it streams in: a gzip bomb is cut off at `limit` instead of being
        inflated into memory first. Over the limit: FetchError, or (truncate=True, robots.txt) keep the first `limit` bytes."""
        limit = MAX_BYTES if limit is None else limit
        hdrs = {"Accept-Encoding": "gzip, deflate", **(headers or {})}  # only encodings we decode with a bounded decompressor
        with self.client.stream(method, url, headers=hdrs, data=data, auth=auth) as resp:
            buf, size, over = bytearray(), 0, False
            if resp.status_code == 200:
                for chunk in _decoded_chunks(resp):
                    size += len(chunk)
                    if size > limit:
                        over = True
                        if truncate:
                            buf += chunk[: max(0, limit - (size - len(chunk)))]
                        break
                    buf += chunk
            if over and not truncate:
                raise FetchError(f"response larger than {limit} bytes: {url}")
            headers_out = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
            return httpx.Response(resp.status_code, headers=headers_out, content=bytes(buf), request=resp.request)

    # -- request with retry ------------------------------------------
    def _request(self, url: str, method: str = "GET", headers: dict | None = None, data: dict | None = None,
                 auth: tuple[str, str] | None = None) -> httpx.Response:
        host = urlparse(url).netloc
        delay, err = 0.0, "unknown"
        for attempt in range(self.max_retries + 1):
            spacing = max(self.min_delay, self._crawl_delay.get(host, 0.0))
            wait = spacing - (time.monotonic() - self._last.get(host, 0)) + delay
            if wait > 0:
                self._sleep(wait)
            self._last[host] = time.monotonic()
            try:
                r = self._send(method, url, headers=headers, data=data, auth=auth)
            except httpx.HTTPError as e:
                err = f"network error: {type(e).__name__}"  # never echo request details (may contain credentials)
            else:
                if r.status_code in (200, 304) or r.status_code in _REDIRECT:
                    return r
                if r.status_code in (401, 403, 451):
                    raise FetchBlocked(f"{r.status_code} from {host}; not retrying or bypassing")
                if r.status_code in (404, 410):
                    raise FetchGone(f"{r.status_code} for {url}")
                err = f"HTTP {r.status_code}"
                if r.status_code == 429 and r.headers.get("retry-after", "").isdigit():
                    delay = min(float(r.headers["retry-after"]), 60)
            delay = max(delay, 2.0 ** (attempt + 1))
        raise FetchError(f"{err} after {self.max_retries + 1} attempts: {url}")

    def post_form(self, url: str, data: dict, auth: tuple[str, str] | None = None) -> dict:
        """POST application/x-www-form-urlencoded and return parsed JSON (OAuth token endpoints). No redirects."""
        self._check(url)
        r = self._request(url, "POST", data=data, auth=auth)
        if r.status_code != 200:
            raise FetchBlocked(f"POST {urlparse(url).netloc} answered {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise FetchError("token endpoint returned non-JSON") from e

    def get_ex(self, url: str, headers: dict | None = None, etag: str | None = None,
               last_modified: str | None = None) -> FetchResult:
        """GET with optional conditional headers. Returns status 304 (text None) when unchanged."""
        hdrs = dict(headers or {})
        if etag:
            hdrs["If-None-Match"] = etag
        if last_modified:
            hdrs["If-Modified-Since"] = last_modified
        for _ in range(MAX_REDIRECTS + 1):
            self._check(url)
            r = self._request(url, headers=hdrs or None)
            if r.status_code == 304:
                return FetchResult(304, None, etag, last_modified, 0)
            if r.status_code == 200:
                ctype = r.headers.get("content-type", "text/html").lower()
                if not ctype.startswith(_OK_TYPES):
                    raise FetchError(f"unexpected content-type {ctype!r} for {url}")
                if len(r.content) > MAX_BYTES:
                    raise FetchError(f"response larger than {MAX_BYTES} bytes: {url}")
                return FetchResult(200, r.text, r.headers.get("etag"), r.headers.get("last-modified"), len(r.content))
            loc = r.headers.get("location")
            if not loc:
                raise FetchError(f"redirect without Location from {url}")
            new = urljoin(url, loc)
            a, b = urlparse(url), urlparse(new)
            if a.scheme == "https" and b.scheme != "https":
                raise FetchDisallowed(f"refusing https -> {b.scheme} downgrade redirect from {a.netloc}")
            if a.netloc.lower() != b.netloc.lower():  # credentials belong to the host they were issued for
                hdrs = {k: v for k, v in hdrs.items() if k.lower() not in ("authorization", "cookie", "proxy-authorization")}
            url = new
        raise FetchError("too many redirects")

    def get(self, url: str, headers: dict | None = None) -> str:
        return self.get_ex(url, headers).text or ""
