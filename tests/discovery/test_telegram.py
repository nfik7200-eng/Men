"""TelegramChannel: request shape, response validation, retry mapping and token handling."""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from bip.core.exceptions import ConfigurationError
from bip.discovery.notify import AlertMessage, DeliveryOutcome, format_alert_text
from bip.discovery.telegram import TELEGRAM_HOST, TelegramChannel
from bip.providers.registry import ResolvedEndpoint
from bip.rpc.transport import TransportError, TransportResponse

TOKEN = SecretStr("123456:" + "A" * 35)
MSG = AlertMessage(alert_key="1:7", severity="high", title="Large transfer", signal_id=7)
HEADER = "BIP alert — statistical signal (not a verified observation)"


class RecordingTransport:
    def __init__(self, response: TransportResponse | Exception) -> None:
        self.response = response
        self.calls: list[tuple[ResolvedEndpoint, bytes]] = []
        self.kwargs: list[dict[str, object]] = []

    def post_json(
        self, endpoint: ResolvedEndpoint, body: bytes, *, timeout_s: float, max_bytes: int
    ) -> TransportResponse:
        self.calls.append((endpoint, body))
        self.kwargs.append({"timeout_s": timeout_s, "max_bytes": max_bytes})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def respond(status: int, body: object) -> RecordingTransport:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return RecordingTransport(TransportResponse(status, raw, 5))


def send(t: RecordingTransport, message: AlertMessage = MSG) -> DeliveryOutcome:
    return TelegramChannel(TOKEN, "-1001", t).send(message)


def test_sends_plain_text_to_send_message() -> None:
    t = respond(200, {"ok": True, "result": {}})
    out = send(t)
    assert out == DeliveryOutcome("delivered")
    ep, body = t.calls[0]
    assert ep.host == TELEGRAM_HOST == "api.telegram.org"
    assert ep.url.get_secret_value() == (
        f"https://api.telegram.org/bot{TOKEN.get_secret_value()}/sendMessage"
    )
    payload = json.loads(body)
    assert payload == {"chat_id": "-1001", "text": format_alert_text(MSG)}  # no parse_mode
    assert payload["text"].startswith(HEADER)
    assert t.kwargs[0] == {"timeout_s": 10.0, "max_bytes": 64 * 1024}


def test_format_lists_header_severity_title_and_signal() -> None:
    assert format_alert_text(MSG).splitlines() == [
        HEADER,
        "Severity: high",
        "Large transfer",
        "Signal #7",
    ]


def test_text_truncated_to_4096() -> None:
    long = AlertMessage(alert_key="k", severity="low", title="x" * 10_000, signal_id=1)
    text = format_alert_text(long)
    assert len(text) == 4096
    assert text.startswith(HEADER)


def test_truncation_counts_utf16_units() -> None:
    # Telegram counts characters as UTF-16 code units; an emoji is two of them
    long = AlertMessage(alert_key="k", severity="low", title="\U0001f40b" * 5000, signal_id=1)
    text = format_alert_text(long)
    assert len(text.encode("utf-16-le")) // 2 <= 4096


def test_ok_false_is_not_delivered() -> None:
    out = send(respond(200, {"ok": False, "description": "x"}))
    assert out.status == "permanent"


def test_2xx_without_ok_true_is_not_delivered() -> None:
    assert send(respond(200, {"result": {}})).status == "permanent"


def test_429_honours_retry_after() -> None:
    out = send(respond(429, {"ok": False, "parameters": {"retry_after": 17}}))
    assert (out.status, out.retry_after_s) == ("retry", 17.0)


def test_429_without_retry_after() -> None:
    out = send(respond(429, {"ok": False}))
    assert (out.status, out.retry_after_s) == ("retry", None)


def test_429_with_non_numeric_retry_after_is_ignored() -> None:
    out = send(respond(429, {"ok": False, "parameters": {"retry_after": "soon"}}))
    assert (out.status, out.retry_after_s) == ("retry", None)


def test_5xx_and_transport_error_retry() -> None:
    assert send(respond(502, b"bad gateway")).status == "retry"
    out = send(RecordingTransport(TransportError("timeout", TELEGRAM_HOST, "request timed out")))
    assert out.status == "retry"
    assert out.error is not None
    assert "timeout" in out.error


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_4xx_permanent(status: int) -> None:
    out = send(respond(status, {"ok": False, "description": "Unauthorized"}))
    assert out.status == "permanent"
    assert out.error is not None
    assert str(status) in out.error


def test_non_json_body_retries() -> None:
    out = send(respond(200, b"<html>"))
    assert out.status == "retry"
    assert out.error is not None
    assert "invalid JSON" in out.error


def test_token_with_slash_is_refused() -> None:
    t = respond(200, {"ok": True})
    with pytest.raises(ConfigurationError) as e:
        TelegramChannel(SecretStr("123456:abc/../x" + "A" * 30), "1", t)
    assert "abc" not in str(e.value)
    assert t.calls == []


@pytest.mark.parametrize("chat_id", ["1/2", "", "@ab", "12?x=1", "@bip alerts"])
def test_chat_id_pattern_refuses(chat_id: str) -> None:
    with pytest.raises(ConfigurationError) as e:
        TelegramChannel(TOKEN, chat_id, respond(200, {"ok": True}))
    assert not chat_id or chat_id not in str(e.value)


@pytest.mark.parametrize("chat_id", ["-1001", "42", "@bip_alerts"])
def test_chat_id_pattern_accepts(chat_id: str) -> None:
    TelegramChannel(TOKEN, chat_id, respond(200, {"ok": True}))


def test_errors_never_contain_the_token() -> None:
    raw = TOKEN.get_secret_value()
    out = send(respond(400, {"ok": False, "description": f"bad token {raw} in /bot{raw}/x"}))
    assert out.status == "permanent"
    assert out.error is not None
    assert raw not in out.error
    assert "AAAAAAAAAA" not in out.error
    assert len(out.error) <= 200


def test_errors_are_capped_at_200_characters() -> None:
    out = send(respond(400, {"ok": False, "description": "y" * 5000}))
    assert out.error is not None
    assert len(out.error) <= 200
