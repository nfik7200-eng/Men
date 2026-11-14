"""Human review of discovery candidates and the ground truth it yields (Stage 4, schema v19).

A candidate is a signal. Its *machine* status (open, dismissed, promoted, invalidated, expired)
stays as the detectors and reorg handling set it; its *review* state is the latest human decision
in ``signal_decisions`` (``new`` when nobody has decided). Decisions are append-only: a later
decision supersedes an earlier one, nothing is ever edited or erased.

* **States and moves** follow :data:`TRANSITIONS`. Finished states (confirmed, rejected,
  duplicate, insufficient evidence, archived) can only be reopened, back to ``investigating``,
  with the reason ``reopened``. A second, *different* reviewer may record the opposite outcome of a
  confirmed or rejected candidate (a second opinion), and anyone may settle a disagreement with
  the reason ``dispute_resolved``.
* **Reasons** must fit the state (:data:`STATE_REASONS`); ``other`` needs a note.
* A ``duplicate`` names the candidate it duplicates; a confirmation may carry a wallet label from
  the ML taxonomy and any decision a free-text correction.
* ``accepted`` is the pre-v19 name of ``confirmed``. It is accepted as input and read back as
  ``confirmed``; it is never written.

Ground truth (:meth:`ReviewService.ground_truth`): one human ``confirmed`` (1) or ``rejected`` (0)
decision in the current review round makes a label. Decisions by ``system:``, ``ai:`` or
``signal:`` actors never count. Two reviewers with different outcomes make the candidate
``disputed`` (excluded) until a ``dispute_resolved`` decision. Only databases explicitly marked as
holding real network data (``data_origin = live_network``) yield ground truth; fixtures,
synthetic previews and unmarked databases never do. With sign-in off every decision's actor is
``local``, so a disagreement cannot be recorded there. Confirming a candidate is a judgement
about it, never a verified fact.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection

from bip.core.logging import get_logger
from bip.db import schema
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.ml import taxonomy

log = get_logger(__name__)

DEFINITIONS_VERSION = "1"
"""Version of :data:`DEFINITIONS`; stored on every decision so a label can be read in the meaning
it had when it was given."""

STATES = (
    "new",
    "pending_review",
    "investigating",
    "monitoring",
    "confirmed",
    "rejected",
    "duplicate",
    "insufficient_evidence",
    "archived",
)
FINISHED = ("confirmed", "rejected", "duplicate", "insufficient_evidence", "archived")
OUTCOMES = {"confirmed": 1, "rejected": 0}
"""Review states that are ground-truth labels for a candidate (1 = a real finding)."""

_OPEN_MOVES = (
    "pending_review",
    "investigating",
    "monitoring",
    "confirmed",
    "rejected",
    "duplicate",
    "insufficient_evidence",
    "archived",
)
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "new": _OPEN_MOVES,
    "pending_review": tuple(s for s in _OPEN_MOVES if s != "pending_review"),
    "investigating": tuple(s for s in _OPEN_MOVES if s != "investigating"),
    "monitoring": tuple(s for s in _OPEN_MOVES if s not in ("monitoring", "pending_review")),
    **dict.fromkeys(FINISHED, ("investigating",)),
}
"""Allowed moves. A finished candidate can only be reopened (and a second reviewer may record the
opposite outcome, see the module docstring)."""

STATE_REASONS: dict[str, tuple[str, ...]] = {
    "pending_review": ("needs_more_data", "other"),
    "investigating": ("needs_more_data", "promoted_to_investigation", "reopened", "other"),
    "monitoring": ("promoted_to_watchlist", "needs_more_data", "other"),
    "confirmed": ("evidence_sufficient", "known_entity", "dispute_resolved", "other"),
    "rejected": (
        "false_positive",
        "known_entity",
        "out_of_scope",
        "dismissed",
        "evidence_contradicted",
        "dispute_resolved",
        "other",
    ),
    "duplicate": ("duplicate",),
    "insufficient_evidence": ("evidence_insufficient", "needs_more_data"),
    "archived": ("out_of_scope", "dismissed", "other"),
}
REASONS = tuple(sorted({r for rs in STATE_REASONS.values() for r in rs}))
DECISION_STATES = tuple(s for s in STATES if s != "new")
ALIASES = {"accepted": "confirmed"}

DEFINITIONS: dict[str, str] = {
    "confirmed": (
        "A reviewer checked the evidence and judges the candidate a real instance of what its "
        "detector describes. A judgement, not a verified fact; never an identity claim."
    ),
    "rejected": (
        "A reviewer judges the candidate not a real instance: a false positive, a known benign "
        "entity, out of scope, or contradicted by evidence."
    ),
    "duplicate": "The same finding as another candidate, which is named.",
    "insufficient_evidence": "The stored evidence cannot decide it either way.",
    "disputed": "Reviewers recorded different outcomes; excluded from labels until resolved.",
}

_MACHINE_ACTORS = ("system:", "ai:", "signal:")
GROUND_TRUTH_ORIGINS = frozenset({"live_network"})
"""``data_origin`` values whose decisions may become labels. Anything else, including a missing
marker, is excluded (fail closed)."""


def _check_wallet_label(label_id: str, subject: Any) -> None:
    spec = taxonomy.BY_ID[label_id]
    if str(subject.chain) not in spec.chains:
        raise InvalidRecordError(f"wallet_label {label_id!r} does not apply to {subject.chain}")
    if str(subject.subject_kind) not in ("wallet", "contract"):
        raise InvalidRecordError("a wallet_label needs a wallet or contract subject")


def is_human(actor: str) -> bool:
    return bool(actor) and not actor.startswith(_MACHINE_ACTORS)


def _state(value: str) -> str:
    return ALIASES.get(value, value)


class ReviewService:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------ reads

    def state(self, signal_id: int) -> str:
        d = schema.signal_decisions
        with self._repo.transaction() as conn:
            row = conn.execute(
                select(d.c.to_state)
                .where(d.c.signal_id == signal_id)
                .order_by(d.c.id.desc())
                .limit(1)
            ).first()
        return "new" if row is None else _state(str(row[0]))

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
        out.update({int(sid): _state(str(state)) for sid, state in rows})
        return out

    def ids_in_state(self, state: str, limit: int = 500) -> list[int]:
        """Signals whose latest decision is ``state`` (for ``new``, those with no decision)."""
        state = _state(state)
        d, s = schema.signal_decisions, schema.signals
        latest = (
            select(d.c.signal_id, func.max(d.c.id).label("last")).group_by(d.c.signal_id).subquery()
        )
        stored = ("confirmed", "accepted") if state == "confirmed" else (state,)
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
                    .where(d.c.to_state.in_(stored))
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
        return [_decision(r) for r in rows]

    def definitions(self) -> dict[str, Any]:
        return {
            "version": DEFINITIONS_VERSION,
            "states": list(STATES),
            "finished": list(FINISHED),
            "transitions": {k: list(v) for k, v in TRANSITIONS.items()},
            "reasons": {k: list(v) for k, v in STATE_REASONS.items()},
            "definitions": DEFINITIONS,
            "ground_truth": {
                "rule": "one human confirmed/rejected decision in the current review round",
                "disagreement": "different outcomes from different reviewers: disputed, excluded "
                "until a dispute_resolved decision",
                "never": "system:, ai: and signal: actors; data not marked live_network",
                "sign_in_off": "every reviewer is 'local', so disagreement cannot be recorded",
            },
        }

    # ------------------------------------------------------------------ decisions

    def decide(
        self,
        signal_id: int,
        to_state: str,
        reason_code: str,
        note: str | None,
        actor: str,
        *,
        duplicate_of: int | None = None,
        correction: str | None = None,
        wallet_label: str | None = None,
    ) -> dict[str, Any]:
        to_state = _state(to_state)
        if to_state not in DECISION_STATES:
            raise InvalidRecordError(f"review state must be one of {', '.join(DECISION_STATES)}")
        allowed = STATE_REASONS[to_state]
        if reason_code not in allowed:
            raise InvalidRecordError(
                f"reason_code for {to_state} must be one of {', '.join(allowed)}"
            )
        clean = (note or "").strip()
        if reason_code == "other" and not clean:
            raise InvalidRecordError("a note is required when the reason is 'other'")
        if (duplicate_of is None) == (to_state == "duplicate"):
            raise InvalidRecordError("duplicate_of is required for, and only for, a duplicate")
        if duplicate_of == signal_id:
            raise InvalidRecordError("a candidate cannot duplicate itself")
        if wallet_label is not None:
            if wallet_label not in taxonomy.BY_ID:
                raise InvalidRecordError("wallet_label must be a label id from the ML taxonomy")
            if to_state != "confirmed":
                raise InvalidRecordError("a wallet_label can only accompany a confirmation")
        fix = (correction or "").strip()[:500] or None
        s, d = schema.signals, schema.signal_decisions
        with self._repo.transaction() as conn:
            # Take SQLite's write lock before reading the last decision, so two concurrent
            # decisions cannot both start from the same state.
            hit = conn.execute(update(s).where(s.c.id == signal_id).values(id=s.c.id))
            if not hit.rowcount:
                raise RecordNotFoundError(f"signal {signal_id} not found")
            subject = conn.execute(
                select(s.c.chain, s.c.subject_kind, s.c.subject_ref, s.c.observed_at).where(
                    s.c.id == signal_id
                )
            ).one()
            if wallet_label is not None:
                _check_wallet_label(wallet_label, subject)
            if (
                duplicate_of is not None
                and not conn.execute(select(s.c.id).where(s.c.id == duplicate_of)).first()
            ):
                raise InvalidRecordError(f"duplicate_of: signal {duplicate_of} not found")
            current = self._latest(conn, signal_id)
            self._check_move(
                current, to_state, reason_code, actor, self._truth(conn, signal_id, None)
            )
            conn.execute(
                insert(d).values(
                    signal_id=signal_id,
                    from_state=current,
                    to_state=to_state,
                    reason_code=reason_code,
                    note=clean[:500] or None,
                    actor=actor[:80],
                    decided_at=utcnow(),
                    duplicate_of=duplicate_of,
                    correction=fix,
                    wallet_label=wallet_label,
                    definitions_version=DEFINITIONS_VERSION,
                )
            )
        if wallet_label is not None and is_human(actor):
            self._feed_wallet_label(signal_id, subject, wallet_label, fix)
        # wallet labels follow the confirmation: withdrawn when it ends, reinstated when it
        # returns. The decision is already stored, so a failure here is logged, not raised.
        if current != to_state and "confirmed" in (current, to_state):
            try:
                self._sync_wallet_labels(signal_id, actor, current, to_state)
            except Exception:
                log.exception("wallet labels of signal %s were not updated", signal_id)
        return {
            "signal_id": signal_id,
            "review_state": to_state,
            "history": self.history(signal_id),
            "ground_truth": self.ground_truth(signal_id),
        }

    def _sync_wallet_labels(
        self, signal_id: int, actor: str, from_state: str, to_state: str
    ) -> None:
        """Wallet labels a confirmation fed get a ``rejected`` review when the candidate stops
        being confirmed (they leave the training data) and an ``accepted`` review when it is
        confirmed again by a person. Labels and every review stay on record."""
        leaving = from_state == "confirmed"
        if not leaving and not is_human(actor):
            return
        from bip.ml.labels import LabelStore, ReviewIn

        wl = schema.wallet_labels
        with self._repo.transaction() as conn:
            ids = [
                int(i)
                for i in conn.execute(
                    select(wl.c.id).where(
                        wl.c.source == "signal_review", wl.c.source_ref == f"signal:{signal_id}"
                    )
                ).scalars()
            ]
        store = LabelStore(self._repo)
        for label_id in ids:
            store.review(
                label_id,
                ReviewIn(
                    decision="rejected" if leaving else "accepted",
                    reviewer=actor[:80],
                    note=f"signal {signal_id} moved from {from_state} to {to_state}",
                ),
            )

    def _feed_wallet_label(
        self, signal_id: int, subject: Any, wallet_label: str, correction: str | None
    ) -> None:
        """A person confirmed the candidate and named what the wallet is: store it as an
        ``analyst_reviewed`` wallet label for the classifier. Data not marked as real network
        data is stored as ``synthetic`` so it can never train a real model."""
        from bip.ml.labels import LabelIn, LabelStore

        LabelStore(self._repo).add(
            [
                LabelIn(
                    chain=str(subject.chain),
                    address=str(subject.subject_ref),
                    label=wallet_label,
                    status="analyst_reviewed",
                    source="signal_review",
                    source_ref=f"signal:{signal_id}",
                    observed_at=subject.observed_at,
                    note=correction,
                )
            ],
            data_kind="real" if self._origin_problem() is None else "synthetic",
        )

    @staticmethod
    def _check_move(
        current: str, to_state: str, reason: str, actor: str, truth: dict[str, Any]
    ) -> None:
        """Refuse a move the lifecycle does not allow.

        * ``dispute_resolved`` needs a live disagreement in the current round (``truth`` says
          ``disputed``) and must land on confirmed or rejected.
        * A *second opinion* (the opposite confirmed/rejected outcome without reopening) is open
          only to a reviewer who has not given an outcome in this round.
        * Otherwise :data:`TRANSITIONS` decides; reopening a finished candidate needs
          ``reopened``.
        """
        if reason == "dispute_resolved":
            if truth["quality"] != "disputed" or to_state not in OUTCOMES:
                raise InvalidRecordError(
                    "dispute_resolved settles a live confirmed/rejected disagreement; "
                    "there is none to settle"
                )
            return
        if current == to_state:
            raise InvalidRecordError(f"the candidate is already {to_state}")
        second_opinion = (
            current in OUTCOMES
            and to_state in OUTCOMES
            and is_human(actor)
            and actor[:80] not in truth["reviewers"]
        )
        if to_state not in TRANSITIONS[current] and not second_opinion:
            raise InvalidRecordError(f"a {current} candidate cannot move to {to_state}")
        if current in FINISHED and to_state == "investigating" and reason != "reopened":
            raise InvalidRecordError("to reopen a finished candidate use the reason 'reopened'")

    @staticmethod
    def _latest(conn: Connection, signal_id: int) -> str:
        d = schema.signal_decisions
        row = conn.execute(
            select(d.c.to_state).where(d.c.signal_id == signal_id).order_by(d.c.id.desc()).limit(1)
        ).first()
        return "new" if row is None else _state(str(row[0]))

    def check_record(self, signal_id: int, to_state: str, reason_code: str, actor: str) -> None:
        """Raise if :meth:`record` would refuse this move, so an action (dismiss, promote) is
        refused before it changes anything rather than left without its review record."""
        to_state = _state(to_state)
        with self._repo.transaction() as conn:
            current = self._latest(conn, signal_id)
            if current != to_state:
                self._check_move(
                    current, to_state, reason_code, actor, self._truth(conn, signal_id, None)
                )

    def record(
        self, signal_id: int, to_state: str, reason_code: str, note: str | None, actor: str
    ) -> None:
        """Record a decision implied by another action (dismiss, promote). A repeat of the current
        state is a no-op; a move the transition table refuses raises, so the action is refused
        visibly instead of standing without its review record."""
        if self.state(signal_id) == _state(to_state):
            return
        self.decide(signal_id, to_state, reason_code, note, actor)

    # ------------------------------------------------------------------ ground truth

    def ground_truth(self, signal_id: int) -> dict[str, Any]:
        with self._repo.transaction() as conn:
            return self._truth(conn, signal_id, self._origin_problem())

    def ground_truth_set(self, *, chain: str | None = None, limit: int = 5000) -> dict[str, Any]:
        """Labelled candidates (newest first, bounded) and per-chain label counts. All decisions
        are read in one query and grouped in memory, so the cost does not grow per candidate."""
        d, s = schema.signal_decisions, schema.signals
        problem = self._origin_problem()
        items: list[dict[str, Any]] = []
        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"positive": 0, "negative": 0})
        grouped: dict[int, list[Any]] = defaultdict(list)
        chains: dict[int, str] = {}
        q = (
            select(
                d.c.signal_id, s.c.chain, d.c.to_state, d.c.reason_code, d.c.actor, d.c.decided_at
            )
            .join(s, s.c.id == d.c.signal_id)
            .order_by(d.c.signal_id, d.c.id)
        )
        if chain:
            q = q.where(s.c.chain == chain)
        with self._repo.transaction() as conn:
            for r in conn.execute(q).all():
                grouped[int(r.signal_id)].append(r)
                chains[int(r.signal_id)] = str(r.chain)
        for sid in sorted(grouped, reverse=True):
            truth = _truth_from(grouped[sid], problem)
            if not truth["eligible"]:
                continue
            counts[chains[sid]]["positive" if truth["label"] == 1 else "negative"] += 1
            if len(items) < limit:
                items.append({"signal_id": sid, "chain": chains[sid], **truth})
        return {
            "definitions_version": DEFINITIONS_VERSION,
            "items": items,
            "counts": dict(counts),
            "excluded_because": problem,
        }

    def _origin_problem(self) -> str | None:
        origin = self._repo.get_meta("data_origin")
        if origin in GROUND_TRUTH_ORIGINS:
            return None
        if not origin:
            return "the database is not marked as holding real network data"
        return f"data origin '{origin}' is not real network data"

    @staticmethod
    def _truth(conn: Connection, signal_id: int, origin_problem: str | None) -> dict[str, Any]:
        d = schema.signal_decisions
        rows = conn.execute(
            select(d.c.to_state, d.c.reason_code, d.c.actor, d.c.decided_at)
            .where(d.c.signal_id == signal_id)
            .order_by(d.c.id)
        ).all()
        return _truth_from(rows, origin_problem)


def _truth_from(rows: Sequence[Any], origin_problem: str | None) -> dict[str, Any]:
    """Ground truth from one candidate's decisions (oldest first)."""
    # the current review round starts after the last reopen
    start = max((i + 1 for i, r in enumerate(rows) if r.reason_code == "reopened"), default=0)
    round_rows = rows[start:]
    human = [r for r in round_rows if is_human(str(r.actor))]
    outcomes = [r for r in human if _state(str(r.to_state)) in OUTCOMES]
    machine_only = bool(round_rows) and not human
    label: int | None = None
    quality = "unlabelled"
    reviewers = sorted({str(r.actor) for r in outcomes})
    if outcomes:
        resolved = [r for r in outcomes if r.reason_code == "dispute_resolved"]
        if resolved:
            last = resolved[-1]
            after = outcomes[outcomes.index(last) + 1 :]
            if all(_state(str(r.to_state)) == _state(str(last.to_state)) for r in after):
                label, quality = OUTCOMES[_state(str(last.to_state))], "dispute_resolved"
            else:
                quality = "disputed"
        else:
            latest: dict[str, str] = {}
            for r in outcomes:
                latest[str(r.actor)] = _state(str(r.to_state))
            values = set(latest.values())
            if len(values) == 1:
                label = OUTCOMES[values.pop()]
                quality = "human_consensus" if len(latest) > 1 else "human_review"
            else:
                quality = "disputed"
    excluded = origin_problem
    if excluded is None and machine_only:
        excluded = "decided only by non-human actors"
    if excluded is None and quality == "disputed":
        excluded = "reviewers disagree"
    return {
        "label": label,
        "quality": quality,
        "eligible": label is not None and excluded is None,
        "excluded_because": excluded if (label is None or excluded) else None,
        "reviewers": reviewers,
        "definitions_version": DEFINITIONS_VERSION,
    }


def _decision(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "from_state": _state(str(r["from_state"])),
        "to_state": _state(str(r["to_state"])),
        "stored_state": r["to_state"],
        "reason_code": r["reason_code"],
        "note": r["note"],
        "actor": r["actor"],
        "decided_at": r["decided_at"],
        "duplicate_of": r["duplicate_of"],
        "correction": r["correction"],
        "wallet_label": r["wallet_label"],
        "definitions_version": r["definitions_version"],
    }
