"""Human review of discovery candidates (Stage 3, schema v18).

A candidate is a signal. Its *machine* status (open, dismissed, promoted, invalidated, expired)
stays as the detectors and reorg handling set it; its *review* state is the latest human decision
in ``signal_decisions`` (``new`` when nobody has decided). Every decision needs a reason code, and
``other`` also needs a note. Accepting a candidate records a judgement about it: it never turns the
candidate into a verified fact. Any state may follow any other (a person can reopen an archived
candidate), but never the same state twice in a row.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, insert, select, update

from bip.db import schema
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow

STATES = (
    "new",
    "pending_review",
    "accepted",
    "rejected",
    "monitoring",
    "investigating",
    "archived",
)
REASONS = (
    "evidence_sufficient",
    "evidence_insufficient",
    "needs_more_data",
    "false_positive",
    "duplicate",
    "known_entity",
    "out_of_scope",
    "dismissed",
    "promoted_to_watchlist",
    "promoted_to_investigation",
    "reopened",
    "other",
)
DECISION_STATES = tuple(s for s in STATES if s != "new")


class ReviewService:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def state(self, signal_id: int) -> str:
        d = schema.signal_decisions
        with self._repo.transaction() as conn:
            row = conn.execute(
                select(d.c.to_state)
                .where(d.c.signal_id == signal_id)
                .order_by(d.c.id.desc())
                .limit(1)
            ).first()
        return "new" if row is None else str(row[0])

    def states(self, signal_ids: list[int]) -> dict[int, str]:
        """Review state of several signals in one query."""
        if not signal_ids:
            return {}
        d = schema.signal_decisions
        latest = (
            select(d.c.signal_id, func.max(d.c.id).label("last"))
            .where(d.c.signal_id.in_(signal_ids))
            .group_by(d.c.signal_id)
            .subquery()
        )
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(d.c.signal_id, d.c.to_state).join(latest, d.c.id == latest.c.last)
            ).all()
        out = dict.fromkeys(signal_ids, "new")
        out.update({int(sid): str(state) for sid, state in rows})
        return out

    def ids_in_state(self, state: str, limit: int = 500) -> list[int]:
        """Signals whose latest decision is ``state`` (for ``new``, those with no decision)."""
        d, s = schema.signal_decisions, schema.signals
        latest = (
            select(d.c.signal_id, func.max(d.c.id).label("last")).group_by(d.c.signal_id).subquery()
        )
        with self._repo.transaction() as conn:
            if state == "new":
                rows = conn.execute(
                    select(s.c.id)
                    .where(s.c.id.not_in(select(d.c.signal_id)))
                    .order_by(s.c.id.desc())
                    .limit(limit)
                ).all()
            else:
                rows = conn.execute(
                    select(d.c.signal_id)
                    .join(latest, d.c.id == latest.c.last)
                    .where(d.c.to_state == state)
                    .order_by(d.c.signal_id.desc())
                    .limit(limit)
                ).all()
        return [int(r[0]) for r in rows]

    def history(self, signal_id: int) -> list[dict[str, Any]]:
        d = schema.signal_decisions
        with self._repo.transaction() as conn:
            rows = (
                conn.execute(select(d).where(d.c.signal_id == signal_id).order_by(d.c.id))
                .mappings()
                .all()
            )
        return [
            {
                "id": r["id"],
                "from_state": r["from_state"],
                "to_state": r["to_state"],
                "reason_code": r["reason_code"],
                "note": r["note"],
                "actor": r["actor"],
                "decided_at": r["decided_at"],
            }
            for r in rows
        ]

    def decide(
        self, signal_id: int, to_state: str, reason_code: str, note: str | None, actor: str
    ) -> dict[str, Any]:
        if to_state not in DECISION_STATES:
            raise InvalidRecordError(f"review state must be one of {', '.join(DECISION_STATES)}")
        if reason_code not in REASONS:
            raise InvalidRecordError(f"reason_code must be one of {', '.join(REASONS)}")
        clean = (note or "").strip()
        if reason_code == "other" and not clean:
            raise InvalidRecordError("a note is required when the reason is 'other'")
        s, d = schema.signals, schema.signal_decisions
        with self._repo.transaction() as conn:
            # Take SQLite's write lock before reading the last decision, so two concurrent
            # decisions cannot both start from the same state.
            hit = conn.execute(update(s).where(s.c.id == signal_id).values(id=s.c.id))
            if not hit.rowcount:
                raise RecordNotFoundError(f"signal {signal_id} not found")
            last = conn.execute(
                select(d.c.to_state)
                .where(d.c.signal_id == signal_id)
                .order_by(d.c.id.desc())
                .limit(1)
            ).first()
            current = "new" if last is None else str(last[0])
            if current == to_state:
                raise InvalidRecordError(f"the candidate is already {to_state}")
            conn.execute(
                insert(d).values(
                    signal_id=signal_id,
                    from_state=current,
                    to_state=to_state,
                    reason_code=reason_code,
                    note=clean[:500] or None,
                    actor=actor[:80],
                    decided_at=utcnow(),
                )
            )
        return {
            "signal_id": signal_id,
            "review_state": to_state,
            "history": self.history(signal_id),
        }

    def record(
        self, signal_id: int, to_state: str, reason_code: str, note: str | None, actor: str
    ) -> None:
        """Record a decision implied by another action (dismiss, promote); a repeat is ignored."""
        if self.state(signal_id) == to_state:
            return
        try:
            self.decide(signal_id, to_state, reason_code, note, actor)
        except InvalidRecordError:
            return  # a concurrent decision reached the same state first; the action stands
