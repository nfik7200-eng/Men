"""Plan limits. Prices are HYPOTHESES to validate with real users; no billing is implemented."""
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from watchtower.models import WatchItem


@dataclass(frozen=True)
class Plan:
    key: str
    watch_limit: int
    history_days: int
    alert_interval_hours: int
    price_hypothesis_usd_month: tuple[int, int] | None


PLANS = {
    "free": Plan("free", 5, 30, 24, None),
    "pro": Plan("pro", 50, 365, 6, (5, 10)),
    "advanced": Plan("advanced", 250, 1095, 1, (15, 25)),
}


def plan_for(owner: str) -> Plan:
    return PLANS["free"]  # accounts/billing are P1/P2: everyone is on the free plan today


def can_add_watch(s: Session, owner: str) -> tuple[bool, str]:
    plan = plan_for(owner)
    n = s.scalar(select(func.count()).select_from(WatchItem).where(WatchItem.owner == owner)) or 0
    if n >= plan.watch_limit:
        return False, f"The {plan.key} plan allows {plan.watch_limit} watched items. Upgrade (not yet available) to watch more."
    return True, ""
