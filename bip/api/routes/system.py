"""Operational endpoints: detailed health, RPC usage, backups and background jobs.

``/health`` stays public and minimal (is the API up?). Everything here needs a session when
authentication is required, and nothing returns filesystem paths or secrets.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, status

from bip.api.deps import Context, WritableContext
from bip.api.errors import error_response
from bip.ops.backup import BackupError
from bip.ops.health import system_report, usage_report
from bip.providers.state import ProviderStateStore

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def detailed_health(ctx: Context) -> dict[str, Any]:
    report = system_report(ctx, ctx.backups, ctx.started_monotonic)
    return {**report, "data_origin": ctx.repo.get_meta("data_origin") or "stored_observations"}


@router.get("/usage")
def usage(ctx: Context, days: Annotated[int, Query(ge=1, le=90)] = 7) -> dict[str, Any]:
    return usage_report(ctx, days)


@router.get("/backups")
def list_backups(ctx: Context) -> dict[str, Any]:
    return {
        "enabled": ctx.settings.workers.backup_enabled,
        "keep": ctx.settings.workers.backup_keep,
        "items": [b.public() for b in ctx.backups.list()],
    }


@router.post("/backups", status_code=status.HTTP_201_CREATED)
def create_backup(ctx: WritableContext) -> Any:
    try:
        info = ctx.backups.create()
        removed = ctx.backups.prune(ctx.settings.workers.backup_keep)
    except BackupError as exc:
        return error_response(409, "backup_failed", str(exc))
    return {**info.public(), "pruned": removed}


@router.post("/backups/{name}/verify")
def verify_backup(
    ctx: WritableContext, name: Annotated[str, Path(min_length=1, max_length=80)]
) -> Any:
    try:
        return {"name": name, "integrity_ok": ctx.backups.verify(name)}
    except BackupError as exc:
        return error_response(404, "not_found", str(exc))


@router.get("/jobs")
def jobs(ctx: Context) -> dict[str, Any]:
    scheduler = ctx.scheduler
    return {
        "enabled": ctx.settings.workers.enabled,
        "running": bool(scheduler and scheduler.running),
        "jobs": scheduler.status() if scheduler else [],
    }


@router.post("/jobs/{name}/run")
def run_job(ctx: WritableContext, name: Annotated[str, Path(min_length=1, max_length=40)]) -> Any:
    if ctx.scheduler is None:
        return error_response(409, "workers_disabled", "The scheduler is not enabled.")
    try:
        result = ctx.scheduler.run_now(name)
    except KeyError:
        return error_response(404, "not_found", f"no job named {name!r}")
    if result.get("status") == "busy":
        return error_response(409, "job_busy", "Another job is running.")
    return result


@router.get("/budgets")
def budgets(ctx: Context) -> dict[str, Any]:
    """Configured request, run and storage budgets and the circuit-breaker policy (read-only).

    Budgets are per run, plus optional per-provider daily request caps the operator configures.
    Nothing here is a provider's own published limit (those live in the provider registry).
    """
    s = ctx.settings
    return {
        "per_run": {
            "ethereum_collector": {
                "max_rpc_calls": s.collector.max_rpc_calls_per_run,
                "deadline_s": s.collector.run_deadline_s,
                "blocks": s.collector.blocks_per_run,
                "receipts": s.collector.max_receipts_per_run,
            },
            "solana_collector": {
                "max_rpc_calls": s.solana_collector.max_rpc_calls_per_run,
                "deadline_s": s.solana_collector.run_deadline_s,
                "slots": s.solana_collector.blocks_per_run,
            },
            "wallet_ingestion": {
                "max_rpc_calls": s.ingest.max_rpc_calls_per_run,
                "deadline_s": s.ingest.run_deadline_s,
                "max_transactions": s.ingest.max_limit,
            },
            "capability_probe": {
                "max_calls_per_run": s.probe.max_calls_per_run,
                "max_calls_per_provider": s.probe.max_calls_per_provider,
                "max_run_seconds": s.probe.max_run_seconds,
            },
        },
        "retries": {
            "max_attempts": s.rpc.max_attempts,
            "backoff_base_s": s.rpc.backoff_base_s,
            "backoff_cap_s": s.rpc.backoff_cap_s,
            "max_retry_after_s": s.rpc.max_retry_after_s,
            "timeout_s": s.rpc.timeout_s,
        },
        "circuit_breaker": {
            "enabled": s.router.circuit_breakers_enabled,
            "failure_threshold": s.rpc.circuit_failure_threshold,
            "open_s": s.rpc.circuit_open_s,
        },
        "storage": {
            "max_data_bytes": int(s.max_data_bytes),
            "min_free_bytes": int(s.min_free_bytes),
            "min_free_ratio": s.min_free_ratio,
        },
        "scheduled_collection": {
            "scheduler_enabled": s.workers.enabled,
            "ethereum_collector_enabled": s.workers.collector_enabled,
            "solana_collector_enabled": s.workers.solana_collector_enabled,
        },
        "provider_daily": {
            "budgets": dict(s.rpc.daily_request_budgets),
            "requests_today": ProviderStateStore(ctx.repo).usage_today(),
            "note": "BIP's own request counts per UTC day, not the provider's billing units",
        },
        "quota_tracking": "per run, plus BIP's own per-provider daily request counts",
    }
