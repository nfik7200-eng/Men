"""The system health report: what is running, how much room is left, what is failing.

Read-only and cheap. It reports sizes, counts and states, never filesystem paths, endpoints or
secrets. ``warnings`` lists anything an operator should act on; ``status`` is derived from it.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from bip import __version__
from bip.core.exceptions import StorageLimitError
from bip.db import schema
from bip.db.engine import check_connectivity, database_path
from bip.db.types import utcnow
from bip.ops.backup import BackupService

if TYPE_CHECKING:
    from bip.api.deps import AppContext

WAL_WARN_BYTES = 256 * 1024 * 1024
DB_WARN_BYTES = 4 * 1024**3
BACKUP_STALE = timedelta(days=7)
FREE_RATIO_WARN = 0.15


def _process() -> dict[str, Any]:
    rss: int | None = None
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                    break
    except (OSError, ValueError):
        rss = None
    try:
        fds: int | None = len(os.listdir("/proc/self/fd"))
    except OSError:
        fds = None
    return {"rss_bytes": rss, "threads": threading.active_count(), "open_files": fds}


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _file_size(path: Any) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def system_report(
    ctx: AppContext, backups: BackupService, started_monotonic: float
) -> dict[str, Any]:
    now = utcnow()
    warnings: list[dict[str, str]] = []

    def warn(code: str, message: str) -> None:
        warnings.append({"code": code, "message": message})

    # ---- database
    db_ok = check_connectivity(ctx.repo.engine)
    path = database_path(ctx.settings)
    db_bytes = _file_size(path)
    wal_bytes = _file_size(path.with_name(path.name + "-wal"))
    with ctx.repo.transaction() as conn:
        version = conn.execute(select(func.max(schema.schema_migrations.c.version))).scalar_one()
    if not db_ok:
        warn("database_unavailable", "The database does not answer.")
    if wal_bytes > WAL_WARN_BYTES:
        warn("wal_large", f"The WAL is {wal_bytes // 2**20} MB; a checkpoint is overdue.")
    if db_bytes > DB_WARN_BYTES:
        warn("database_large", f"The database is {db_bytes // 2**30} GB; review retention.")

    # ---- disk
    disk: dict[str, Any]
    try:
        check = ctx.disk_guard.check()
        st = check.status
        disk = {
            "writes": "allowed" if check.allowed else "refused",
            "reasons": list(check.reasons),
            "total_bytes": st.total_bytes,
            "free_bytes": st.free_bytes,
            "free_ratio": round(st.free_ratio, 4),
            "bip_used_bytes": st.bip_used_bytes,
            "max_data_bytes": st.max_data_bytes,
            "min_free_bytes": st.min_free_bytes,
        }
        if not check.allowed:
            warn("storage_refusing_writes", "; ".join(check.reasons) or "writes are refused")
        elif st.free_ratio < FREE_RATIO_WARN:
            warn("disk_low", f"Only {st.free_ratio:.0%} of the disk is free.")
    except StorageLimitError as exc:
        disk = {"writes": "unknown", "error": str(exc)}
        warn("storage_unmeasurable", str(exc))

    # ---- ingestion
    runs = ctx.ingestor.store.recent_runs(5)
    day_ago = now - timedelta(hours=24)
    ir, ie = schema.ingestion_runs, schema.ingestion_errors
    with ctx.repo.transaction() as conn:
        failed_24h = int(
            conn.execute(
                select(func.count())
                .select_from(ir)
                .where(ir.c.status == "failed", ir.c.started_at >= day_ago)
            ).scalar_one()
        )
        errors = conn.execute(
            select(ie.c.error_code, func.count().label("n"))
            .where(ie.c.occurred_at >= day_ago)
            .group_by(ie.c.error_code)
            .order_by(func.count().desc())
            .limit(8)
        ).all()
    if failed_24h:
        warn("ingestion_failures", f"{failed_24h} ingestion run(s) failed in the last 24 hours.")

    # ---- discovery and alerts
    last_discovery = ctx.discovery.last_run("live")
    open_signals = ctx.discovery.list_signals(status="open", limit=1)["total"]
    unread = ctx.discovery.list_alerts(unread_only=True, limit=1, offset=0)["unread"]

    # ---- providers
    from bip.api.routes.providers import provider_views, summarize

    summary = summarize(provider_views(ctx))
    tested = summary.healthy + summary.degraded + summary.unavailable
    if tested and summary.healthy == 0 and summary.degraded == 0:
        warn("no_healthy_provider", "Every provider that has been checked is unavailable.")

    # ---- workers
    jobs = ctx.scheduler.status() if ctx.scheduler is not None else []
    for job in jobs:
        if job["consecutive_failures"]:
            warn(
                "job_failing", f"Job {job['job']} has failed {job['consecutive_failures']} time(s)."
            )

    # ---- backups
    latest = backups.list()[:1]
    if ctx.settings.workers.backup_enabled and (
        not latest or now - latest[0].created_at > BACKUP_STALE
    ):
        warn("backup_stale", "The newest backup is missing or older than 7 days.")

    status = (
        "critical"
        if not db_ok or disk.get("writes") == "refused"
        else ("degraded" if warnings else "ok")
    )
    return {
        "status": status,
        "version": __version__,
        "environment": ctx.settings.env.value,
        "time": now.isoformat(),
        "uptime_seconds": round(time.monotonic() - started_monotonic, 1),
        "warnings": warnings,
        "database": {
            "status": "ok" if db_ok else "unavailable",
            "schema_version": version,
            "size_bytes": db_bytes,
            "wal_bytes": wal_bytes,
            "journal_mode": "wal",
            "busy_timeout_ms": ctx.settings.database.busy_timeout_ms,
        },
        "disk": disk,
        "process": _process(),
        "api": ctx.metrics.snapshot(),
        "ingestion": {
            "recent_runs": [
                {
                    "id": r["id"],
                    "chain": r["chain"],
                    "status": r["status"],
                    "started_at": _iso(r["started_at"]),
                    "rpc_calls": r["rpc_calls"],
                    "error_count": r["error_count"],
                }
                for r in runs
            ],
            "failed_runs_24h": failed_24h,
            "errors_24h": [{"code": str(code), "count": int(n)} for code, n in errors],
        },
        "discovery": {
            "last_run": None
            if last_discovery is None
            else {k: last_discovery[k] for k in ("id", "status", "started_at", "finished_at")},
            "open_signals": open_signals,
            "unread_alerts": unread,
        },
        "providers": summary.model_dump(),
        "workers": {
            "enabled": ctx.settings.workers.enabled,
            "running": bool(ctx.scheduler and ctx.scheduler.running),
            "jobs": jobs,
        },
        "backups": {
            "enabled": ctx.settings.workers.backup_enabled,
            "count": len(backups.list()),
            "latest": latest[0].public() if latest else None,
        },
    }


def usage_report(ctx: AppContext, days: int) -> dict[str, Any]:
    """RPC usage per day from the ingestion run log: calls, runs and the providers used.

    This counts what BIP asked, per run. It is not the provider's own meter, so it cannot show
    credits or cost; ``docs/PROVIDER_REGISTRY.md`` lists each provider's units and prices.
    """
    ir = schema.ingestion_runs
    since = utcnow() - timedelta(days=days)
    with ctx.repo.transaction() as conn:
        rows = conn.execute(
            select(ir.c.started_at, ir.c.chain, ir.c.rpc_calls, ir.c.providers_json, ir.c.status)
            .where(ir.c.started_at >= since)
            .order_by(ir.c.started_at)
        ).all()
    import json

    daily: dict[str, dict[str, Any]] = {}
    for r in rows:
        day = daily.setdefault(
            r.started_at.date().isoformat(),
            {"runs": 0, "failed_runs": 0, "rpc_calls": 0, "by_chain": {}, "runs_by_provider": {}},
        )
        day["runs"] += 1
        day["failed_runs"] += 1 if r.status == "failed" else 0
        day["rpc_calls"] += int(r.rpc_calls)
        day["by_chain"][r.chain] = day["by_chain"].get(r.chain, 0) + int(r.rpc_calls)
        for provider in json.loads(r.providers_json or "[]"):
            day["runs_by_provider"][provider] = day["runs_by_provider"].get(provider, 0) + 1
    return {
        "window_days": days,
        "total_rpc_calls": sum(d["rpc_calls"] for d in daily.values()),
        "total_runs": sum(d["runs"] for d in daily.values()),
        "days": [{"date": k, **v} for k, v in sorted(daily.items())],
        "note": (
            "Counts BIP's own requests per ingestion run. Provider credits and prices are not "
            "metered here."
        ),
    }
