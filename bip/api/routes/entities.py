"""Entities: search, transaction/token/contract detail, relationships and case notes.

All of it is read from, or written about, data BIP already stored. Responses state their basis
and what is not known, and orphaned transactions are excluded from every derived figure.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Request, status

from bip.api.deps import Context, WritableContext
from bip.api.routes.chains import _require_chain  # pyright: ignore[reportPrivateUsage]
from bip.api.schemas import Body
from bip.db.repository import canonical_address
from bip.entities import views

router = APIRouter(tags=["entities"])
ChainP = Annotated[str, Path(min_length=1, max_length=32)]
Ref = Annotated[str, Path(min_length=3, max_length=100)]
Id = Annotated[int, Path(ge=1, le=2**53)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


class NoteCreate(Body):
    kind: Literal["note", "hypothesis"] = "note"
    body: Annotated[str, Query(min_length=1, max_length=4000)]


class EvidenceIn(Body):
    kind: Literal["url", "transaction", "signal", "note"]
    ref: Annotated[str, Query(min_length=1, max_length=300)]
    description: Annotated[str, Query(max_length=300)] = ""


class LabelPropose(Body):
    chain: Annotated[str, Query(min_length=1, max_length=32)]
    ref: Annotated[str, Query(min_length=3, max_length=100)]
    entity_kind: Literal["wallet", "contract", "token", "pool", "program"]
    category: Literal["exchange", "protocol", "bridge", "market_maker", "treasury", "team", "other"]
    name: Annotated[str, Query(min_length=1, max_length=80)]
    evidence: Annotated[list[EvidenceIn], Query(max_length=10)] = []  # noqa: RUF012
    note: Annotated[str | None, Query(max_length=500)] = None


class LabelReview(Body):
    decision: Literal["approve", "reject"]
    note: Annotated[str, Query(min_length=10, max_length=500)]
    evidence: Annotated[list[EvidenceIn], Query(max_length=10)] = []  # noqa: RUF012


class LabelSuggest(Body):
    chain: Annotated[str, Query(min_length=1, max_length=32)]
    ref: Annotated[str, Query(min_length=3, max_length=100)]
    entity_kind: Literal["wallet", "contract", "token", "pool", "program"]


@router.get("/search")
def search(
    ctx: Context,
    q: Annotated[str, Query(min_length=3, max_length=100)],
    chain: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """Prefix search over stored wallets, tokens, contracts and transactions."""
    return views.search(ctx.repo, q, chain, limit)


@router.get("/transactions/{chain}/{tx_id}")
def transaction(ctx: Context, chain: ChainP, tx_id: Ref) -> dict[str, Any]:
    _require_chain(ctx, chain)
    return views.transaction_detail(ctx.repo, chain, tx_id)


@router.get("/tokens/{chain}/{address}")
def token(ctx: Context, chain: ChainP, address: Ref) -> dict[str, Any]:
    _require_chain(ctx, chain)
    return views.token_detail(ctx.repo, chain, address)


@router.get("/contracts/{chain}/{address}")
def contract(ctx: Context, chain: ChainP, address: Ref) -> dict[str, Any]:
    _require_chain(ctx, chain)
    return views.contract_detail(ctx.repo, chain, address)


@router.get("/entities/{chain}/{ref}/relationships")
def relationships(
    ctx: Context,
    chain: ChainP,
    ref: Ref,
    kind: Literal["funded_by", "deployed_by", "transferred_to", "interacted_with"] | None = None,
    direction: Literal["out", "in", "both"] = "both",
    status_: Annotated[Literal["active", "invalidated", "any"], Query(alias="status")] = "active",
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    _require_chain(ctx, chain)
    canonical = canonical_address(chain, ref).address
    return ctx.relationships.for_entity(
        chain, canonical, kind=kind, direction=direction, status=status_, limit=limit, offset=offset
    )


@router.get("/relationships/summary")
def relationship_summary(ctx: Context) -> dict[str, Any]:
    return {"counts": ctx.relationships.counts()}


@router.post("/relationships/rebuild", status_code=status.HTTP_201_CREATED)
def rebuild_relationships(
    ctx: WritableContext, chain: Annotated[str | None, Query(max_length=32)] = None
) -> dict[str, Any]:
    """Rebuild relationships from stored canonical data. Reads the database only."""
    if chain is not None:
        _require_chain(ctx, chain)
    return ctx.relationships.rebuild(chain)


# ----------------------------------------------------------------------------- case notes


def _author(request: Request) -> str:
    user = request.scope.get("state", {}).get("user")
    return str(user.username) if user is not None else "local"


@router.post("/investigations/{investigation_id}/notes", status_code=status.HTTP_201_CREATED)
def add_note(
    request: Request, ctx: WritableContext, investigation_id: Id, body: NoteCreate
) -> dict[str, Any]:
    return ctx.cases.add_note(investigation_id, body.kind, body.body, _author(request))


@router.get("/investigations/{investigation_id}/notes")
def list_notes(
    ctx: Context,
    investigation_id: Id,
    kind: Literal["note", "hypothesis"] | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return ctx.cases.notes(investigation_id, kind=kind, limit=limit, offset=offset)


@router.get("/investigations/{investigation_id}/timeline")
def timeline(ctx: Context, investigation_id: Id) -> dict[str, Any]:
    return ctx.cases.timeline(investigation_id)


# ------------------------------------------------------------------------------- labels


@router.post("/entities/labels", status_code=status.HTTP_201_CREATED)
def propose_label(request: Request, ctx: WritableContext, body: LabelPropose) -> dict[str, Any]:
    """Propose an attribution to an organisation or protocol. It stays a proposal until reviewed."""
    _require_chain(ctx, body.chain)
    return ctx.entity_labels.propose(
        chain=body.chain,
        ref=body.ref,
        entity_kind=body.entity_kind,
        category=body.category,
        name=body.name,
        evidence=[e.model_dump() for e in body.evidence],
        actor=_author(request),
        note=body.note,
    )


@router.get("/entities/labels")
def list_labels(
    ctx: Context,
    status: Literal["proposed", "approved", "rejected"] | None = None,
    chain: Annotated[str | None, Query(max_length=32)] = None,
    ref: Annotated[str | None, Query(max_length=100)] = None,
    category: Literal["exchange", "protocol", "bridge", "market_maker", "treasury", "team", "other"]
    | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return ctx.entity_labels.list(
        status=status, chain=chain, ref=ref, category=category, limit=limit, offset=offset
    )


@router.post("/entities/labels/{label_id}/review")
def review_label(
    request: Request, ctx: WritableContext, label_id: Id, body: LabelReview
) -> dict[str, Any]:
    """A person approves or rejects a proposal. Approval needs human-added evidence and a note."""
    return ctx.entity_labels.review(
        label_id,
        decision=body.decision,
        reviewer=_author(request),
        note=body.note,
        evidence=[e.model_dump() for e in body.evidence],
    )


@router.post("/entities/labels/suggest", status_code=status.HTTP_201_CREATED)
def suggest_labels(request: Request, ctx: WritableContext, body: LabelSuggest) -> dict[str, Any]:
    """Ask the configured local model for label proposals grounded in stored signals.

    Never contacts a chain provider. With no model configured it answers ``available: false``.
    Results are proposals only.
    """
    _require_chain(ctx, body.chain)
    return ctx.entity_labels.suggest(
        ctx.research.provider,
        chain=body.chain,
        ref=body.ref,
        entity_kind=body.entity_kind,
        actor=_author(request),
        timeout_s=ctx.settings.ai.request_timeout_s,
        max_bytes=ctx.settings.ai.max_model_output_bytes,
    )


@router.get("/entities/{chain}/{ref}/labels")
def entity_labels(ctx: Context, chain: ChainP, ref: Ref) -> dict[str, Any]:
    _require_chain(ctx, chain)
    return ctx.entity_labels.for_entity(chain, ref)
