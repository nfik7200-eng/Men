"""Polite HTTP fetching: robots.txt, per-host delay, retry/backoff. Never bypasses blocks."""
import time
import urllib.robotparser
from urllib.parse import urlparse

import httpx

from . import config


class FetchBlocked(Exception):
    """robots.txt disallows, or the server answered 401/403/451. We stop; we never work around it."""


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, client: httpx.Client | None = None, min_delay: float | None = None,
                 max_retries: int | None = None, sleep=time.sleep):
        self.client = client or httpx.Client(
            headers={"User-Agent": config.USER_AGENT, "Accept": "text/html,application/json"},
            timeout=config.HTTP_TIMEOUT_S, follow_redirects=True)
        self.min_delay = config.MIN_REQUEST_DELAY_S if min_delay is None else min_delay
        self.max_retries = config.MAX_RETRIES if max_retries is None else max_retries
        self._sleep = sleep
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}

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
                else:  # 401/403/5xx: be conservative, treat as disallow-all
                    rp.parse(["User-agent: *", "Disallow: /"])
                    self._robots[origin] = rp
            except httpx.HTTPError as e:
                raise FetchError(f"robots.txt unreachable for {origin}: {e}") from e
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(config.USER_AGENT, url)

    def get(self, url: str) -> str:
        if not self.allowed(url):
            raise FetchBlocked(f"robots.txt disallows {url}")
        host = urlparse(url).netloc
        delay = 0.0
        for attempt in range(self.max_retries + 1):
            wait = self.min_delay - (time.monotonic() - self._last.get(host, 0)) + delay
            if wait > 0:
                self._sleep(wait)
            self._last[host] = time.monotonic()
            try:
                r = self.client.get(url)
            except httpx.HTTPError as e:
                err = f"network error: {e}"
            else:
                if r.status_code == 200:
                    return r.text
                if r.status_code in (401, 403, 451):
                    raise FetchBlocked(f"{r.status_code} from {host}; not retrying or bypassing")
                if r.status_code == 404:
                    raise FetchError(f"404 for {url}")
                err = f"HTTP {r.status_code}"
                if r.status_code == 429 and r.headers.get("retry-after", "").isdigit():
                    delay = min(float(r.headers["retry-after"]), 60)
            delay = max(delay, 2.0 ** (attempt + 1))
        raise FetchError(f"{err} after {self.max_retries + 1} attempts: {url}")
