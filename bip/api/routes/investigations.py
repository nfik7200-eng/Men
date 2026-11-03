from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from bip.api.deps import Context, WritableContext
from bip.api.schemas import InvestigationCreate, InvestigationUpdate
from bip.contracts.findings import Finding
from bip.db.records import FindingSummary, Investigation, InvestigationStatus, InvestigationSummary
from bip.db.repository import canonical_address

router = APIRouter(tags=["investigations"])
Id = Annotated[int, Path(ge=1, le=2**53)]


@router.get("/investigations", response_model=list[InvestigationSummary])
def list_investigations(
    ctx: Context, status_filter: Annotated[InvestigationStatus | None, Query(alias="status")] = None
) -> list[InvestigationSummary]:
    return ctx.repo.list_investigations(status_filter)


@router.post("/investigations", response_model=Investigation, status_code=status.HTTP_201_CREATED)
def create_investigation(ctx: WritableContext, body: InvestigationCreate) -> Investigation:
    subject = None
    if body.subject_chain is not None and body.subject_address is not None:
        subject = canonical_address(body.subject_chain, body.subject_address)
    return ctx.repo.create_investigation(body.title, body.description, subject)


@router.get("/investigations/{investigation_id}", response_model=Investigation)
def get_investigation(ctx: Context, investigation_id: Id) -> Investigation:
    return ctx.repo.get_investigation(investigation_id)


@router.patch("/investigations/{investigation_id}", response_model=Investigation)
def update_investigation(
    ctx: WritableContext, investigation_id: Id, body: InvestigationUpdate
) -> Investigation:
    return ctx.repo.update_investigation_status(investigation_id, body.status)


@router.post(
    "/investigations/{investigation_id}/findings",
    response_model=FindingSummary,
    status_code=status.HTTP_201_CREATED,
)
def add_finding(ctx: WritableContext, investigation_id: Id, body: Finding) -> FindingSummary:
    """Attach a finding. The body must satisfy the evidence rules of the Finding contract."""
    return ctx.repo.add_finding(investigation_id, body)
