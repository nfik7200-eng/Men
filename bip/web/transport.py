"""Adapter: the acquisition fetcher's ``Transport`` over the hardened HTTPS transport.

The fetcher brings robots.txt, per-domain rate limits and conditional GET; the hardened
transport brings https-only, pinned public addresses, no redirects, and size and time caps.
"""

from __future__ import annotations

from bip.acquisition.fetcher import RawResponse
from bip.rpc.transport import HttpsTransport


class HardenedFetchTransport:
    def __init__(self, transport: HttpsTransport, *, max_bytes: int) -> None:
        self._transport = transport
        self._max = max_bytes

    def get(self, url: str, headers: dict[str, str], timeout: float) -> RawResponse:
        """Raises ``TransportError`` on any refused or failed request (the fetcher reports it)."""
        r = self._transport.get(url, headers, timeout_s=timeout, max_bytes=self._max)
        return RawResponse(r.status, dict(r.headers), r.body)
