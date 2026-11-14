"""Reviewed entity labels: who an address belongs to, only with traceable evidence and a human.

A label attributes an address, contract, token, pool or program to an *organisation or protocol*
(exchange, bridge, market maker, ...). It is never an attribution to a person.

Rules, enforced here and not only in the UI:

* A label starts ``proposed``. Analysts, signals and an AI model can propose; none of them can
  approve their own proposal as ground truth.
* Approval needs a human reviewer, a written review note, and at least one evidence item added by
  a human (a public ``url``, a stored ``transaction`` or a stored ``signal``). Evidence the AI
  supplied does not count on its own. When authentication is required the reviewer must also be a
  different user than the proposer.
* Transaction and signal evidence must exist in BIP's own database; an AI suggestion that cites
  something that is not stored is dropped, not stored.
* Only ``approved`` labels are shown as attribution (for example on signals).
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, func, insert, select, update
from sqlalchemy.engine import Connection

from bip.db import schema
from bip.db.errors import DuplicateRecordError, InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository, canonical_address
from bip.db.types import utcnow

CATEGORIES = ("exchange", "protocol", "bridge", "market_maker", "treasury", "team", "other")
ENTITY_KINDS = ("wallet", "contract", "token", "pool", "program")
EVIDENCE_KINDS = ("url", "transaction", "signal", "note")
HUMAN_EVIDENCE = ("url", "transaction", "signal")
MAX_EVIDENCE = 10
MIN_REVIEW_NOTE = 10
_URL = re.compile(r"^https?://[^\s]{4,290}$")
SCOPE = (
    "Labels attribute an address to an organisation or protocol, never to a person. Only approved "
    "labels count as attribution; a proposal is a claim waiting for a human to check its evidence."
)

AI_SYSTEM_PROMPT = (
    "You help an analyst review a blockchain entity. You are given stored evidence only. Reply "
    'with JSON: {"suggestions": [{"category": one of '
    + "/".join(CATEGORIES)
    + ', "name": short organisation or protocol name, "rationale": one sentence, '
    '"evidence": [{"kind": "signal" or "transaction", "ref": id from the evidence '
    "given}]}]}. Suggest nothing unless the evidence supports it. Never name a person. Never "
    "invent identifiers. An empty list is a good answer."
)


def _cited(suggestion: dict[str, Any], allowed_signals: set[str]) -> list[dict[str, Any]]:
    """The evidence a model suggestion cites. Only signals that were in the bundle it was given
    count: a model may not cite stored items it was never shown."""
    raw = suggestion.get("evidence", [])
    out: list[dict[str, Any]] = []
    for item in cast("list[Any]", raw) if isinstance(raw, list) else []:
        if isinstance(item, dict):
            d = cast("dict[str, Any]", item)
            if d.get("kind") == "signal" and str(d.get("ref")) in allowed_signals:
                out.append(
                    {"kind": "signal", "ref": str(d["ref"]), "description": "cited by the model"}
                )
    return out


def _is_human(actor: str) -> bool:
    return not actor.startswith(("ai:", "signal:"))


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "chain": r["chain"],
        "ref": r["ref"],
        "entity_kind": r["entity_kind"],
        "category": r["category"],
        "name": r["name"],
        "status": r["status"],
        "source": r["source"],
        "proposed_by": r["proposed_by"],
        "requested_by": r["requested_by"],
        "evidence": json.loads(r["evidence_json"]),
        "note": r["note"],
        "reviewed_by": r["reviewed_by"],
        "review_note": r["review_note"],
        "created_at": _iso(r["created_at"]),
        "reviewed_at": _iso(r["reviewed_at"]),
    }


def approved_for(
    repo: Repository, chain: str, refs: Iterable[str]
) -> dict[str, list[dict[str, Any]]]:
    """Approved labels for the given references, by reference (nothing else is attribution)."""
    wanted = sorted({r for r in refs if r})[:400]
    if not wanted:
        return {}
    t = schema.entity_labels
    out: dict[str, list[dict[str, Any]]] = {}
    with repo.transaction() as conn:
        for r in conn.execute(
            select(t).where(t.c.chain == chain, t.c.status == "approved", t.c.ref.in_(wanted))
        ).mappings():
            out.setdefault(str(r["ref"]), []).append(
                {
                    "name": r["name"],
                    "category": r["category"],
                    "label_id": r["id"],
                    "reviewed_by": r["reviewed_by"],
                    "reviewed_at": _iso(r["reviewed_at"]),
                    "evidence": json.loads(r["evidence_json"]),
                }
            )
    return out


class EntityLabelService:
    def __init__(
        self,
        repo: Repository,
        *,
        clock: Callable[[], datetime] = utcnow,
        separate_reviewer: bool = False,
        ai_concurrency: int = 2,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._separate = separate_reviewer
        self._ai_slots = threading.BoundedSemaphore(max(1, ai_concurrency))

    # ------------------------------------------------------------------ validation

    def _evidence(
        self, chain: str, items: Sequence[dict[str, Any]], actor: str
    ) -> list[dict[str, Any]]:
        if len(items) > MAX_EVIDENCE:
            raise InvalidRecordError(f"at most {MAX_EVIDENCE} evidence items")
        out: list[dict[str, Any]] = []
        for raw in items:
            kind, ref = str(raw.get("kind", "")), str(raw.get("ref", "")).strip()
            if kind not in EVIDENCE_KINDS:
                raise InvalidRecordError(
                    f"evidence kind must be one of {', '.join(EVIDENCE_KINDS)}"
                )
            if not ref or len(ref) > 300:
                raise InvalidRecordError("evidence needs a reference of 1 to 300 characters")
            if kind == "url" and not _URL.match(ref):
                raise InvalidRecordError("a url evidence item must be an http(s) address")
            out.append(
                {
                    "kind": kind,
                    "ref": ref,
                    "description": str(raw.get("description") or "")[:300],
                    "added_by": actor,
                    # None: nothing was checked (a url is never fetched; a note is free text)
                    "verified_stored": self._stored(chain, kind, ref)
                    if kind in ("transaction", "signal")
                    else None,
                }
            )
        for item in out:
            if item["kind"] in ("transaction", "signal") and not item["verified_stored"]:
                raise InvalidRecordError(
                    f"{item['kind']} evidence {item['ref']!r} is not stored in BIP"
                )
        return out

    def _stored(self, chain: str, kind: str, ref: str) -> bool:
        with self._repo.transaction() as conn:
            if kind == "transaction":
                t = schema.transactions
                return (
                    conn.execute(select(t.c.id).where(t.c.chain == chain, t.c.tx_id == ref)).first()
                    is not None
                )
            if kind == "signal":
                s = schema.signals
                return (
                    ref.isascii()
                    and ref.isdecimal()
                    and conn.execute(
                        select(s.c.id).where(s.c.id == int(ref), s.c.chain == chain)
                    ).first()
                    is not None
                )
        return kind in ("url", "note")

    # ------------------------------------------------------------------ propose / review

    def propose(
        self,
        *,
        chain: str,
        ref: str,
        entity_kind: str,
        category: str,
        name: str,
        evidence: Sequence[dict[str, Any]],
        actor: str,
        source: str = "analyst",
        note: str | None = None,
        requested_by: str | None = None,
    ) -> dict[str, Any]:
        if entity_kind not in ENTITY_KINDS:
            raise InvalidRecordError(f"entity_kind must be one of {', '.join(ENTITY_KINDS)}")
        if category not in CATEGORIES:
            raise InvalidRecordError(f"category must be one of {', '.join(CATEGORIES)}")
        name = unicodedata.normalize("NFKC", name).strip()
        if not 1 <= len(name) <= 80:
            raise InvalidRecordError("name needs 1 to 80 characters")
        if any(unicodedata.category(c).startswith("C") for c in name):
            raise InvalidRecordError("name must not contain control or invisible characters")
        address = canonical_address(chain, ref)
        items = self._evidence(address.chain, evidence, actor)
        t = schema.entity_labels
        with self._repo.transaction() as conn:
            existing = conn.execute(
                select(t.c.id, t.c.status).where(
                    t.c.chain == address.chain,
                    t.c.ref == address.address,
                    t.c.category == category,
                    t.c.name == name,
                )
            ).first()
            if existing is not None and existing.status != "rejected":
                raise DuplicateRecordError(f"this label is already {existing.status}")
            if existing is not None:  # a rejected label may be proposed again with new evidence
                conn.execute(
                    update(t)
                    .where(t.c.id == existing.id)
                    .values(
                        status="proposed",
                        source=source,
                        proposed_by=actor,
                        requested_by=requested_by,
                        evidence_json=json.dumps(items),
                        note=(note or None) and note[:500],
                        reviewed_by=None,
                        review_note=None,
                        reviewed_at=None,
                        created_at=self._clock(),
                    )
                )
                label_id = int(existing.id)
                self._event(conn, label_id, "reproposed", actor, note, len(items))
            else:
                pk = conn.execute(
                    insert(t).values(
                        chain=address.chain,
                        ref=address.address,
                        entity_kind=entity_kind,
                        category=category,
                        name=name,
                        status="proposed",
                        source=source,
                        proposed_by=actor,
                        requested_by=requested_by,
                        evidence_json=json.dumps(items),
                        note=(note or None) and note[:500],
                        created_at=self._clock(),
                    )
                ).inserted_primary_key
                assert pk is not None  # noqa: S101
                label_id = int(pk[0])
                self._event(conn, label_id, "proposed", actor, note, len(items))
        return self.get(label_id)

    def review(
        self,
        label_id: int,
        *,
        decision: str,
        reviewer: str,
        note: str,
        evidence: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        if decision not in ("approve", "reject"):
            raise InvalidRecordError("decision must be approve or reject")
        label = self.get(label_id)
        if label["status"] != "proposed":
            raise DuplicateRecordError(f"label {label_id} was already {label['status']}")
        if not _is_human(reviewer):
            raise InvalidRecordError("only a human reviewer can decide a label")
        if len(note.strip()) < MIN_REVIEW_NOTE:
            raise InvalidRecordError(
                f"a review note of at least {MIN_REVIEW_NOTE} characters is required"
            )
        items: list[dict[str, Any]] = list(label["evidence"])
        items += self._evidence(label["chain"], evidence, reviewer)
        if decision == "approve":
            if self._separate and reviewer in (label["proposed_by"], label["requested_by"]):
                raise InvalidRecordError(
                    "the proposer, or the person who asked the model for it, cannot approve it"
                )
            if not any(
                e["kind"] in HUMAN_EVIDENCE and _is_human(str(e["added_by"])) for e in items
            ):
                raise InvalidRecordError(
                    "approval needs at least one evidence item (url, transaction or signal) "
                    "added by a person; AI-supplied evidence is not enough"
                )
        t = schema.entity_labels
        with self._repo.transaction() as conn:
            changed = conn.execute(
                update(t)
                .where(t.c.id == label_id, t.c.status == "proposed")
                .values(
                    status="approved" if decision == "approve" else "rejected",
                    reviewed_by=reviewer,
                    review_note=note.strip()[:500],
                    reviewed_at=self._clock(),
                    evidence_json=json.dumps(items),
                )
            )
            if changed.rowcount != 1:  # another review decided it between our read and update
                raise DuplicateRecordError(f"label {label_id} was already decided")
            event = "approved" if decision == "approve" else "rejected"
            self._event(conn, label_id, event, reviewer, note.strip(), len(items))
        return self.get(label_id)

    def _event(
        self,
        conn: Connection,
        label_id: int,
        event: str,
        actor: str,
        note: str | None,
        evidence_count: int,
    ) -> None:
        conn.execute(
            insert(schema.entity_label_events).values(
                label_id=label_id,
                event=event,
                actor=actor[:80],
                note=(note or None) and note[:500],
                evidence_count=evidence_count,
                at=self._clock(),
            )
        )

    # ------------------------------------------------------------------ reads

    def get(self, label_id: int) -> dict[str, Any]:
        """The label with its append-only history (proposals, re-proposals, decisions)."""
        t, ev = schema.entity_labels, schema.entity_label_events
        with self._repo.transaction() as conn:
            row = conn.execute(select(t).where(t.c.id == label_id)).mappings().first()
            if row is None:
                raise RecordNotFoundError(f"label {label_id} not found")
            events = conn.execute(
                select(ev).where(ev.c.label_id == label_id).order_by(ev.c.id)
            ).all()
        history = [
            {
                "event": h.event,
                "actor": h.actor,
                "note": h.note,
                "evidence_count": h.evidence_count,
                "at": h.at.isoformat(),
            }
            for h in events
        ]
        return {**_row(row), "history": history}

    def list(
        self,
        *,
        status: str | None = None,
        chain: str | None = None,
        ref: str | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        t = schema.entity_labels
        where: list[ColumnElement[bool]] = []
        if status:
            where.append(t.c.status == status)
        if chain:
            where.append(t.c.chain == chain)
        if ref:
            where.append(t.c.ref == ref)
        if category:
            where.append(t.c.category == category)
        with self._repo.transaction() as conn:
            total = int(
                conn.execute(select(func.count()).select_from(t).where(*where)).scalar_one()
            )
            rows = (
                conn.execute(
                    select(t).where(*where).order_by(t.c.id.desc()).limit(limit).offset(offset)
                )
                .mappings()
                .all()
            )
        return {"total": total, "items": [_row(r) for r in rows], "scope": SCOPE}

    def for_entity(self, chain: str, ref: str) -> dict[str, Any]:
        address = canonical_address(chain, ref)
        page = self.list(chain=address.chain, ref=address.address, limit=200)
        approved = [x for x in page["items"] if x["status"] == "approved"]
        return {
            "chain": address.chain,
            "ref": address.address,
            "approved": approved,
            "proposed": sum(1 for x in page["items"] if x["status"] == "proposed"),
            "scope": SCOPE,
        }

    # ------------------------------------------------------------------ AI proposals

    def suggest(
        self,
        provider: Any,
        *,
        chain: str,
        ref: str,
        entity_kind: str,
        actor: str,
        timeout_s: float = 30.0,
        max_bytes: int = 16_384,
    ) -> dict[str, Any]:
        """One suggestion at a time per ``ai.max_concurrent``: the model is a shared local resource
        and a request thread waits on it, so a burst must not exhaust the pool."""
        if not self._ai_slots.acquire(blocking=False):
            raise DuplicateRecordError("a label suggestion is already running; try again shortly")
        try:
            return self._suggest(
                provider,
                chain=chain,
                ref=ref,
                entity_kind=entity_kind,
                actor=actor,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
            )
        finally:
            self._ai_slots.release()

    def _suggest(
        self,
        provider: Any,
        *,
        chain: str,
        ref: str,
        entity_kind: str,
        actor: str,
        timeout_s: float,
        max_bytes: int,
    ) -> dict[str, Any]:
        """Ask the configured model to propose labels from stored evidence; keep only grounded ones.

        The model sees stored signals about the entity, nothing else. Every suggestion must cite
        evidence that exists in the database and must name an allowed category; the rest is
        dropped and counted. What is kept is stored as ``proposed``, never as approved.
        """
        status = provider.status()
        if not status.available:
            return {"available": False, "reason": status.reason, "created": [], "dropped": []}
        address = canonical_address(chain, ref)
        s = schema.signals
        with self._repo.transaction() as conn:
            rows = (
                conn.execute(
                    select(s.c.id, s.c.title, s.c.detector_id, s.c.explanation_json)
                    .where(s.c.chain == address.chain, s.c.subject_ref == address.address)
                    .order_by(s.c.id.desc())
                    .limit(10)
                )
                .mappings()
                .all()
            )
        bundle = [
            {"signal_id": r["id"], "detector": r["detector_id"], "title": r["title"]} for r in rows
        ]
        user = json.dumps(
            {
                "entity": {"chain": address.chain, "ref": address.address, "kind": entity_kind},
                "stored_signals": bundle,
            }
        )
        allowed = {str(r["id"]) for r in rows}
        raw = provider.generate(AI_SYSTEM_PROMPT, user, timeout_s=timeout_s, max_bytes=max_bytes)
        try:
            parsed: Any = json.loads(raw)
            suggestions: list[Any] = (
                list(cast("dict[str, Any]", parsed)["suggestions"])
                if isinstance(parsed, dict)
                else []
            )
        except (ValueError, KeyError, TypeError):
            return {
                "available": True,
                "reason": "unparseable model output",
                "created": [],
                "dropped": [],
            }
        created: list[dict[str, Any]] = []
        dropped: list[dict[str, str]] = []
        candidates = suggestions[:5]
        for item in candidates:
            sug = cast("dict[str, Any]", item) if isinstance(item, dict) else {}
            try:
                evidence = _cited(sug, allowed)
                if not evidence:
                    raise InvalidRecordError("no evidence from the bundle cited")
                name = sug.get("name")
                category = sug.get("category")
                if not isinstance(name, str) or not isinstance(category, str):
                    raise InvalidRecordError("a suggestion needs a text name and category")
                created.append(
                    self.propose(
                        chain=address.chain,
                        ref=address.address,
                        entity_kind=entity_kind,
                        category=category,
                        name=name,
                        evidence=evidence,
                        actor=f"ai:{provider.name}",
                        source="ai",
                        requested_by=actor,
                        note=str(sug.get("rationale") or "")[:500] or None,
                    )
                )
            except (InvalidRecordError, DuplicateRecordError) as exc:
                dropped.append({"reason": str(exc)[:200]})
        return {
            "available": True,
            "reason": "proposals only: a person must approve each with their own evidence",
            "created": created,
            "dropped": dropped,
        }
