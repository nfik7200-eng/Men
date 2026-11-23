"""The guarded HTTP client every REST integration uses.

Before any request it checks that the provider has an integration, that the URL's host is on the
provider's registry allow-list, and that today's request count is under the daily cap. Requests
go through the hardened HTTPS transport (https only, pinned public addresses, no redirects, size
and time caps). Every call is counted in the provider's daily usage and health. Keys are read
live from the layered environment (environment, then Setup page), registered for redaction, and
never appear in errors or stored records.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.parse import urlencode, urlsplit

from bip.config.settings import SourcesSettings
from bip.core.exceptions import BIPError
from bip.providers.registry import ProviderSpec, Registry
from bip.providers.state import ProviderStateStore
from bip.rpc.transport import TransportError, TransportResponse
from bip.security.secrets import redact_text, register_secret


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
        *,
        timeout_s: float,
        max_bytes: int,
    ) -> TransportResponse: ...


def as_dict(value: object) -> dict[str, Any]:
    """``value`` as a JSON object, or an empty one (provider answers are untrusted)."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any] | None:
    return cast(list[Any], value) if isinstance(value, list) else None


class SourceError(BIPError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceResponse:
    data: Any
    request_id: str
    fetched_at: datetime
    content_sha256: str
    elapsed_ms: int


class SourceClient:
    def __init__(
        self,
        registry: Registry,
        state: ProviderStateStore,
        transport: Transport,
        environ: Mapping[str, str],
        cfg: SourcesSettings,
    ) -> None:
        self.registry = registry
        self.state = state
        self.transport = transport
        self.environ = environ
        self.cfg = cfg

    def spec(self, provider_id: str) -> ProviderSpec:
        spec = self.registry.get(provider_id)
        if spec.integration is None:
            raise SourceError("no_integration", f"{provider_id} has no integration")
        return spec

    def key(self, provider_id: str) -> str | None:
        spec = self.spec(provider_id)
        if spec.endpoint_env is None:
            return None
        value = self.environ.get(spec.endpoint_env) or None
        if value:
            register_secret(value)
        return value

    def require_key(self, provider_id: str) -> str:
        key = self.key(provider_id)
        if key is None:
            spec = self.spec(provider_id)
            raise SourceError(
                "not_configured", f"{spec.name} needs a key: add it on the Setup page"
            )
        return key

    def get(
        self,
        provider_id: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> SourceResponse:
        return self._call(provider_id, "GET", url, params, headers, None)

    def post_json(
        self,
        provider_id: str,
        url: str,
        body: object,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> SourceResponse:
        raw = json.dumps(body).encode()
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        return self._call(provider_id, "POST", url, None, hdrs, raw)

    def _call(
        self,
        provider_id: str,
        method: str,
        url: str,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
        body: bytes | None,
    ) -> SourceResponse:
        spec = self.spec(provider_id)
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme != "https" or host not in spec.allowed_hosts:
            raise SourceError(
                "host_not_allowed", f"host {host or '?'} is not allowed for {spec.id}"
            )
        used = self.state.usage_today().get(provider_id, 0)
        if used >= self.cfg.daily_request_cap:
            raise SourceError(
                "cap_reached",
                f"{spec.name}: today's cap of {self.cfg.daily_request_cap} requests is reached",
            )
        full = url + ("?" + urlencode(params) if params else "")
        hdrs = {"Accept": "application/json", "User-Agent": "BIP/0.2 (read-only research)"}
        hdrs.update(headers or {})
        started = time.monotonic()
        try:
            resp = self.transport.request(
                method,
                full,
                hdrs,
                body,
                timeout_s=self.cfg.timeout_s,
                max_bytes=self.cfg.max_response_bytes,
            )
        except TransportError as exc:
            self.state.record_call(provider_id, ok=False, latency_ms=None, outcome=exc.kind)
            raise SourceError("transport", redact_text(str(exc))[:200]) from None
        latency = int((time.monotonic() - started) * 1000)
        if not 200 <= resp.status < 300:
            outcome = "rate_limited" if resp.status == 429 else f"http_{resp.status}"
            self.state.record_call(provider_id, ok=False, latency_ms=latency, outcome=outcome)
            raise SourceError("http_error", f"{spec.name} answered HTTP {resp.status}")
        try:
            data = json.loads(resp.body)
        except ValueError:
            self.state.record_call(provider_id, ok=False, latency_ms=latency, outcome="invalid")
            raise SourceError("invalid_response", f"{spec.name} returned non-JSON") from None
        self.state.record_call(provider_id, ok=True, latency_ms=latency, outcome="ok")
        return SourceResponse(
            data=data,
            request_id=uuid.uuid4().hex,
            fetched_at=datetime.now(UTC),
            content_sha256=hashlib.sha256(resp.body).hexdigest(),
            elapsed_ms=latency,
        )
