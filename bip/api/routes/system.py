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
