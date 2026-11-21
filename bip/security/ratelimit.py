"""A small in-memory sliding-window rate limiter with bounded memory.

One process, one worker: that is the deployment BIP targets, so no shared store is needed. The
number of tracked keys is capped (oldest evicted first), so an attacker cycling addresses cannot
grow memory without bound.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        *,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def hit(self, key: str) -> tuple[bool, float]:
        """Record a request. Returns ``(allowed, retry_after_seconds)``."""
        if self._limit <= 0:
            return True, 0.0
        now = self._clock()
        bucket = self._hits.get(key)
        if bucket is None:
            bucket = self._hits[key] = deque()
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(key)
        cutoff = now - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return False, max(0.0, bucket[0] + self._window - now)
        bucket.append(now)
        return True, 0.0

    @property
    def tracked_keys(self) -> int:
        return len(self._hits)
