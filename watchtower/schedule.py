"""Optional, DISABLED-BY-DEFAULT scheduling of live collection. A library + CLI entry point, not a service.

Safety design (mission 005): nothing here installs cron/systemd or starts a daemon. `run_due` does something only when
  1. data/schedule.json says "enabled": true, AND
  2. the environment sets WATCHTOWER_SCHEDULER_ENABLED=1 (a second, deliberate opt-in by whoever operates the host), AND
  3. each source then still goes through `collector.run_source`, i.e. the policy gate (approved + current + robots-compliant
     + real contact), the busy check and the per-run page budget. A refusal is reported as a skip reason, never bypassed.
Only sources whose adapter is a real retailer (kind == "live") can be scheduled; intervals have a floor, budgets a ceiling.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from . import config
from .collector import SourceBusy, SourceDisabled, SourceNotConfigured, run_source
from .db import Database
from .fetcher import FetchError
from .models import CollectionRun, Source, utcnow
from .policy import SourceNotApproved
from .sources import ADAPTERS

MIN_INTERVAL_HOURS = 12


class ScheduleError(ValueError):
    pass


@dataclass
class Result:
    status: str  # disabled | ran
    message: str = ""
    ran: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


def load(path) -> dict:
    cfg = json.loads(Path(path).read_text())
    if not isinstance(cfg.get("sources"), dict):
        raise ScheduleError("schedule config needs a 'sources' object")
    for key, v in cfg["sources"].items():
        a = ADAPTERS.get(key)
        if a is None or a.kind != "live":
            raise ScheduleError(f"{key}: only real-retailer (live) sources may be scheduled")
        if not isinstance(v.get("min_interval_hours"), (int, float)) or v["min_interval_hours"] < MIN_INTERVAL_HOURS:
            raise ScheduleError(f"{key}: min_interval_hours must be at least {MIN_INTERVAL_HOURS}")
        if not isinstance(v.get("max_pages"), int) or not 1 <= v["max_pages"] <= config.MAX_PAGE_BUDGET:
            raise ScheduleError(f"{key}: max_pages must be between 1 and {config.MAX_PAGE_BUDGET}")
    cfg["enabled"] = cfg.get("enabled") is True
    return cfg


def enabled(cfg: dict) -> tuple[bool, str]:
    if not cfg["enabled"]:
        return False, "scheduling is disabled (data/schedule.json: enabled=false); nothing was run"
    if os.getenv("WATCHTOWER_SCHEDULER_ENABLED") != "1":
        return False, "scheduling is disabled: the config is enabled but WATCHTOWER_SCHEDULER_ENABLED=1 is not set; nothing was run"
    return True, ""


def run_due(db: Database, path, now: datetime | None = None, runner=run_source) -> Result:
    cfg = load(path)
    ok, why = enabled(cfg)
    if not ok:
        return Result("disabled", why)
    now = now or utcnow()
    with db.session() as s:
        last = {k: s.scalar(select(func.max(CollectionRun.started_at)).join(Source, Source.id == CollectionRun.source_id).where(Source.key == k))
                for k in cfg["sources"]}
    res = Result("ran")
    # never-run sources first, then the longest-waiting: no merchant is favoured, each gets its own budget
    for key in sorted(cfg["sources"], key=lambda k: (last[k] is not None, last[k] or now, k)):
        v = cfg["sources"][key]
        prev = last[key]
        if prev is not None:
            hours = (now - prev).total_seconds() / 3600
            if hours < v["min_interval_hours"]:
                res.skipped[key] = f"ran {hours:.1f} h ago; minimum interval {v['min_interval_hours']:g} h"
                continue
        try:
            runner(db, key, max_pages=v["max_pages"])
            res.ran.append(key)
        except (SourceNotApproved, SourceNotConfigured, SourceDisabled, SourceBusy, FetchError) as e:
            res.skipped[key] = str(e)[:200]
    return res
