"""AI research investigations: preview, start, progress, cancel, rerun, history, report.

Runs execute in a bounded worker pool (≤ 2 concurrent). Every budget is enforced on the
server; requests cannot raise them. Investigations read BIP's own data through typed tools;
the LLM (optional) only synthesizes over curated claims.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from bip.api.deps import Context, WritableContext
from bip.api.errors import error_response
from bip.api.routes.chains import _require_chain  # pyright: ignore[reportPrivateUsage]
from bip.research.engine import FOCUS_AREAS, BusyError, ResearchRequest
from bip.research.tools import TOOLS

router = APIRouter(prefix="/research", tags=["research"])
J = dict[str, Any]
RunId = Annotated[str, Path(pattern=r"^[0-9a-f\-]{36}$")]


@router.get("/config")
def research_config(ctx: Context) -> J:
    st = ctx.research.provider.status()
    cfg = ctx.settings.ai
    return {
        "provider": {
            "name": st.provider,
            "model": st.model,
            "available": st.available,
            "reason": st.reason,
        },
        "configured": {
            "provider": cfg.provider,
            "ollama_url": cfg.ollama_url if cfg.provider == "ollama" else None,
            "ollama_model": cfg.ollama_model,
            "api_model": cfg.api_model,
            "api_key_env": cfg.api_key_env if cfg.provider == "openai_compatible" else None,
        },
        "how_to_configure": "Set BIP_AI__PROVIDER=ollama and BIP_AI__OLLAMA_MODEL=<an installed "
        "model tag> (BIP never pulls models), or BIP_AI__PROVIDER=openai_compatible with "
        "BIP_AI__API_BASE_URL, BIP_AI__API_MODEL and the key in the variable named by "
        "BIP_AI__API_KEY_ENV. Restart the API to apply.",
        "budget": ctx.research.budget(),
        "focus_areas": list(FOCUS_AREAS),
        "tools": [
            {"name": t.name, "description": t.description, "specialists": sorted(t.specialists)}
            for t in TOOLS.values()
        ],
    }


@router.post("/preview")
def preview(ctx: Context, body: ResearchRequest) -> J:
    _require_chain(ctx, body.chain)
    return ctx.research.preview(body)


@router.post("/runs", status_code=202)
def start(ctx: WritableContext, body: ResearchRequest) -> Any:
    _require_chain(ctx, body.chain)
    try:
        key = ctx.research.start(body)
    except BusyError as exc:
        return error_response(409, "research_busy", str(exc))
    return ctx.research.get(key, detail=False)


@router.get("/runs")
def list_runs(
    ctx: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    chain: Annotated[str | None, Query(max_length=32)] = None,
    address: Annotated[str | None, Query(max_length=100)] = None,
) -> J:
    items, total = ctx.research.list(limit, offset, chain, address)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/runs/{run_id}")
def get_run(ctx: Context, run_id: RunId) -> J:
    return ctx.research.get(run_id)


@router.post("/runs/{run_id}/cancel")
def cancel(ctx: WritableContext, run_id: RunId) -> J:
    return ctx.research.cancel(run_id)


@router.post("/runs/{run_id}/rerun", status_code=202)
def rerun(ctx: WritableContext, run_id: RunId) -> Any:
    try:
        key = ctx.research.rerun(run_id)
    except BusyError as exc:
        return error_response(409, "research_busy", str(exc))
    return ctx.research.get(key, detail=False)


@router.get("/runs/{run_id}/report")
def report(ctx: Context, run_id: RunId) -> J:
    return ctx.research.report(run_id)
