"""Notification channels. In-app only for now: no paid or external service is configured.

A channel turns an alert into a delivery status. Adding e-mail or push later means adding a class
that satisfies :class:`NotificationChannel`; nothing else in the pipeline changes. Until one is
configured and approved, :func:`unavailable_channels` states what is not delivered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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


def unavailable_channels() -> dict[str, str]:
    return {
        "email": "not configured: needs an approved mail service",
        "push": "not configured: needs an approved push service",
        "sms": "not configured: paid service, needs owner approval",
    }
