"""Telegram Bot API alert channel over the hardened HTTPS transport.

Only ``sendMessage`` is used, with plain text (no ``parse_mode``). The bot token sits in the URL
path (``/bot<token>/sendMessage``), so it is validated against a strict pattern before any request
can be built, registered for redaction, and never placed in an error, log or API response.

Response handling (``CLAUDE.md`` §2.7: an HTTP 200 is not proof of delivery):
- 2xx with ``"ok": true`` -> delivered;
- 429 -> retry, honouring ``parameters.retry_after`` when it is a number;
- 5xx, transport failure or a body that is not JSON -> retry;
- any other 4xx, or 2xx without ``"ok": true`` -> permanent.

Provider facts (text limit, per-chat rate) are in ``docs/PROVIDER_REGISTRY.md``.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from pydantic import SecretStr

from bip.config.settings import TELEGRAM_CHAT_ID_PATTERN
from bip.core.exceptions import ConfigurationError
from bip.discovery.notify import AlertMessage, DeliveryOutcome, format_alert_text
from bip.providers.registry import ResolvedEndpoint
from bip.rpc.transport import Transport, TransportError
from bip.security.secrets import REDACTED, redact_text, register_secret

TELEGRAM_HOST = "api.telegram.org"
TOKEN_PATTERN = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{30,64}$")
CHAT_ID_PATTERN = re.compile(TELEGRAM_CHAT_ID_PATTERN)
MAX_RESPONSE_BYTES = 64 * 1024
MAX_ERROR_CHARS = 200


class TelegramChannel:
    name = "telegram"

    def __init__(
        self, token: SecretStr, chat_id: str, transport: Transport, *, timeout_s: float = 10.0
    ) -> None:
        raw = token.get_secret_value()
        if not TOKEN_PATTERN.fullmatch(raw):
            raise ConfigurationError("BIP_TELEGRAM_BOT_TOKEN does not look like a bot token")
        if not CHAT_ID_PATTERN.fullmatch(chat_id):
            raise ConfigurationError(
                "telegram_chat_id must be a numeric chat id or an @username (5-32 characters)"
            )
        register_secret(token)
        self._token = token
        self._chat_id = chat_id
        self._transport = transport
        self._timeout_s = timeout_s

    def _endpoint(self) -> ResolvedEndpoint:
        url = f"https://{TELEGRAM_HOST}/bot{self._token.get_secret_value()}/sendMessage"
        return ResolvedEndpoint(url=SecretStr(url), host=TELEGRAM_HOST, from_environment=True)

    def _clean(self, text: str) -> str:
        # the registry redacts the token too; replace it here as well so this channel's
        # guarantee does not depend on global state
        text = text.replace(self._token.get_secret_value(), REDACTED)
        return redact_text(text)[:MAX_ERROR_CHARS]

    def send(self, message: AlertMessage) -> DeliveryOutcome:
        body = json.dumps({"chat_id": self._chat_id, "text": format_alert_text(message)}).encode()
        try:
            resp = self._transport.post_json(
                self._endpoint(), body, timeout_s=self._timeout_s, max_bytes=MAX_RESPONSE_BYTES
            )
        except TransportError as exc:
            return DeliveryOutcome("retry", self._clean(f"transport {exc}"))
        try:
            parsed: object = json.loads(resp.body)
        except (ValueError, UnicodeDecodeError):
            parsed = None
        data = cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None
        description = ""
        if data is not None and isinstance(data.get("description"), str):
            description = f": {data['description']}"
        if resp.status == 429:
            return DeliveryOutcome(
                "retry", self._clean(f"HTTP 429{description}"), _retry_after(data)
            )
        if resp.status >= 500:
            return DeliveryOutcome("retry", self._clean(f"HTTP {resp.status}{description}"))
        if data is None:
            return DeliveryOutcome("retry", f"HTTP {resp.status}: invalid JSON response")
        if 200 <= resp.status < 300:
            if data.get("ok") is True:
                return DeliveryOutcome("delivered")
            return DeliveryOutcome("permanent", self._clean(f"ok flag not true{description}"))
        return DeliveryOutcome("permanent", self._clean(f"HTTP {resp.status}{description}"))


def _retry_after(data: dict[str, Any] | None) -> float | None:
    if data is None:
        return None
    params = data.get("parameters")
    if not isinstance(params, dict):
        return None
    value = cast("dict[str, Any]", params).get("retry_after")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return max(0.0, float(value))
