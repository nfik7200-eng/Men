from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, insert, inspect, select
from sqlalchemy.exc import IntegrityError

from bip.db import schema
from bip.db.engine import migrate
from bip.investigations.models import (
    Case,
    CaseEvidence,
    CaseHypothesis,
    CaseLead,
    CaseType,
)
from bip.investigations.templates import TEMPLATES, get_template

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
EXPECTED = {
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
}


def _case(**kw: object) -> Case:
    base: dict[str, object] = {
        "title": "t",
        "case_type": "rugpull",
        "created_by": "me",
        "created_at": NOW,
        "updated_at": NOW,
    }
    return Case.model_validate(base | kw)


def test_eleven_templates_cover_all_case_types() -> None:
    assert set(TEMPLATES) == EXPECTED == set(get_args(CaseType)) == set(schema.CASE_TYPES)
    assert len(TEMPLATES) == 11
    for ct, t in TEMPLATES.items():
        assert t.case_type == ct
        assert t.required_evidence
        assert t.analysis_steps
        assert t.initial_leads
    assert get_template("cluster").case_type == "cluster"


def test_case_rejects_bad_type_and_naive_time() -> None:
    with pytest.raises(ValidationError):
        _case(case_type="bogus")
    with pytest.raises(ValidationError):
        _case(created_at=datetime(2026, 1, 1))


def test_hypothesis_supported_requires_evidence_and_bounds() -> None:
    common = {
        "case_id": 1,
        "statement": "s",
        "created_by": "me",
        "created_at": NOW,
        "updated_at": NOW,
    }
    with pytest.raises(ValidationError):
        CaseHypothesis(**common, status="supported")
    with pytest.raises(ValidationError):
        CaseHypothesis(**common, confidence=1.5)
    ok = CaseHypothesis(**common, status="supported", evidence_ids=(1, 2))
    assert ok.evidence_ids == (1, 2)


def test_lead_priority_bounds() -> None:
    with pytest.raises(ValidationError):
        CaseLead(case_id=1, title="x", priority=9, created_at=NOW, updated_at=NOW)


def test_evidence_requires_provenance_fields() -> None:
    with pytest.raises(ValidationError):
        CaseEvidence.model_validate({"case_id": 1, "kind": "verified_observation"})
    with pytest.raises(ValidationError):
        CaseEvidence(
            case_id=1,
            kind="rumour",  # type: ignore[arg-type]
            subject_type="address",
            subject_ref="a",
            description="d",
            source_kind="provider",
            collected_at=NOW,
            created_at=NOW,
        )


def test_migration_v6_creates_tables_and_enforces_constraints() -> None:
    engine = create_engine("sqlite://")
    assert migrate(engine) == schema.SCHEMA_VERSION >= 6
    assert migrate(engine) == schema.SCHEMA_VERSION  # idempotent
    names = set(inspect(engine).get_table_names())
    assert {"cases", "case_evidence", "case_hypotheses", "case_leads"} <= names
    with engine.begin() as conn:
        cid = conn.execute(
            insert(schema.cases).values(
                title="t",
                case_type="rugpull",
                status="open",
                template_version="1",
                created_by="me",
                created_at=NOW,
                updated_at=NOW,
            )
        ).inserted_primary_key[0]
        assert conn.execute(select(schema.cases.c.id)).scalar_one() == cid
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            insert(schema.cases).values(
                title="t",
                case_type="nope",
                status="open",
                template_version="1",
                created_by="me",
                created_at=NOW,
                updated_at=NOW,
            )
        )
