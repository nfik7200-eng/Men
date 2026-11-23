"""A fake HTTPS transport for keyed REST integrations: records requests, never touches a network."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from bip.rpc.transport import TransportResponse


@dataclass
class Call:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    @property
    def query(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlsplit(self.url).query).items()}

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""


Handler = Callable[[Call], tuple[int, object]]


@dataclass
class FakeRestTransport:
    """Routes by ``host + path``; a route returns ``(status, json_body)``."""

    routes: dict[str, Handler] = field(default_factory=dict[str, Handler])
    calls: list[Call] = field(default_factory=list[Call])

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
        *,
        timeout_s: float,
        max_bytes: int,
    ) -> TransportResponse:
        del timeout_s
        call = Call(method, url, dict(headers), body)
        self.calls.append(call)
        handler = self.routes.get(call.host + call.path)
        if handler is None:
            return TransportResponse(404, b'{"error":"not found"}', 1, {})
        status, payload = handler(call)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if len(raw) > max_bytes:
            from bip.rpc.transport import TransportError

            raise TransportError("too_large", call.host, "response exceeds the size limit")
        return TransportResponse(status, raw, 1, {})


def ok(payload: object) -> Handler:
    return lambda _call: (200, payload)
