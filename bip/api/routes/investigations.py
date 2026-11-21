from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from bip.api.deps import AppContext, Context, WritableContext
from bip.api.schemas import Body, InvestigationCreate, InvestigationUpdate
from bip.contracts.blockchain import get_chain
from bip.contracts.findings import Finding
from bip.db import schema
from bip.db.errors import InvalidRecordError
from bip.db.records import FindingSummary, Investigation, InvestigationStatus, InvestigationSummary
from bip.db.repository import canonical_address
from bip.db.types import utcnow
from bip.investigations.models import (
    Case,
    CaseEvidence,
    CaseHypothesis,
    CaseLead,
    CaseStatus,
    CaseTemplate,
    CaseType,
    EvidenceKind,
    HypothesisStatus,
    LeadStatus,
)
from bip.investigations.store import CaseStore
from bip.investigations.templates import TEMPLATES, get_template

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


class ResearchLinkIn(Body):
    run_id: Annotated[str, Field(min_length=1, max_length=36)]
    note: Annotated[str, Field(max_length=500)] | None = None


@router.post(
    "/investigations/{investigation_id}/research-runs", status_code=status.HTTP_201_CREATED
)
def link_research_run(
    ctx: WritableContext, investigation_id: Annotated[int, Path(ge=1)], body: ResearchLinkIn
) -> dict[str, object]:
    ctx.repo.link_research_run(investigation_id, body.run_id, body.note)
    return {"investigation_id": investigation_id, "run_id": body.run_id, "note": body.note}


@router.get("/investigations/{investigation_id}/research-runs")
def list_research_links(
    ctx: Context, investigation_id: Annotated[int, Path(ge=1)]
) -> dict[str, object]:
    return {"items": ctx.repo.research_links(investigation_id)}


@router.delete(
    "/investigations/{investigation_id}/research-runs/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unlink_research_run(
    ctx: WritableContext,
    investigation_id: Annotated[int, Path(ge=1)],
    run_id: Annotated[str, Path(min_length=1, max_length=36)],
) -> None:
    ctx.repo.unlink_research_run(investigation_id, run_id)


# ---------------------------------------------------------------------------------------------
# Investigation workbench: cases, evidence, hypotheses, leads and the automated scan.
# Cases live under /cases; /investigations/run is the scan entry point.
# ---------------------------------------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseCreate(_Body):
    title: str = Field(min_length=1, max_length=200)
    case_type: CaseType
    chain: str | None = Field(default=None, max_length=32)
    summary: str | None = Field(default=None, max_length=2000)
    created_by: str = Field(default="analyst", min_length=1, max_length=80)


class CaseUpdate(_Body):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: CaseStatus | None = None
    summary: str | None = Field(default=None, max_length=2000)


class EvidenceCreate(_Body):
    kind: EvidenceKind
    chain: str | None = Field(default=None, max_length=32)
    subject_type: str = Field(min_length=1, max_length=24)
    subject_ref: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    payload: dict[str, Any] | None = None
    source_kind: str = Field(min_length=1, max_length=24)
    provider_id: str | None = Field(default=None, max_length=64)
    source_ref: str | None = Field(default=None, max_length=300)
    raw_ref: str | None = Field(default=None, max_length=100)
    collected_at: datetime

    @field_validator("collected_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")
        return v


class HypothesisCreate(_Body):
    statement: str = Field(min_length=1, max_length=1000)
    status: HypothesisStatus = "open"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: tuple[int, ...] = ()
    created_by: str = Field(default="analyst", min_length=1, max_length=80)


class HypothesisUpdate(_Body):
    status: HypothesisStatus | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: tuple[int, ...] | None = None


class LeadCreate(_Body):
    title: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=3, ge=1, le=5)
    subject_type: str | None = Field(default=None, max_length=24)
    subject_ref: str | None = Field(default=None, max_length=200)
    rationale: str | None = Field(default=None, max_length=1000)
    source_evidence_id: int | None = None


class LeadUpdate(_Body):
    status: LeadStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=5)


class RunRequest(_Body):
    case_type: CaseType
    chain: str = Field(min_length=1, max_length=32)
    target_type: Literal["address", "tx"] = "address"
    target: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    created_by: str = Field(default="analyst", min_length=1, max_length=80)


class CaseDetail(BaseModel):
    case: Case
    evidence: list[CaseEvidence]
    hypotheses: list[CaseHypothesis]
    leads: list[CaseLead]


class RunResult(BaseModel):
    case: Case
    template: CaseTemplate
    target_type: str
    target: str
    analysis_run_id: int | None
    evidence: list[CaseEvidence]
    hypotheses: list[CaseHypothesis]
    leads: list[CaseLead]
    coverage_gaps: list[str]
    required_evidence_unconfirmed: list[str]
    note: str


def _store(ctx: AppContext) -> CaseStore:
    return CaseStore(ctx.repo)


@router.get("/cases/templates", response_model=list[CaseTemplate])
def list_case_templates() -> list[CaseTemplate]:
    return list(TEMPLATES.values())


@router.get("/cases/templates/{case_type}", response_model=CaseTemplate)
def get_case_template(case_type: CaseType) -> CaseTemplate:
    return get_template(case_type)


@router.get("/cases", response_model=list[Case])
def list_cases(
    ctx: Context,
    status_filter: Annotated[CaseStatus | None, Query(alias="status")] = None,
    case_type: CaseType | None = None,
) -> list[Case]:
    return _store(ctx).list_cases(status_filter, case_type)


@router.post("/cases", response_model=Case, status_code=status.HTTP_201_CREATED)
def create_case(ctx: WritableContext, body: CaseCreate) -> Case:
    chain = _canonical_chain(body.chain) if body.chain is not None else None
    return _store(ctx).create_case(
        title=body.title,
        case_type=body.case_type,
        chain=chain,
        summary=body.summary,
        created_by=body.created_by,
    )


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case(ctx: Context, case_id: Id) -> CaseDetail:
    st = _store(ctx)
    return CaseDetail(
        case=st.get_case(case_id),
        evidence=st.list_evidence(case_id),
        hypotheses=st.list_hypotheses(case_id),
        leads=st.list_leads(case_id),
    )


@router.patch("/cases/{case_id}", response_model=Case)
def update_case(ctx: WritableContext, case_id: Id, body: CaseUpdate) -> Case:
    return _store(ctx).update_case(
        case_id, title=body.title, status=body.status, summary=body.summary
    )


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(ctx: WritableContext, case_id: Id) -> Response:
    _store(ctx).delete_case(case_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/cases/{case_id}/evidence", response_model=list[CaseEvidence])
def list_evidence(
    ctx: Context, case_id: Id, kind: EvidenceKind | None = None
) -> list[CaseEvidence]:
    return _store(ctx).list_evidence(case_id, kind)


@router.post(
    "/cases/{case_id}/evidence", response_model=CaseEvidence, status_code=status.HTTP_201_CREATED
)
def add_case_evidence(ctx: WritableContext, case_id: Id, body: EvidenceCreate) -> CaseEvidence:
    chain = _canonical_chain(body.chain) if body.chain is not None else None
    ev = _evidence_from(case_id, body.model_copy(update={"chain": chain}))
    return _store(ctx).add_evidence(case_id, ev)


@router.get("/cases/{case_id}/evidence/{evidence_id}", response_model=CaseEvidence)
def get_case_evidence(ctx: Context, case_id: Id, evidence_id: Id) -> CaseEvidence:
    return _store(ctx).get_evidence(case_id, evidence_id)


@router.delete("/cases/{case_id}/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case_evidence(ctx: WritableContext, case_id: Id, evidence_id: Id) -> Response:
    _store(ctx).delete_evidence(case_id, evidence_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/cases/{case_id}/hypotheses", response_model=list[CaseHypothesis])
def list_hypotheses(ctx: Context, case_id: Id) -> list[CaseHypothesis]:
    return _store(ctx).list_hypotheses(case_id)


@router.post(
    "/cases/{case_id}/hypotheses",
    response_model=CaseHypothesis,
    status_code=status.HTTP_201_CREATED,
)
def add_hypothesis(ctx: WritableContext, case_id: Id, body: HypothesisCreate) -> CaseHypothesis:
    now = utcnow()
    try:
        h = CaseHypothesis(case_id=case_id, created_at=now, updated_at=now, **body.model_dump())
    except ValueError as exc:
        raise InvalidRecordError(str(exc).splitlines()[-1]) from None
    return _store(ctx).add_hypothesis(case_id, h)


@router.patch("/cases/{case_id}/hypotheses/{hypothesis_id}", response_model=CaseHypothesis)
def update_hypothesis(
    ctx: WritableContext, case_id: Id, hypothesis_id: Id, body: HypothesisUpdate
) -> CaseHypothesis:
    return _store(ctx).update_hypothesis(
        case_id,
        hypothesis_id,
        status=body.status,
        confidence=body.confidence,
        evidence_ids=body.evidence_ids,
    )


@router.get("/cases/{case_id}/leads", response_model=list[CaseLead])
def list_leads(
    ctx: Context,
    case_id: Id,
    status_filter: Annotated[LeadStatus | None, Query(alias="status")] = None,
) -> list[CaseLead]:
    return _store(ctx).list_leads(case_id, status_filter)


@router.post("/cases/{case_id}/leads", response_model=CaseLead, status_code=status.HTTP_201_CREATED)
def add_lead(ctx: WritableContext, case_id: Id, body: LeadCreate) -> CaseLead:
    now = utcnow()
    lead = CaseLead(case_id=case_id, created_at=now, updated_at=now, **body.model_dump())
    return _store(ctx).add_lead(case_id, lead)


@router.patch("/cases/{case_id}/leads/{lead_id}", response_model=CaseLead)
def update_lead(ctx: WritableContext, case_id: Id, lead_id: Id, body: LeadUpdate) -> CaseLead:
    return _store(ctx).update_lead(case_id, lead_id, status=body.status, priority=body.priority)


@router.post("/investigations/run", response_model=RunResult, status_code=status.HTTP_201_CREATED)
def run_investigation(ctx: WritableContext, body: RunRequest) -> RunResult:
    """Open a case from a template and run the automated scan over locally stored data.

    The scan never calls providers: it uses records BIP already retrieved. Findings are labelled
    by epistemic kind; the template's required evidence is listed as unconfirmed because an
    automated scan cannot establish it. Missing data is reported as a coverage gap, not zero.
    """
    template = get_template(body.case_type)
    evidence_specs: list[EvidenceCreate] = []
    gaps: list[str] = []
    run_id: int | None = None
    now = utcnow()
    chain: str
    if body.target_type == "address":
        ref = canonical_address(body.chain, body.target)
        chain, target = ref.chain, ref.address
        with ctx.analysis_lock:
            run = ctx.intel.analyze(chain, target)
        run_id = int(run["run_id"])
        result: dict[str, Any] = run["result"]
        ref_s = f"analysis_run:{run_id}"
        scope = result["scope"]
        if scope["transactions"] == 0 and scope["transfers"] == 0:
            gaps.append(
                "No transactions or transfers are stored for this address; ingest it first. "
                "Absence of records is not evidence of inactivity."
            )
        else:
            evidence_specs.append(
                _spec(
                    "verified_observation",
                    chain,
                    "address",
                    target,
                    f"{scope['transactions']} transactions and {scope['transfers']} transfers "
                    "observed in the retrieved dataset (not lifetime history).",
                    ref_s,
                    now,
                    {"scope": scope},
                )
            )
        for a in result.get("anomalies", []):
            if a["status"] == "anomaly":
                evidence_specs.append(
                    _spec(
                        "statistical_signal",
                        chain,
                        "address",
                        target,
                        f"Detector {a['detector_id']} flagged an anomaly "
                        f"(severity {a['severity']}).",
                        ref_s,
                        now,
                        a,
                    )
                )
        for r in result.get("behaviour", []):
            if r["status"] == "detected":
                evidence_specs.append(
                    _spec(
                        "hypothesis",
                        chain,
                        "address",
                        target,
                        f"Behaviour rule '{r['name']}' matched; this is a hypothesis, "
                        "not a verified conclusion.",
                        ref_s,
                        now,
                        r,
                    )
                )
    else:
        chain = _canonical_chain(body.chain)
        target = body.target.strip()
        with ctx.repo.transaction() as conn:
            row = conn.execute(
                select(schema.transactions.c.tx_id, schema.transactions.c.provenance_json).where(
                    schema.transactions.c.chain == chain,
                    schema.transactions.c.tx_id.in_([target, target.lower()]),
                )
            ).first()
        if row is None:
            gaps.append("Transaction is not in local storage; no provider is queried by this scan.")
        else:
            evidence_specs.append(
                _spec(
                    "verified_observation",
                    chain,
                    "tx",
                    str(row[0]),
                    "Transaction record present in local storage with its original provenance.",
                    f"tx:{row[0]}",
                    now,
                    {"provenance": json.loads(row[1])},
                )
            )

    st = _store(ctx)
    case = st.create_case(
        title=body.title or f"{template.name}: {target[:60]}",
        case_type=body.case_type,
        chain=chain,
        summary=None,
        created_by=body.created_by,
    )
    assert case.id is not None  # noqa: S101
    evidence = [st.add_evidence(case.id, _evidence_from(case.id, s)) for s in evidence_specs]
    hypotheses = [
        st.add_hypothesis(
            case.id,
            CaseHypothesis(
                case_id=case.id,
                statement=text,
                created_by="template",
                created_at=now,
                updated_at=now,
            ),
        )
        for text in template.suggested_hypotheses
    ]
    leads = [
        st.add_lead(
            case.id,
            CaseLead(
                case_id=case.id,
                title=text,
                subject_type=body.target_type,
                subject_ref=target,
                created_at=now,
                updated_at=now,
            ),
        )
        for text in template.initial_leads
    ]
    return RunResult(
        case=st.get_case(case.id),
        template=template,
        target_type=body.target_type,
        target=target,
        analysis_run_id=run_id,
        evidence=evidence,
        hypotheses=hypotheses,
        leads=leads,
        coverage_gaps=gaps,
        required_evidence_unconfirmed=list(template.required_evidence),
        note=(
            "Template hypotheses are unsupported suggestions (no evidence cited). "
            "No claim is made about who controls any address."
        ),
    )


def _canonical_chain(chain: str) -> str:
    try:
        return get_chain(chain).chain_id
    except ValueError:
        raise InvalidRecordError(f"unknown chain '{chain}'") from None


def _spec(
    kind: EvidenceKind,
    chain: str,
    subject_type: str,
    subject_ref: str,
    description: str,
    source_ref: str,
    collected_at: datetime,
    payload: dict[str, Any],
) -> EvidenceCreate:
    return EvidenceCreate(
        kind=kind,
        chain=chain,
        subject_type=subject_type,
        subject_ref=subject_ref,
        description=description,
        payload=payload,
        source_kind="local_analysis" if source_ref.startswith("analysis_run") else "local_store",
        source_ref=source_ref,
        collected_at=collected_at,
    )


def _evidence_from(case_id: int, body: EvidenceCreate) -> CaseEvidence:
    return CaseEvidence(
        case_id=case_id,
        created_at=utcnow(),
        payload_json=None if body.payload is None else json.dumps(body.payload, default=str),
        **body.model_dump(exclude={"payload"}),
    )
