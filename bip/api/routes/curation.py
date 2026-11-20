"""Human-in-the-loop curation: analyst queue, verdicts and curation statistics."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from bip.api.deps import Context, WritableContext
from bip.ml.curation import CurationStore, FeedbackIn

router = APIRouter(prefix="/curation", tags=["curation"])
J = dict[str, Any]


@router.get("/queue")
def queue(
    ctx: Context,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    chain: Annotated[Literal["ethereum", "solana"] | None, Query()] = None,
) -> J:
    return CurationStore(ctx.repo).queue(limit=limit, chain=chain)


@router.post("/feedback", status_code=201)
def submit_feedback(ctx: WritableContext, body: FeedbackIn) -> J:
    return CurationStore(ctx.repo).record(body)


@router.get("/feedback")
def list_feedback(
    ctx: Context,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    target_type: Annotated[Literal["classification_label", "pattern_match"] | None, Query()] = None,
) -> J:
    items, total = CurationStore(ctx.repo).list(target_type=target_type, limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/stats")
def stats(ctx: Context) -> J:
    return CurationStore(ctx.repo).stats()
