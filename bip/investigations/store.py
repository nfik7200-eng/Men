"""Persistence for investigation cases, evidence, hypotheses and leads (SQLAlchemy Core)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection

from bip.db import schema
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.investigations.models import (
    TEMPLATE_VERSION,
    Case,
    CaseEvidence,
    CaseHypothesis,
    CaseLead,
)


def _case_row(conn: Connection, case_id: int) -> Mapping[Any, Any]:
    row = conn.execute(select(schema.cases).where(schema.cases.c.id == case_id)).mappings().first()
    if row is None:
        raise RecordNotFoundError(f"case {case_id} not found")
    return row


def _touch(conn: Connection, case_id: int) -> None:
    conn.execute(
        update(schema.cases).where(schema.cases.c.id == case_id).values(updated_at=utcnow())
    )


def _hyp(row: Mapping[Any, Any]) -> CaseHypothesis:
    d = dict(row)
    d["evidence_ids"] = tuple(json.loads(d.pop("evidence_ids_json")))
    return CaseHypothesis(**d)


class CaseStore:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------ cases
    def create_case(
        self,
        *,
        title: str,
        case_type: str,
        chain: str | None,
        summary: str | None,
        created_by: str,
    ) -> Case:
        now = utcnow()
        with self._repo.transaction() as conn:
            pk = conn.execute(
                insert(schema.cases).values(
                    title=title,
                    case_type=case_type,
                    status="open",
                    chain=chain,
                    summary=summary,
                    template_version=TEMPLATE_VERSION,
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            return Case(**_case_row(conn, int(pk[0])))

    def get_case(self, case_id: int) -> Case:
        with self._repo.transaction() as conn:
            return Case(**_case_row(conn, case_id))

    def list_cases(self, status: str | None = None, case_type: str | None = None) -> list[Case]:
        c = schema.cases
        q = select(c).order_by(c.c.id.desc())
        if status is not None:
            q = q.where(c.c.status == status)
        if case_type is not None:
            q = q.where(c.c.case_type == case_type)
        with self._repo.transaction() as conn:
            return [Case(**r) for r in conn.execute(q).mappings()]

    def update_case(
        self,
        case_id: int,
        *,
        title: str | None,
        status: str | None,
        summary: str | None,
    ) -> Case:
        values: dict[str, Any] = {"updated_at": utcnow()}
        if title is not None:
            values["title"] = title
        if summary is not None:
            values["summary"] = summary
        if status is not None:
            values["status"] = status
            values["closed_at"] = utcnow() if status in ("closed", "archived") else None
        with self._repo.transaction() as conn:
            _case_row(conn, case_id)
            conn.execute(update(schema.cases).where(schema.cases.c.id == case_id).values(**values))
            return Case(**_case_row(conn, case_id))

    def delete_case(self, case_id: int) -> None:
        with self._repo.transaction() as conn:
            _case_row(conn, case_id)
            # Explicit child deletes: SQLite FK cascades depend on a PRAGMA.
            for t in (schema.case_leads, schema.case_hypotheses, schema.case_evidence):
                conn.execute(delete(t).where(t.c.case_id == case_id))
            conn.execute(delete(schema.cases).where(schema.cases.c.id == case_id))

    # ------------------------------------------------------------------ evidence
    def add_evidence(self, case_id: int, ev: CaseEvidence) -> CaseEvidence:
        values = ev.model_dump(exclude={"id", "case_id"})
        with self._repo.transaction() as conn:
            _case_row(conn, case_id)
            pk = conn.execute(
                insert(schema.case_evidence).values(case_id=case_id, **values)
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            _touch(conn, case_id)
            return self._evidence(conn, case_id, int(pk[0]))

    def _evidence(self, conn: Connection, case_id: int, evidence_id: int) -> CaseEvidence:
        t = schema.case_evidence
        row = conn.execute(select(t).where(t.c.id == evidence_id, t.c.case_id == case_id))
        m = row.mappings().first()
        if m is None:
            raise RecordNotFoundError(f"evidence {evidence_id} not found in case {case_id}")
        return CaseEvidence(**m)

    def get_evidence(self, case_id: int, evidence_id: int) -> CaseEvidence:
        with self._repo.transaction() as conn:
            _case_row(conn, case_id)
            return self._evidence(conn, case_id, evidence_id)

    def list_evidence(self, case_id: int, kind: str | None = None) -> list[CaseEvidence]:
        t = schema.case_evidence
        q = select(t).where(t.c.case_id == case_id).order_by(t.c.id)
        if kind is not None:
            q = q.where(t.c.kind == kind)
        with self._repo.transaction() as conn:
            _case_row(conn, case_id)
            return [CaseEvidence(**r) for r in conn.execute(q).mappings()]

    def delete_evidence(self, case_id: int, evidence_id: int) -> None:
        with self._repo.transaction() as conn:
            self._evidence(conn, case_id, evidence_id)
            for h in conn.execute(
                select(schema.case_hypotheses).where(schema.case_hypotheses.c.case_id == case_id)
            ).mappings():
                if evidence_id in json.loads(h["evidence_ids_json"]):
                    raise InvalidRecordError(
                        f"evidence {evidence_id} is cited by hypothesis {h['id']}"
                    )
            conn.execute(
                update(schema.case_leads)
                .where(schema.case_leads.c.source_evidence_id == evidence_id)
                .values(source_evidence_id=None)
            )
            conn.execute(
                delete(schema.case_evidence).where(schema.case_evidence.c.id == evidence_id)
            )
            _touch(conn, case_id)

    # ------------------------------------------------------------------ hypotheses
    def _check_evidence_ids(self, conn: Connection, case_id: int, ids: Sequence[int]) -> None:
        if not ids:
            return
        t = schema.case_evidence
        found = {
            int(r[0])
            for r in conn.execute(select(t.c.id).where(t.c.case_id == case_id, t.c.id.in_(ids)))
        }
        missing = sorted(set(ids) - found)
        if missing:
            raise InvalidRecordError(f"unknown evidence ids for this case: {missing}")

    def add_hypothesis(self, case_id: int, h: CaseHypothesis) -> CaseHypothesis:
        with self._repo.transaction() as conn:
            _case_row(conn, case_id)
            self._check_evidence_ids(conn, case_id, h.evidence_ids)
            values = h.model_dump(exclude={"id", "case_id", "evidence_ids"})
            pk = conn.execute(
                insert(schema.case_hypotheses).values(
                    case_id=case_id, evidence_ids_json=json.dumps(list(h.evidence_ids)), **values
                )
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            _touch(conn, case_id)
            return self._hypothesis(conn, case_id, int(pk[0]))

    def _hypothesis(self, conn: Connection, case_id: int, hid: int) -> CaseHypothesis:
        t = schema.case_hypotheses
        m = conn.execute(select(t).where(t.c.id == hid, t.c.case_id == case_id)).mappings().first()
        if m is None:
            raise RecordNotFoundError(f"hypothesis {hid} not found in case {case_id}")
        return _hyp(m)

    def list_hypotheses(self, case_id: int) -> list[CaseHypothesis]:
        t = schema.case_hypotheses
        with self._repo.transaction() as conn:
            _case_row(conn, case_id)
            rows = conn.execute(select(t).where(t.c.case_id == case_id).order_by(t.c.id)).mappings()
            return [_hyp(r) for r in rows]

    def update_hypothesis(
        self,
        case_id: int,
        hid: int,
        *,
        status: str | None,
        confidence: float | None,
        evidence_ids: tuple[int, ...] | None,
    ) -> CaseHypothesis:
        with self._repo.transaction() as conn:
            cur = self._hypothesis(conn, case_id, hid)
            ids = cur.evidence_ids if evidence_ids is None else evidence_ids
            self._check_evidence_ids(conn, case_id, ids)
            merged: dict[str, Any] = {
                **cur.model_dump(),
                "status": status or cur.status,
                "confidence": cur.confidence if confidence is None else confidence,
                "evidence_ids": ids,
                "updated_at": utcnow(),
            }
            try:  # re-validates: a supported hypothesis must cite evidence
                new = CaseHypothesis(**merged)
            except ValueError as exc:
                raise InvalidRecordError(str(exc).splitlines()[-1]) from None
            conn.execute(
                update(schema.case_hypotheses)
                .where(schema.case_hypotheses.c.id == hid)
                .values(
                    status=new.status,
                    confidence=new.confidence,
                    evidence_ids_json=json.dumps(list(new.evidence_ids)),
                    updated_at=new.updated_at,
                )
            )
            _touch(conn, case_id)
            return self._hypothesis(conn, case_id, hid)

    # ------------------------------------------------------------------ leads
    def add_lead(self, case_id: int, lead: CaseLead) -> CaseLead:
        with self._repo.transaction() as conn:
            _case_row(conn, case_id)
            if lead.source_evidence_id is not None:
                self._evidence(conn, case_id, lead.source_evidence_id)
            pk = conn.execute(
                insert(schema.case_leads).values(
                    case_id=case_id, **lead.model_dump(exclude={"id", "case_id"})
                )
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            _touch(conn, case_id)
            return self._lead(conn, case_id, int(pk[0]))

    def _lead(self, conn: Connection, case_id: int, lid: int) -> CaseLead:
        t = schema.case_leads
        m = conn.execute(select(t).where(t.c.id == lid, t.c.case_id == case_id)).mappings().first()
        if m is None:
            raise RecordNotFoundError(f"lead {lid} not found in case {case_id}")
        return CaseLead(**m)

    def list_leads(self, case_id: int, status: str | None = None) -> list[CaseLead]:
        t = schema.case_leads
        q = select(t).where(t.c.case_id == case_id).order_by(t.c.priority, t.c.id)
        if status is not None:
            q = q.where(t.c.status == status)
        with self._repo.transaction() as conn:
            _case_row(conn, case_id)
            return [CaseLead(**r) for r in conn.execute(q).mappings()]

    def update_lead(
        self, case_id: int, lid: int, *, status: str | None, priority: int | None
    ) -> CaseLead:
        values: dict[str, Any] = {"updated_at": utcnow()}
        if status is not None:
            values["status"] = status
        if priority is not None:
            values["priority"] = priority
        with self._repo.transaction() as conn:
            self._lead(conn, case_id, lid)
            conn.execute(
                update(schema.case_leads).where(schema.case_leads.c.id == lid).values(**values)
            )
            _touch(conn, case_id)
            return self._lead(conn, case_id, lid)
