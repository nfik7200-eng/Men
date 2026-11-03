from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from bip.contracts.findings import (
    Attribution,
    Claim,
    ClaimType,
    EntityKind,
    EntityRef,
    EvidenceKind,
    EvidenceRef,
    Finding,
    FindingStatus,
)
from bip.contracts.provenance import IngestionMethod, Provenance, SourceKind, VerificationStatus
from tests.conftest import COLLECTED, EVM_ADDR_A, evm_provenance

VERIFIED_TX = EvidenceRef(
    evidence_id="ev-tx-1",
    kind=EvidenceKind.CHAIN_RECORD,
    locator="silver/transactions/chain=ethereum#0xab..",
    provenance=evm_provenance(verification_status=VerificationStatus.CROSS_VERIFIED),
)
UNVERIFIED_TX = EvidenceRef(
    evidence_id="ev-tx-2",
    kind=EvidenceKind.CHAIN_RECORD,
    locator="bronze/x",
    provenance=evm_provenance(),
)
METRIC = EvidenceRef(
    evidence_id="ev-metric",
    kind=EvidenceKind.DERIVED_METRIC,
    locator="gold/token_daily_metrics/date=2026-11-02",
)
WEB_DOC = EvidenceRef(
    evidence_id="ev-doc",
    kind=EvidenceKind.WEB_DOCUMENT,
    locator="https://docs.project.xyz/team",
    provenance=Provenance(
        chain=None,
        source_kind=SourceKind.WEB,
        source_provider="web-fetcher",
        collected_at=COLLECTED,
        ingestion_method=IngestionMethod.ON_DEMAND,
        schema_version=1,
        verification_status=VerificationStatus.SINGLE_SOURCE,
    ),
)
SUBJECT = EntityRef(kind=EntityKind.ADDRESS, identifier=EVM_ADDR_A, chain="ethereum")


def finding(**kw: object) -> Finding:
    data: dict[str, object] = {
        "finding_id": "f-0001",
        "finding_type": "large-transfer",
        "subject": SUBJECT,
        "claims": (
            Claim(
                statement="Address sent 5,000 ETH in one transaction.",
                claim_type=ClaimType.VERIFIED_FACT,
                evidence_ids=("ev-tx-1",),
            ),
        ),
        "evidence": (VERIFIED_TX,),
        "detected_at": COLLECTED,
        "explanation": "Single transfer above the whale threshold.",
        "produced_by": "rule.whale-v1",
    }
    data.update(kw)
    return Finding.model_validate(data)


def test_verified_fact_finding() -> None:
    f = finding(status=FindingStatus.VERIFIED)
    assert f.status is FindingStatus.VERIFIED


def test_mixed_claims_need_limitations_and_cannot_be_verified() -> None:
    claims = (
        Claim(
            statement="Transfer occurred.",
            claim_type=ClaimType.VERIFIED_FACT,
            evidence_ids=("ev-tx-1",),
        ),
        Claim(
            statement="Volume is 6 sigma above baseline.",
            claim_type=ClaimType.STATISTICAL_OBSERVATION,
            evidence_ids=("ev-metric",),
            method="robust-zscore-30d",
            confidence=0.9,
        ),
        Claim(
            statement="Possibly preparing a CEX deposit.",
            claim_type=ClaimType.HYPOTHESIS,
            evidence_ids=("ev-tx-1",),
            confidence=0.3,
        ),
    )
    evidence = (VERIFIED_TX, METRIC)
    with pytest.raises(ValidationError, match="limitations"):
        finding(claims=claims, evidence=evidence)
    with pytest.raises(ValidationError, match="every claim is a verified_fact"):
        finding(
            claims=claims,
            evidence=evidence,
            status=FindingStatus.VERIFIED,
            limitations=("Hypothesis untested.",),
        )
    ok = finding(
        claims=claims,
        evidence=evidence,
        status=FindingStatus.PARTIALLY_VERIFIED,
        limitations=("CEX label coverage is partial.",),
    )
    assert len(ok.claims) == 3


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"claims": ()}, "at least 1"),
        ({"evidence": ()}, "at least 1"),
        (
            {
                "claims": (
                    Claim(
                        statement="x", claim_type=ClaimType.HYPOTHESIS, evidence_ids=("ev-missing",)
                    ),
                ),
                "limitations": ("l",),
            },
            "unknown evidence",
        ),
        (
            {
                "claims": (
                    Claim(
                        statement="x", claim_type=ClaimType.VERIFIED_FACT, evidence_ids=("ev-tx-2",)
                    ),
                ),
                "evidence": (UNVERIFIED_TX,),
            },
            "verified provenance",
        ),
        ({"evidence": (VERIFIED_TX, VERIFIED_TX)}, "duplicate"),
        (
            {"observation_start": COLLECTED, "observation_end": COLLECTED - timedelta(days=1)},
            "observation_end",
        ),
        ({"observation_start": COLLECTED + timedelta(days=1)}, "detected_at"),
        ({"confidence": 1.5}, "less than or equal"),
    ],
)
def test_invalid_findings(kw: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        finding(**kw)


def test_claims_without_evidence_are_impossible() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        Claim(
            statement="The deployer is a known fund.",
            claim_type=ClaimType.HYPOTHESIS,
            evidence_ids=(),
        )


def test_statistical_claim_requires_method() -> None:
    with pytest.raises(ValidationError, match="method"):
        Claim(statement="x", claim_type=ClaimType.STATISTICAL_OBSERVATION, evidence_ids=("e",))


def test_chain_evidence_requires_provenance() -> None:
    with pytest.raises(ValidationError, match="requires provenance"):
        EvidenceRef(evidence_id="e", kind=EvidenceKind.CHAIN_RECORD, locator="x")


def test_attribution_requires_verified_public_evidence() -> None:
    attributed = SUBJECT.model_copy(
        update={
            "attribution": Attribution(
                name="Example Labs",
                basis="Listed as the deployer on the project's official docs.",
                evidence_ids=("ev-doc",),
            )
        }
    )
    ok = finding(subject=attributed, evidence=(VERIFIED_TX, WEB_DOC))
    assert ok.subject.attribution is not None

    chain_only = SUBJECT.model_copy(
        update={
            "attribution": Attribution(
                name="Example Labs",
                basis="Same funder as a labelled wallet.",
                evidence_ids=("ev-tx-1",),
            )
        }
    )
    with pytest.raises(ValidationError, match="public web evidence"):
        finding(subject=chain_only)
    dangling = SUBJECT.model_copy(
        update={
            "attribution": Attribution(name="Example Labs", basis="x", evidence_ids=("ev-nope",))
        }
    )
    with pytest.raises(ValidationError, match="unknown evidence"):
        finding(subject=dangling)
