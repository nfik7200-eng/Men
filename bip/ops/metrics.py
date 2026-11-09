"""In-process API metrics with bounded memory.

Counters are per process and reset on restart; they describe the current run, not history. Paths
are normalised (ids, hashes and addresses become ``{id}``) and the number of distinct routes is
capped, so request traffic cannot grow memory.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_BUCKETS_MS = (50, 200, 1000, 5000)
_ID = re.compile(r"/(?:0x[0-9a-fA-F]{8,}|[1-9A-HJ-NP-Za-km-z]{32,}|[0-9a-f]{16,}|\d+)(?=/|$)")
MAX_ROUTES = 200


def normalise(path: str) -> str:
    return _ID.sub("/{id}", path)


class ApiMetrics:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._started = clock()
        self.requests = 0
        self.by_class: dict[str, int] = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
        self.throttled = 0
        self.unauthorised = 0
        self.latency_ms: dict[str, int] = {f"<{b}ms": 0 for b in _BUCKETS_MS} | {">=5000ms": 0}
        self._routes: dict[str, list[float]] = {}  # key -> [count, total_ms, max_ms, errors]
        self.other_routes = 0

    def record(self, method: str, path: str, status: int, elapsed_ms: float) -> None:
        klass = f"{status // 100}xx"
        key = f"{method} {normalise(path)}"
        with self._lock:
            self.requests += 1
            self.by_class[klass] = self.by_class.get(klass, 0) + 1
            if status == 429:
                self.throttled += 1
            elif status in (401, 403):
                self.unauthorised += 1
            for bound in _BUCKETS_MS:
                if elapsed_ms < bound:
                    self.latency_ms[f"<{bound}ms"] += 1
                    break
            else:
                self.latency_ms[">=5000ms"] += 1
            entry = self._routes.get(key)
            if entry is None:
                if len(self._routes) >= MAX_ROUTES:
                    self.other_routes += 1
                    return
                entry = self._routes[key] = [0, 0.0, 0.0, 0]
            entry[0] += 1
            entry[1] += elapsed_ms
            entry[2] = max(entry[2], elapsed_ms)
            entry[3] += 1 if status >= 500 else 0

    def snapshot(self, top: int = 10) -> dict[str, Any]:
        with self._lock:
            slowest = sorted(self._routes.items(), key=lambda kv: kv[1][2], reverse=True)[:top]
            return {
                "since_start_seconds": round(self._clock() - self._started, 1),
                "requests": self.requests,
                "by_status_class": dict(self.by_class),
                "throttled_429": self.throttled,
                "unauthorised_401_403": self.unauthorised,
                "latency_buckets": dict(self.latency_ms),
                "slowest_routes": [
                    {
                        "route": key,
                        "count": int(v[0]),
                        "mean_ms": round(v[1] / v[0], 1) if v[0] else 0.0,
                        "max_ms": round(v[2], 1),
                        "server_errors": int(v[3]),
                    }
                    for key, v in slowest
                ],
                "routes_not_tracked": self.other_routes,
            }


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, *, metrics: ApiMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        seen: dict[str, int] = {"status": 500}

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                seen["status"] = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            self.metrics.record(
                scope["method"],
                scope.get("path", ""),
                seen["status"],
                (time.perf_counter() - started) * 1000,
            )
