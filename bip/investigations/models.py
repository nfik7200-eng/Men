"""Typed investigation case models.

Pure data layer: no network, no provider SDKs. Evidence carries provenance and one of three
epistemic kinds (verified observation, statistical signal, hypothesis). Hypotheses must cite
evidence IDs; an address is never asserted to be a real-world person.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CaseType = Literal[
    "rugpull",
    "hack_tracking",
    "cluster",
    "insider",
    "ecosystem",
    "sanctions_aml",
    "mev_sandwich",
    "flash_loan",
    "nft_wash_trading",
    "pump_dump",
    "phishing_tracking",
]
CaseStatus = Literal["open", "in_review", "closed", "archived"]
EvidenceKind = Literal["verified_observation", "statistical_signal", "hypothesis"]
HypothesisStatus = Literal["open", "supported", "refuted", "inconclusive"]
LeadStatus = Literal["new", "investigating", "resolved", "dismissed"]

TEMPLATE_VERSION = "1"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value


class Case(_Model):
    id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    case_type: CaseType
    status: CaseStatus = "open"
    chain: str | None = None
    summary: str | None = Field(default=None, max_length=2000)
    template_version: str = TEMPLATE_VERSION
    created_by: str = Field(min_length=1, max_length=80)
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None

    @field_validator("created_at", "updated_at", "closed_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _require_aware(v)


class CaseEvidence(_Model):
    id: int | None = None
    case_id: int
    kind: EvidenceKind
    chain: str | None = None
    subject_type: str = Field(min_length=1, max_length=24)
    subject_ref: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    payload_json: str | None = None
    # Provenance (required).
    source_kind: str = Field(min_length=1, max_length=24)
    provider_id: str | None = None
    source_ref: str | None = None
    raw_ref: str | None = None
    collected_at: datetime
    created_at: datetime

    @field_validator("collected_at", "created_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v)


class CaseHypothesis(_Model):
    id: int | None = None
    case_id: int
    statement: str = Field(min_length=1, max_length=1000)
    status: HypothesisStatus = "open"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: tuple[int, ...] = ()
    created_by: str = Field(min_length=1, max_length=80)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v)

    @field_validator("evidence_ids")
    @classmethod
    def _supported_needs_evidence(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate evidence ids")
        return v

    def model_post_init(self, __context: object) -> None:
        if self.status == "supported" and not self.evidence_ids:
            raise ValueError("a supported hypothesis must cite evidence ids")


class CaseLead(_Model):
    id: int | None = None
    case_id: int
    title: str = Field(min_length=1, max_length=200)
    status: LeadStatus = "new"
    priority: int = Field(default=3, ge=1, le=5)
    subject_type: str | None = None
    subject_ref: str | None = None
    rationale: str | None = Field(default=None, max_length=1000)
    source_evidence_id: int | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v)


class CaseTemplate(_Model):
    """Static playbook for a case type: what to collect and which questions to answer."""

    case_type: CaseType
    name: str
    description: str
    version: str = TEMPLATE_VERSION
    required_evidence: tuple[str, ...]
    suggested_hypotheses: tuple[str, ...]
    initial_leads: tuple[str, ...]
    analysis_steps: tuple[str, ...]
