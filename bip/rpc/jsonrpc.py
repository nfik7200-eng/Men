"""JSON-RPC 2.0 request building and strict response validation.

Responses are untrusted. :func:`parse_response` accepts only a JSON object with
``"jsonrpc": "2.0"``, the matching ``id`` and exactly one of ``result``/``error``.
Errors are classified so the router can decide whether to retry, fail over or stop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, cast

from bip.core.exceptions import BIPError

ErrorClass = Literal[
    "rate_limited",  # back off / try another provider
    "transient",  # retry with backoff
    "method_unsupported",  # provider lacks the method: never retry on it
    "range_too_large",  # eth_getLogs window too wide: shrink and retry
    "not_available",  # data not served (e.g. pruned slot): not a provider failure
    "invalid_request",  # our parameters are wrong: do not retry anywhere
    "unauthorized",  # credentials missing/invalid: provider misconfigured
    "malformed",  # response violated JSON-RPC: count as provider failure
]

_RANGE_HINTS = (
    "range",
    "block range",
    "too many",
    "limit exceeded",
    "10000 results",
    "query returned more",
    "specify an address",  # PublicNode: address-less queries only over a single block
)
_RATE_HINTS = ("rate limit", "too many requests", "exceeded the capacity", "quota")


class RpcError(BIPError):
    def __init__(self, error_class: ErrorClass, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.code = code
        self.provider_id: str | None = None
        """The provider that returned this error, set by the router when it re-raises."""

    @property
    def counts_against_provider(self) -> bool:
        return self.error_class in {"rate_limited", "transient", "malformed", "unauthorized"}


@dataclass(frozen=True, slots=True)
class RpcRequest:
    id: int
    method: str
    params: list[object] | dict[str, object]

    def encode(self) -> bytes:
        return json.dumps(
            {"jsonrpc": "2.0", "id": self.id, "method": self.method, "params": self.params},
            separators=(",", ":"),
        ).encode()


def classify_error(code: int | None, message: str, method: str) -> ErrorClass:
    text = message.lower()
    if code == -32601:
        return "method_unsupported"
    if code in {-32005, -32429} or any(h in text for h in _RATE_HINTS):
        if method == "eth_getLogs" and any(h in text for h in _RANGE_HINTS) and "rate" not in text:
            return "range_too_large"
        return "rate_limited"
    if method == "eth_getLogs" and any(h in text for h in _RANGE_HINTS):
        return "range_too_large"
    if code == -32001 and not method.startswith("eth_"):
        return "not_available"  # Solana: block cleaned up (EVM uses -32001 for other errors)
    if code in {-32004, -32007, -32009, -32011, -32015, -32016}:
        # Solana: block not available / slot skipped / missing in long-term storage /
        # tx history not available / unsupported version / min context slot not reached
        return "not_available"
    if code in {-32602, -32600}:
        return "invalid_request"
    return "transient"


def parse_response(raw: bytes, request: RpcRequest) -> object:
    """Return ``result`` or raise :class:`RpcError`. ``None`` results are returned as-is."""
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RpcError("malformed", "response is not valid JSON") from None
    if not isinstance(payload, dict):
        raise RpcError("malformed", "response is not a JSON object (batch replies are not used)")
    payload = cast("dict[str, object]", payload)
    if payload.get("jsonrpc") != "2.0":
        raise RpcError("malformed", "missing jsonrpc 2.0 marker")
    if payload.get("id") != request.id:
        raise RpcError("malformed", "response id does not match the request")
    has_result, has_error = "result" in payload, "error" in payload
    if has_result == has_error:
        raise RpcError("malformed", "response must contain exactly one of result/error")
    if has_error:
        err_raw = payload["error"]
        if not isinstance(err_raw, dict):
            raise RpcError("malformed", "error member is not an object")
        err = cast("dict[str, object]", err_raw)
        code = err.get("code")
        message = err.get("message")
        code_int = code if isinstance(code, int) and not isinstance(code, bool) else None
        text = message[:300] if isinstance(message, str) else "provider returned an error"
        raise RpcError(classify_error(code_int, text, request.method), text, code_int)
    return payload["result"]


def classify_http_status(status: int) -> ErrorClass | None:
    if 200 <= status < 300:
        return None
    if status == 429:
        return "rate_limited"
    if status in {401, 403}:
        return "unauthorized"
    if status >= 500:
        return "transient"
    return "invalid_request"
