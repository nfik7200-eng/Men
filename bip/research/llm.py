"""LLM provider abstraction (local Ollama first; optional OpenAI-compatible; none by default).

Safety rules:

* **Ollama:** only a loopback URL (``127.0.0.1``, ``::1`` or ``localhost``) is accepted. BIP
  calls ``/api/tags`` (to check the configured model is installed) and ``/api/chat``. It never
  pulls, deletes or modifies models.
* **OpenAI-compatible:** https only. The host passes the SSRF URL check (public addresses
  only), and the key is read from the environment variable named in settings. Nothing is
  called unless the operator configures it; no paid service is required.
* **Payloads:** prompts and outputs are never logged. Usage records keep sizes, timing and
  outcomes only. Outputs are size-capped.
"""

from __future__ import annotations

import http.client
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urlsplit

from bip.config.settings import AISettings
from bip.core.exceptions import BIPError, UnsafeURLError
from bip.security.secrets import register_secret
from bip.security.url_validation import check_ip, validate_url

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class LLMError(BIPError):
    pass


@dataclass(frozen=True, slots=True)
class LLMStatus:
    provider: str
    model: str | None
    available: bool
    reason: str


class LLMProvider(Protocol):
    name: str
    model: str | None

    def status(self) -> LLMStatus: ...

    def generate(self, system: str, user: str, *, timeout_s: float, max_bytes: int) -> str: ...


def _model_names(payload: object) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    models = cast(dict[str, object], payload).get("models")
    out: set[str] = set()
    for m in cast(list[object], models) if isinstance(models, list) else []:
        if isinstance(m, dict):
            name = cast(dict[str, object], m).get("name")
            if isinstance(name, str):
                out.add(name)
    return out


class NoProvider:
    name = "none"
    model: str | None = None

    def status(self) -> LLMStatus:
        return LLMStatus(
            "none",
            None,
            False,
            "No AI provider is configured (BIP_AI__PROVIDER="
            "none). Deterministic analysis and wallet classification still run.",
        )

    def generate(self, system: str, user: str, *, timeout_s: float, max_bytes: int) -> str:
        raise LLMError("no AI provider configured")


def _post(
    host: str,
    port: int,
    path: str,
    body: bytes,
    *,
    https: bool,
    timeout_s: float,
    max_bytes: int,
    headers: Mapping[str, str],
    public: bool = False,
) -> bytes:
    """One bounded request. ``public``: the peer address is re-checked after connecting, so a
    name that resolves to a private/metadata address is refused (DNS-rebinding safe)."""
    conn: http.client.HTTPConnection = (
        http.client.HTTPSConnection(host, port, timeout=timeout_s)
        if https
        else http.client.HTTPConnection(host, port, timeout=timeout_s)
    )
    deadline = time.monotonic() + timeout_s  # total budget, not per socket operation
    try:
        conn.connect()
        if public:
            peer = conn.sock.getpeername()[0] if conn.sock else ""
            try:
                check_ip(str(peer))
            except UnsafeURLError:
                raise LLMError("endpoint resolved to a non-public address (refused)") from None
        conn.request(
            "POST" if body else "GET",
            path,
            body=body or None,
            headers={"Content-Type": "application/json", **headers},
        )
        resp = conn.getresponse()
        if 300 <= resp.status < 400:
            raise LLMError(f"redirect refused (HTTP {resp.status})")
        chunks: list[bytes] = []
        total = 0
        while True:
            if time.monotonic() > deadline:
                raise LLMError("model response took longer than the timeout")
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise LLMError("model response exceeded the size limit")
            chunks.append(chunk)
        data = b"".join(chunks)
        if resp.status >= 400:
            raise LLMError(f"HTTP {resp.status}")
        return data
    except (OSError, http.client.HTTPException, TimeoutError) as exc:
        raise LLMError(f"connection failed ({type(exc).__name__})") from None
    finally:
        conn.close()


class OllamaProvider:
    name = "ollama"

    def __init__(self, cfg: AISettings) -> None:
        parts = urlsplit(cfg.ollama_url)
        if parts.scheme not in {"http", "https"} or (parts.hostname or "") not in LOOPBACK:
            raise LLMError("ollama_url must be a loopback http(s) URL (local Ollama only)")
        self._host = parts.hostname or "127.0.0.1"
        self._port = parts.port or 11434
        self._https = parts.scheme == "https"
        self.model = cfg.ollama_model
        self._timeout = cfg.request_timeout_s

    def status(self) -> LLMStatus:
        if not self.model:
            return LLMStatus(
                self.name,
                None,
                False,
                "BIP_AI__OLLAMA_MODEL is not set. Choose a "
                "model that is already installed; BIP never pulls models.",
            )
        try:
            data = _post(
                self._host,
                self._port,
                "/api/tags",
                b"",
                https=self._https,
                timeout_s=min(self._timeout, 5.0),
                max_bytes=1024 * 1024,
                headers={},
            )
            names = _model_names(json.loads(data))
        except (LLMError, ValueError, AttributeError) as exc:
            return LLMStatus(self.name, self.model, False, f"Ollama not reachable: {exc}")
        if self.model not in names:
            return LLMStatus(
                self.name,
                self.model,
                False,
                f"Model {self.model!r} is not installed in the local Ollama.",
            )
        return LLMStatus(self.name, self.model, True, "Local Ollama model available.")

    def generate(self, system: str, user: str, *, timeout_s: float, max_bytes: int) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode()
        data = _post(
            self._host,
            self._port,
            "/api/chat",
            body,
            https=self._https,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
            headers={},
        )
        try:
            return str(json.loads(data)["message"]["content"])
        except (ValueError, KeyError, TypeError):
            raise LLMError("unexpected Ollama response shape") from None


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, cfg: AISettings, environ: Mapping[str, str] | None = None) -> None:
        env = os.environ if environ is None else environ
        if not cfg.api_base_url:
            raise LLMError("BIP_AI__API_BASE_URL is not set")
        try:
            validated = validate_url(cfg.api_base_url, allowed_schemes={"https"}, resolve=True)
        except UnsafeURLError as exc:
            raise LLMError(f"api_base_url rejected: {exc}") from None
        self._host = validated.host
        self._port = urlsplit(cfg.api_base_url).port or 443
        self._path = urlsplit(cfg.api_base_url).path.rstrip("/") + "/chat/completions"
        self._key = env.get(cfg.api_key_env, "")
        if self._key:
            register_secret(self._key)
        self.model = cfg.api_model

    def status(self) -> LLMStatus:
        if not self.model:
            return LLMStatus(self.name, None, False, "BIP_AI__API_MODEL is not set.")
        if not self._key:
            return LLMStatus(
                self.name,
                self.model,
                False,
                "API key environment variable is not set; no request is made.",
            )
        return LLMStatus(self.name, self.model, True, "Configured (not contacted until used).")

    def generate(self, system: str, user: str, *, timeout_s: float, max_bytes: int) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode()
        data = _post(
            self._host,
            self._port,
            self._path,
            body,
            https=True,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
            headers={"Authorization": f"Bearer {self._key}"},
            public=True,
        )
        try:
            return str(json.loads(data)["choices"][0]["message"]["content"])
        except (ValueError, KeyError, TypeError, IndexError):
            raise LLMError("unexpected API response shape") from None


def build_provider(cfg: AISettings, environ: Mapping[str, str] | None = None) -> LLMProvider:
    try:
        if cfg.provider == "ollama":
            return OllamaProvider(cfg)
        if cfg.provider == "openai_compatible":
            return OpenAICompatibleProvider(cfg, environ)
    except LLMError as exc:
        return _Broken(cfg.provider, str(exc))
    return NoProvider()


class _Broken:
    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.model: str | None = None
        self._reason = reason

    def status(self) -> LLMStatus:
        return LLMStatus(self.name, None, False, f"Misconfigured: {self._reason}")

    def generate(self, system: str, user: str, *, timeout_s: float, max_bytes: int) -> str:
        raise LLMError(self._reason)
