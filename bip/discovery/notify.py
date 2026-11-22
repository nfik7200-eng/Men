"""Notification channels.

The in-app channel runs inside the discovery pass: the alert row is the notification. External
channels (:class:`ExternalChannel`, for example Telegram) never run there. Discovery queues an
outbox row, and the alert dispatcher sends it later, so a slow or unreachable channel cannot stall
a pass. :func:`unavailable_channels` states what is not delivered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ALERT_HEADER = "BIP alert — statistical signal (not a verified observation)"
MAX_TEXT_UNITS = 4096
"""Telegram sendMessage text limit, 1-4096 characters (docs/PROVIDER_REGISTRY.md, Telegram)."""


@dataclass(frozen=True, slots=True)
class AlertMessage:
    alert_key: str
    severity: str
    title: str
    signal_id: int


class NotificationChannel(Protocol):
    name: str

    def deliver(self, message: AlertMessage) -> str:
        """Deliver ``message`` and return a delivery status such as ``delivered_in_app``."""
        ...


class InAppChannel:
    """The alert row is the notification: it is listed and marked read through the API."""

    name = "in_app"

    def deliver(self, message: AlertMessage) -> str:  # the alert row itself is the delivery
        return "delivered_in_app"


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Result of one external send. ``retry`` may carry the channel's requested wait."""

    status: Literal["delivered", "retry", "permanent"]
    error: str | None = None
    retry_after_s: float | None = None


class ExternalChannel(Protocol):
    name: str

    def send(self, message: AlertMessage) -> DeliveryOutcome:
        """Send ``message`` over the network. Never raises for a delivery failure."""
        ...


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def format_alert_text(message: AlertMessage) -> str:
    """Plain text for an external channel, labelled as a statistical signal (CLAUDE.md §2.12).

    Cut to :data:`MAX_TEXT_UNITS` UTF-16 code units, which is how Telegram counts characters.
    """
    text = "\n".join(
        [
            ALERT_HEADER,
            f"Severity: {message.severity}",
            message.title,
            f"Signal #{message.signal_id}",
        ]
    )
    if _utf16_units(text) <= MAX_TEXT_UNITS:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        width = 2 if ord(ch) > 0xFFFF else 1
        if used + width > MAX_TEXT_UNITS:
            break
        out.append(ch)
        used += width
    return "".join(out)


def unavailable_channels() -> dict[str, str]:
    return {
        "email": "not configured: needs an approved mail service",
        "push": "not configured: needs an approved push service",
        "sms": "not configured: paid service, needs owner approval",
    }
