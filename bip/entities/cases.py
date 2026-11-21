"""Investigation notes, hypotheses and the case timeline.

Findings carry evidence. Notes do not, and a *hypothesis* is a note about something not yet shown.
They are stored and shown separately so a reader can always tell what is evidenced from what is a
working theory. The timeline merges everything in time order and labels each entry's kind.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select

from bip.db import schema
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.security.secrets import redact_text

NOTE_KINDS = ("note", "hypothesis")
MAX_BODY = 4_000


class CaseService:
    def __init__(self, repo: Repository, *, clock: Callable[[], datetime] = utcnow) -> None:
        self._repo = repo
        self._clock = clock

    def _require_case(self, investigation_id: int) -> Any:
        i = schema.investigations
        with self._repo.transaction() as conn:
            row = conn.execute(select(i).where(i.c.id == investigation_id)).mappings().first()
        if row is None:
            raise RecordNotFoundError(f"investigation {investigation_id} not found")
        return row

    def add_note(self, investigation_id: int, kind: str, body: str, author: str) -> dict[str, Any]:
        if kind not in NOTE_KINDS:
            raise InvalidRecordError(f"kind must be one of {', '.join(NOTE_KINDS)}")
        text = body.strip()
        if not text or len(text) > MAX_BODY:
            raise InvalidRecordError(f"a note must be 1-{MAX_BODY} characters")
        self._require_case(investigation_id)
        n = schema.investigation_notes
        with self._repo.transaction() as conn:
            pk = conn.execute(
                insert(n).values(
                    investigation_id=investigation_id,
                    kind=kind,
                    body=redact_text(text),
                    author=author[:64],
                    created_at=self._clock(),
                )
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            row = conn.execute(select(n).where(n.c.id == pk[0])).mappings().one()
            conn.execute(
                schema.investigations.update()
                .where(schema.investigations.c.id == investigation_id)
                .values(updated_at=self._clock())
            )
        return self._note(row)

    @staticmethod
    def _note(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "body": row["body"],
            "author": row["author"],
            "created_at": row["created_at"].isoformat(),
            "evidenced": False,
        }

    def notes(
        self, investigation_id: int, *, kind: str | None, limit: int, offset: int
    ) -> dict[str, Any]:
        self._require_case(investigation_id)
        n = schema.investigation_notes
        conds = [n.c.investigation_id == investigation_id]
        if kind:
            if kind not in NOTE_KINDS:
                raise InvalidRecordError(f"kind must be one of {', '.join(NOTE_KINDS)}")
            conds.append(n.c.kind == kind)
        with self._repo.transaction() as conn:
            total = int(
                conn.execute(select(func.count()).select_from(n).where(*conds)).scalar_one()
            )
            rows = (
                conn.execute(
                    select(n).where(*conds).order_by(n.c.id.desc()).limit(limit).offset(offset)
                )
                .mappings()
                .all()
            )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [self._note(r) for r in rows],
        }

    def timeline(self, investigation_id: int, limit: int = 500) -> dict[str, Any]:
        case = self._require_case(investigation_id)
        f, e, n = schema.findings, schema.evidence_refs, schema.investigation_notes
        links = schema.investigation_research_runs
        sg = schema.signals
        events: list[dict[str, Any]] = [
            {
                "at": case["created_at"].isoformat(),
                "type": "case_opened",
                "label": "fact",
                "title": f"Case opened: {case['title']}",
                "ref": None,
            }
        ]
        with self._repo.transaction() as conn:
            for row in conn.execute(
                select(f.c.id, f.c.finding_id, f.c.finding_type, f.c.status, f.c.created_at).where(
                    f.c.investigation_id == investigation_id
                )
            ).all():
                evidence = int(
                    conn.execute(
                        select(func.count()).select_from(e).where(e.c.finding_pk == row.id)
                    ).scalar_one()
                )
                events.append(
                    {
                        "at": row.created_at.isoformat(),
                        "type": "finding",
                        "label": "evidenced",
                        "title": f"Finding {row.finding_type} ({row.status})",
                        "ref": row.finding_id,
                        "evidence_count": evidence,
                    }
                )
            for row in (
                conn.execute(select(n).where(n.c.investigation_id == investigation_id))
                .mappings()
                .all()
            ):
                events.append(
                    {
                        "at": row["created_at"].isoformat(),
                        "type": row["kind"],
                        "label": "hypothesis" if row["kind"] == "hypothesis" else "note",
                        "title": row["body"][:120],
                        "ref": str(row["id"]),
                        "author": row["author"],
                    }
                )
            for row in conn.execute(
                select(links.c.research_run_id, links.c.attached_at).where(
                    links.c.investigation_id == investigation_id
                )
            ).all():
                events.append(
                    {
                        "at": row.attached_at.isoformat(),
                        "type": "research_run_attached",
                        "label": "analysis",
                        "title": "AI research run attached",
                        "ref": str(row.research_run_id),
                    }
                )
            for row in conn.execute(
                select(sg.c.id, sg.c.title, sg.c.first_seen_at, sg.c.status).where(
                    sg.c.promoted_to == f"investigation:{investigation_id}"
                )
            ).all():
                events.append(
                    {
                        "at": row.first_seen_at.isoformat(),
                        "type": "signal_promoted",
                        "label": "candidate",
                        "title": f"Signal promoted: {row.title}",
                        "ref": str(row.id),
                        "signal_status": row.status,
                    }
                )
        events.sort(key=lambda x: x["at"])
        return {
            "investigation_id": investigation_id,
            "status": case["status"],
            "total": len(events),
            "items": events[-limit:],
            "labels": {
                "evidenced": "a finding with evidence references",
                "hypothesis": "a working theory without evidence",
                "note": "an analyst's remark",
                "candidate": "a discovery signal, not yet a finding",
                "analysis": "an automated analysis, not a verified fact",
                "fact": "a recorded event in this case",
            },
        }
