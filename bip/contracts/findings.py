"""Evidence-based intelligence findings.

A finding is a set of **claims**, each of a declared type and each citing evidence held in the
same finding:

* ``verified_fact``: directly present in data whose provenance is verified;
* ``statistical_observation``: computed with a stated ``method``;
* ``analytical_interpretation``: reasoning over facts and observations;
* ``hypothesis``: an unconfirmed explanation.

The validators make the honest path the only one that validates. A claim without evidence, a
"verified" finding containing hypotheses, or an identity attribution without independent public
evidence are all rejected. This is the contract future AI agents must satisfy (ADR-009).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, model_validator

from bip.contracts.base import ContractModel, Probability, Slug
from bip.contracts.blockchain import ChainId
from bip.contracts.provenance import VERIFIED_STATUSES, Provenance, SourceKind

EvidenceId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class ClaimType(StrEnum):
    VERIFIED_FACT = "verified_fact"
    STATISTICAL_OBSERVATION = "statistical_observation"
    ANALYTICAL_INTERPRETATION = "analytical_interpretation"
    HYPOTHESIS = "hypothesis"


class EvidenceKind(StrEnum):
    CHAIN_RECORD = "chain_record"
    DATASET_ROW = "dataset_row"
    WEB_DOCUMENT = "web_document"
    DERIVED_METRIC = "derived_metric"


class FindingStatus(StrEnum):
    UNVERIFIED = "unverified"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    REFUTED = "refuted"


class EntityKind(StrEnum):
    ADDRESS = "address"
    CONTRACT = "contract"
    TOKEN = "token"  # noqa: S105 - enum label, not a credential
    PROTOCOL = "protocol"
    DEVELOPER = "developer"
    PROJECT = "project"
    EXCHANGE = "exchange"
    CLUSTER = "cluster"


class EvidenceRef(ContractModel):
    """A pointer to data supporting a claim."""

    evidence_id: EvidenceId
    kind: EvidenceKind
    locator: Annotated[str, Field(min_length=1, max_length=2048)]
    """Where to find it: table/partition/natural key, file URI, or document URL."""
    provenance: Provenance | None = None
    description: Text | None = None

    @property
    def is_verified(self) -> bool:
        return (
            self.provenance is not None and self.provenance.verification_status in VERIFIED_STATUSES
        )

    @model_validator(mode="after")
    def _needs_provenance(self) -> Self:
        if (
            self.kind
            in {EvidenceKind.CHAIN_RECORD, EvidenceKind.DATASET_ROW, EvidenceKind.WEB_DOCUMENT}
            and self.provenance is None
        ):
            raise ValueError(f"{self.kind.value} evidence requires provenance")
        return self


class Attribution(ContractModel):
    """Link between an entity and a real-world name, backed by public evidence."""

    name: Annotated[str, Field(min_length=1, max_length=200)]
    basis: Text
    """How the evidence supports the attribution (e.g. 'listed on the project's official docs')."""
    evidence_ids: Annotated[tuple[EvidenceId, ...], Field(min_length=1)]


class EntityRef(ContractModel):
    kind: EntityKind
    identifier: Annotated[str, Field(min_length=1, max_length=256)]
    chain: ChainId | None = None
    attribution: Attribution | None = None


class Claim(ContractModel):
    statement: Text
    claim_type: ClaimType
    evidence_ids: Annotated[tuple[EvidenceId, ...], Field(min_length=1)]
    confidence: Probability | None = None
    method: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    """Rule or statistical method. Required for statistical observations."""

    @model_validator(mode="after")
    def _method_required(self) -> Self:
        if self.claim_type is ClaimType.STATISTICAL_OBSERVATION and self.method is None:
            raise ValueError("statistical observations must state their method")
        return self


class Finding(ContractModel):
    finding_id: EvidenceId
    finding_type: Slug
    subject: EntityRef
    related: tuple[EntityRef, ...] = ()
    claims: Annotated[tuple[Claim, ...], Field(min_length=1)]
    evidence: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    detected_at: AwareDatetime
    observation_start: AwareDatetime | None = None
    observation_end: AwareDatetime | None = None
    explanation: Text
    status: FindingStatus = FindingStatus.UNVERIFIED
    confidence: Probability | None = None
    limitations: tuple[Text, ...] = ()
    produced_by: Annotated[str, Field(min_length=1, max_length=128)]
    """Rule id, detector version or agent/model version that produced the finding."""

    @model_validator(mode="after")
    def _evidence_rules(self) -> Self:
        by_id: dict[str, EvidenceRef] = {}
        for ev in self.evidence:
            if ev.evidence_id in by_id:
                raise ValueError(f"duplicate evidence_id {ev.evidence_id!r}")
            by_id[ev.evidence_id] = ev

        for claim in self.claims:
            missing = [e for e in claim.evidence_ids if e not in by_id]
            if missing:
                raise ValueError(f"claim cites unknown evidence {missing}")
            if claim.claim_type is ClaimType.VERIFIED_FACT:
                cited = [by_id[e] for e in claim.evidence_ids]
                if not all(ev.is_verified for ev in cited):
                    raise ValueError(
                        "verified_fact claims may only cite evidence with verified provenance"
                    )

        if self.status is FindingStatus.VERIFIED and any(
            c.claim_type is not ClaimType.VERIFIED_FACT for c in self.claims
        ):
            raise ValueError("a finding is 'verified' only when every claim is a verified_fact")
        if not self.limitations and any(
            c.claim_type is not ClaimType.VERIFIED_FACT for c in self.claims
        ):
            raise ValueError("findings with non-factual claims must state their limitations")

        for entity in (self.subject, *self.related):
            if entity.attribution is None:
                continue
            refs = [by_id.get(e) for e in entity.attribution.evidence_ids]
            if any(r is None for r in refs):
                raise ValueError("attribution cites unknown evidence")
            if not any(
                r is not None
                and r.kind is EvidenceKind.WEB_DOCUMENT
                and r.provenance is not None
                and r.provenance.source_kind is SourceKind.WEB
                and r.is_verified
                for r in refs
            ):
                raise ValueError("real-world attribution requires verified public web evidence")

        if (
            self.observation_start is not None
            and self.observation_end is not None
            and self.observation_end < self.observation_start
        ):
            raise ValueError("observation_end is before observation_start")
        if self.observation_start is not None and self.detected_at < self.observation_start:
            raise ValueError("detected_at is before observation_start")
        return self
