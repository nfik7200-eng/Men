"""Hardened HTTPS POST transport for JSON-RPC (standard library only).

Second SSRF layer (see :mod:`bip.security.url_validation`):
- **direct mode**: the host is resolved, *every* address is validated with ``check_ip``, and the
  TCP connection goes to that validated address (pinned), with TLS SNI and certificate checks
  against the hostname. DNS rebinding between check and connect is not possible;
- **proxy mode** (``BIP_RPC__USE_ENV_PROXY=true``): requests go through ``HTTPS_PROXY`` via
  CONNECT. The proxy resolves hosts, so pinning is impossible; URL/host validation still applies;
- redirects are never followed (any 3xx is an error);
- response bodies are read in chunks and aborted beyond ``max_response_bytes``;
- connect/read timeouts apply to every request.

Error messages contain the host, never the URL (provider URLs often embed API keys).
"""

from __future__ import annotations

import http.client
import os
import socket
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlsplit

from bip.core.exceptions import BIPError, UnsafeURLError
from bip.providers.registry import ResolvedEndpoint
from bip.security.url_validation import check_ip, validate_url

TransportFailure = Literal[
    "timeout", "connection", "tls", "too_large", "redirect", "unsafe_destination", "proxy"
]


class TransportError(BIPError):
    def __init__(self, kind: TransportFailure, host: str, message: str) -> None:
        super().__init__(f"{kind} ({host}): {message}")
        self.kind = kind
        self.host = host


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: int
    body: bytes
    elapsed_ms: int
    headers: Mapping[str, str] = field(default_factory=dict[str, str])

    @property
    def retry_after_s(self) -> float | None:
        value = self.headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None  # HTTP-date form: treated as unknown


class Transport(Protocol):
    def post_json(
        self, endpoint: ResolvedEndpoint, body: bytes, *, timeout_s: float, max_bytes: int
    ) -> TransportResponse: ...


Resolver = Callable[[str, int], list[str]]


def _system_resolver(host: str, port: int) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection to a pre-validated IP, verifying TLS against the original hostname."""

    def __init__(
        self, host: str, port: int, ip: str, *, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip
        self._ctx = context

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self.host)


class HttpsTransport:
    """Real network transport. One connection per request (simple, bounded)."""

    def __init__(
        self,
        *,
        use_env_proxy: bool = False,
        resolver: Resolver = _system_resolver,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._use_proxy = use_env_proxy
        self._resolver = resolver
        self._env = os.environ if environ is None else environ
        self._ctx = ssl.create_default_context()

    def _connection(self, host: str, port: int, timeout_s: float) -> http.client.HTTPSConnection:
        if self._use_proxy:
            proxy = self._env.get("HTTPS_PROXY") or self._env.get("https_proxy")
            if not proxy:
                raise TransportError("proxy", host, "use_env_proxy is set but HTTPS_PROXY is empty")
            p = urlsplit(proxy)
            if not p.hostname or p.scheme not in {"http", "https"}:
                raise TransportError("proxy", host, "HTTPS_PROXY is not a valid http(s) URL")
            # HTTPSConnection to the proxy address + set_tunnel issues CONNECT, then performs
            # TLS with the *target* host (SNI and certificate checked against `host`).
            conn = http.client.HTTPSConnection(
                p.hostname,
                p.port or (443 if p.scheme == "https" else 80),
                timeout=timeout_s,
                context=self._ctx,
            )
            conn.set_tunnel(host, port)
            return conn
        try:
            validate_url(f"https://{host}:{port}/")
        except UnsafeURLError as exc:
            raise TransportError("unsafe_destination", host, str(exc)) from None
        try:
            addresses = list(dict.fromkeys(self._resolver(host, port)))
        except OSError:
            raise TransportError("connection", host, "DNS resolution failed") from None
        if not addresses:
            raise TransportError("connection", host, "DNS returned no addresses")
        try:
            for address in addresses:  # every answer must be public, not just the first
                check_ip(address)
        except UnsafeURLError as exc:
            raise TransportError("unsafe_destination", host, str(exc)) from None
        return _PinnedHTTPSConnection(
            host, port, addresses[0], timeout=timeout_s, context=self._ctx
        )

    def post_json(
        self, endpoint: ResolvedEndpoint, body: bytes, *, timeout_s: float, max_bytes: int
    ) -> TransportResponse:
        url = endpoint.url.get_secret_value()
        parts = urlsplit(url)
        host = parts.hostname or endpoint.host
        port = parts.port or 443
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        started = time.monotonic()
        conn = self._connection(host, port, timeout_s)
        try:
            conn.request(
                "POST",
                target,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "BIP/0.2 (read-only research client)",
                },
            )
            resp = conn.getresponse()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            if 300 <= resp.status < 400:
                raise TransportError("redirect", host, f"HTTP {resp.status} redirect refused")
            declared = headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                raise TransportError("too_large", host, "response exceeds the size limit")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise TransportError("too_large", host, "response exceeds the size limit")
                chunks.append(chunk)
            return TransportResponse(
                status=resp.status,
                body=b"".join(chunks),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                headers=headers,
            )
        except TransportError:
            raise
        except TimeoutError:
            raise TransportError("timeout", host, "request timed out") from None
        except ssl.SSLError as exc:
            raise TransportError(
                "tls", host, f"TLS error: {exc.reason or 'handshake failed'}"
            ) from None
        except (OSError, http.client.HTTPException) as exc:
            if self._use_proxy and "Tunnel connection failed" in str(exc):
                raise TransportError("proxy", host, "proxy refused the CONNECT tunnel") from None
            raise TransportError("connection", host, type(exc).__name__) from None
        finally:
            conn.close()
