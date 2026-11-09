"""The scheduler's jobs.

``discovery``, ``maintenance`` and ``backup`` touch only the local database and files.
``watchlist_refresh`` and ``chain_scan`` are the jobs that contact providers. Each is registered
only when ``workers.watchlist_refresh_enabled`` / ``workers.collector_enabled`` is set, goes
through a bounded path (call budget, disk guard, single-run lock), and says so in its
description.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

from bip.core.exceptions import StorageLimitError
from bip.db import schema
from bip.ops.backup import BackupService
from bip.ops.scheduler import Job

if TYPE_CHECKING:
    from bip.api.deps import AppContext

CLOSED_ERROR_RETENTION_DAYS = 180
DISCOVERY_RUNS_KEPT = 200


def build_jobs(ctx: AppContext, backups: BackupService) -> list[Job]:
    w = ctx.settings.workers
    jobs = [
        Job(
            "discovery",
            timedelta(minutes=w.discovery_interval_minutes),
            lambda: _discovery(ctx),
            "Run every detector over stored data. Reads the local database only.",
        ),
        Job(
            "relationships",
            timedelta(minutes=w.discovery_interval_minutes),
            lambda: _relationships(ctx),
            "Rebuild evidence-backed relationships from stored data. Local database only.",
        ),
        Job(
            "maintenance",
            timedelta(hours=w.maintenance_interval_hours),
            lambda: _maintenance(ctx),
            "Apply retention to audit, signals, runs and history; checkpoint the WAL.",
        ),
    ]
    if w.backup_enabled:
        jobs.append(
            Job(
                "backup",
                timedelta(hours=w.backup_interval_hours),
                lambda: _backup(backups, w.backup_keep),
                "Verified SQLite online backup, then keep the newest few.",
            )
        )
    if w.watchlist_refresh_enabled:
        jobs.append(
            Job(
                "watchlist_refresh",
                timedelta(minutes=w.watchlist_refresh_interval_minutes),
                lambda: _refresh(ctx),
                "Re-ingest the least recently refreshed watchlist wallets. CONTACTS PROVIDERS.",
                contacts_providers=True,
            )
        )
    if w.collector_enabled:
        jobs.append(
            Job(
                "chain_scan",
                timedelta(minutes=w.collector_interval_minutes),
                lambda: _chain_scan(ctx),
                "Read the next few finalized Ethereum blocks for contract creations and large "
                "native transfers. CONTACTS PROVIDERS.",
                contacts_providers=True,
            )
        )
    if w.solana_collector_enabled:
        jobs.append(
            Job(
                "solana_scan",
                timedelta(minutes=w.solana_collector_interval_minutes),
                lambda: _solana_scan(ctx),
                "Read the next few finalized Solana slots for new mints, programs, pools and "
                "large SOL transfers. CONTACTS PROVIDERS.",
                contacts_providers=True,
            )
        )
    return jobs


def _discovery(ctx: AppContext) -> str:
    if not ctx.discovery_lock.acquire(blocking=False):
        return "skipped: a discovery pass is already running"
    try:
        result = ctx.discovery.run()
    finally:
        ctx.discovery_lock.release()
    created = sum(d["created"] for d in result["detectors"].values())
    merged = sum(d["merged"] for d in result["detectors"].values())
    return (
        f"{result['status']}: {created} new, {merged} merged, {result['expired']} expired, "
        f"{result['alerts_created']} alerts"
    )


def _relationships(ctx: AppContext) -> str:
    summary = ctx.relationships.rebuild()
    totals = {"created": 0, "updated": 0, "invalidated": 0}
    for per_kind in summary["chains"].values():
        for stats in per_kind.values():
            for key in totals:
                totals[key] += stats[key]
    capped = sum(
        1
        for per_kind in summary["chains"].values()
        for s in per_kind.values()
        if s["row_cap_reached"]
    )
    return (
        f"{totals['created']} new, {totals['updated']} refreshed, {totals['invalidated']} "
        f"invalidated; {capped} scan(s) hit the row cap"
    )


def _maintenance(ctx: AppContext) -> str:
    w = ctx.settings.workers
    now = ctx.discovery.now()
    parts: list[str] = []
    parts.append(f"audit -{ctx.auth.prune_audit()}")
    pruned = ctx.discovery.prune(older_than_days=w.closed_signal_retention_days)
    parts.append(f"signals -{pruned['signals_deleted']}")
    runs, ie, se = schema.discovery_runs, schema.ingestion_errors, schema.sessions
    jr = schema.job_runs
    with ctx.repo.transaction() as conn:
        keep = select(runs.c.id).order_by(runs.c.id.desc()).limit(DISCOVERY_RUNS_KEPT)
        parts.append(
            f"discovery_runs -{conn.execute(delete(runs).where(runs.c.id.not_in(keep))).rowcount}"
        )
        old_errors = conn.execute(
            delete(ie).where(ie.c.occurred_at < now - timedelta(days=CLOSED_ERROR_RETENTION_DAYS))
        ).rowcount
        parts.append(f"ingestion_errors -{old_errors}")
        parts.append(f"sessions -{conn.execute(delete(se).where(se.c.expires_at < now)).rowcount}")
        history = conn.execute(
            delete(jr).where(jr.c.started_at < now - timedelta(days=w.job_history_days))
        ).rowcount
        parts.append(f"job_runs -{history}")
    with ctx.repo.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.exec_driver_sql("PRAGMA optimize")
    parts.append("wal checkpointed")
    return "; ".join(parts)


def _backup(backups: BackupService, keep: int) -> str:
    info = backups.create()
    removed = backups.prune(keep)
    return f"{info.name} ({info.size_bytes} bytes); pruned {len(removed)}"


def _chain_scan(ctx: AppContext) -> str:
    from bip.api.deps import INGEST_RESERVE_BYTES

    try:
        ctx.disk_guard.require(INGEST_RESERVE_BYTES)
    except StorageLimitError as exc:
        return f"skipped: storage guard refused ({exc})"
    if not ctx.ingest_lock.acquire(blocking=False):
        return "skipped: an ingestion run is in progress"
    try:
        m = ctx.collector.run()
    finally:
        ctx.ingest_lock.release()
    return (
        f"{m['status']}: {m['blocks_scanned']} block(s), {m['contract_creations_stored']} "
        f"creation(s), {m['large_transfers_stored']} large transfer(s)"
    )


def _solana_scan(ctx: AppContext) -> str:
    from bip.api.deps import INGEST_RESERVE_BYTES

    try:
        ctx.disk_guard.require(INGEST_RESERVE_BYTES)
    except StorageLimitError as exc:
        return f"skipped: storage guard refused ({exc})"
    if not ctx.ingest_lock.acquire(blocking=False):
        return "skipped: an ingestion run is in progress"
    try:
        m = ctx.solana_collector.run()
    finally:
        ctx.ingest_lock.release()
    return (
        f"{m['status']}: {m['slots_scanned']} slot(s), {m['mints_found']} mint(s), "
        f"{m['programs_found']} program(s), {m['pools_found']} pool(s)"
    )


def _refresh(ctx: AppContext) -> str:
    from bip.api.deps import INGEST_RESERVE_BYTES

    try:
        ctx.disk_guard.require(INGEST_RESERVE_BYTES)
    except StorageLimitError as exc:
        return f"skipped: storage guard refused ({exc})"
    if not ctx.ingest_lock.acquire(blocking=False):
        return "skipped: an ingestion run is in progress"
    try:
        we, ir = schema.watchlist_entries, schema.ingestion_runs
        last = (
            select(ir.c.chain, ir.c.address, func.max(ir.c.started_at).label("last"))
            .group_by(ir.c.chain, ir.c.address)
            .subquery()
        )
        with ctx.repo.transaction() as conn:
            targets = conn.execute(
                select(we.c.chain, we.c.address)
                .distinct()
                .outerjoin(last, (last.c.chain == we.c.chain) & (last.c.address == we.c.address))
                .order_by(last.c.last.asc().nulls_first())
                .limit(ctx.settings.workers.watchlist_refresh_wallets_per_tick)
            ).all()
        outcomes = [
            str(ctx.ingestor.store.get_run(ctx.ingestor.run(str(c), str(a), None))["status"])  # type: ignore[index]
            for c, a in targets
        ]
    finally:
        ctx.ingest_lock.release()
    return f"{len(outcomes)} wallet(s): {', '.join(outcomes) or 'none on any watchlist'}"
