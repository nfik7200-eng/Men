"""Polite, SSRF-safe HTTP fetching: host allowlist, robots.txt on every hop, per-host delay, retry/backoff.
Never bypasses blocks; never follows a redirect to a host it was not allowed to start from."""
import ipaddress
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import httpx

from . import config

MAX_REDIRECTS = 4
_REDIRECT = (301, 302, 303, 307, 308)


class FetchBlocked(Exception):
    """robots.txt / allowlist / server refusal (401/403/451). We stop; we never work around it."""


class FetchError(Exception):
    pass


def _host_ok(host: str, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    return any(host == a or host.endswith("." + a) for a in allowed if a)


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
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}

    # -- safety checks -------------------------------------------------
    def _check(self, url: str) -> None:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise FetchBlocked(f"unsupported URL scheme/host: {url!r}")
        host = p.hostname.lower()
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".internal"):
            raise FetchBlocked(f"internal host refused: {host}")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                               or ip.is_multicast or ip.is_unspecified):
            raise FetchBlocked(f"non-public IP refused: {host}")
        if not _host_ok(host, self.allowed_hosts):
            raise FetchBlocked(f"host not in this source's allowlist: {host}")
        if self.respect_robots and not self.allowed(url):
            raise FetchBlocked(f"robots.txt disallows {url}")

    def _robots_for(self, url: str):
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(f"{origin}/robots.txt")
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._robots[origin] = rp
                elif r.status_code in (404, 410):
                    self._robots[origin] = None  # no robots.txt => no published restrictions
                else:  # 401/403/5xx/redirect: be conservative, treat as disallow-all
                    rp.parse(["User-agent: *", "Disallow: /"])
                    self._robots[origin] = rp
            except httpx.HTTPError as e:
                raise FetchError(f"robots.txt unreachable for {origin}: {e}") from e
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(config.USER_AGENT, url)

    # -- request with retry ------------------------------------------
    def _request(self, url: str, method: str = "GET", headers: dict | None = None, data: dict | None = None,
                 auth: tuple[str, str] | None = None) -> httpx.Response:
        host = urlparse(url).netloc
        delay, err = 0.0, "unknown"
        for attempt in range(self.max_retries + 1):
            wait = self.min_delay - (time.monotonic() - self._last.get(host, 0)) + delay
            if wait > 0:
                self._sleep(wait)
            self._last[host] = time.monotonic()
            try:
                r = self.client.request(method, url, headers=headers, data=data, auth=auth)
            except httpx.HTTPError as e:
                err = f"network error: {type(e).__name__}"  # never echo request details (may contain credentials)
            else:
                if r.status_code == 200 or r.status_code in _REDIRECT:
                    return r
                if r.status_code in (401, 403, 451):
                    raise FetchBlocked(f"{r.status_code} from {host}; not retrying or bypassing")
                if r.status_code == 404:
                    raise FetchError(f"404 for {url}")
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

    def get(self, url: str, headers: dict | None = None) -> str:
        for _ in range(MAX_REDIRECTS + 1):
            self._check(url)
            r = self._request(url, headers=headers)
            if r.status_code == 200:
                return r.text
            loc = r.headers.get("location")
            if not loc:
                raise FetchError(f"redirect without Location from {url}")
            url = urljoin(url, loc)
        raise FetchError("too many redirects")
