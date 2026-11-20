"""Human-in-the-loop curation: analyst verdicts on model classifications and pattern matches.

* Feedback is append-only (``curation_feedback``) and freezes a snapshot of what the analyst
  was shown, so each verdict keeps its provenance.
* Accept/reject of a *classification label* also writes an analyst label (``wallet_labels``,
  status ``analyst_reviewed``, source ``analyst_curation:<reviewer>``). The model prediction
  itself is never written as a label (no automatic self-training). ``needs_evidence`` writes
  no label.
* Pattern-match feedback (case evidence produced by behaviour rules) is stored as a signal
  only; it never creates wallet labels.
* The active-learning queue lists the least certain, not-yet-reviewed predictions first.
  Ranking is a prioritisation aid, not a statement about truth.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select

from bip.db import schema
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.ml import taxonomy
from bip.ml.labels import LabelIn, LabelStore

J = dict[str, Any]
Decision = Literal["accepted", "rejected", "needs_evidence"]


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_type: Literal["classification_label", "pattern_match"]
    target_key: Annotated[str, Field(min_length=1, max_length=64)]
    """Classification id (classification_label) or case evidence id (pattern_match)."""
    label: Annotated[str, Field(pattern=r"^[a-z_]{3,48}$")] | None = None
    """Required for classification_label: which predicted label is being judged."""
    decision: Decision
    reviewer: Annotated[str, Field(min_length=1, max_length=80)]
    note: Annotated[str, Field(max_length=500)] | None = None


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _row(r: Any) -> J:
    d = {k: r[k] for k in r if k not in ("snapshot_json", "created_at")}
    d["snapshot"] = json.loads(r["snapshot_json"])
    d["created_at"] = _iso(r["created_at"])
    return d


def _predictions(payload: J) -> list[J]:
    out = [payload["account_type"], *payload.get("behavioural_roles", [])]
    return [p for p in out if p]


class CurationStore:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------ record
    def record(self, fb: FeedbackIn) -> J:
        if fb.target_type == "classification_label":
            return self._record_classification(fb)
        return self._record_pattern(fb)

    def _record_classification(self, fb: FeedbackIn) -> J:
        if fb.label is None:
            raise InvalidRecordError("label is required for a classification_label verdict")
        c = schema.wallet_classifications
        with self._repo.transaction() as conn:
            row = conn.execute(
                select(c.c.payload_json).where(c.c.classification_key == fb.target_key)
            ).first()
        if row is None:
            raise RecordNotFoundError(f"classification {fb.target_key} not found")
        payload: J = json.loads(row[0])
        pred = next((p for p in _predictions(payload) if p["label"] == fb.label), None)
        if pred is None:
            raise InvalidRecordError(
                f"label {fb.label!r} was not assigned by classification {fb.target_key}"
            )
        snapshot: J = {
            "prediction": pred,
            "classification_id": fb.target_key,
            "as_of": payload["as_of"],
            "feature_digest": payload["feature_digest"],
            "taxonomy_version": payload["taxonomy_version"],
            "model": payload.get("model"),
            "data_origin": payload["data_origin"],
            "supporting_evidence_refs": payload.get("supporting_evidence_refs", []),
        }
        label_id: int | None = None
        if fb.decision in ("accepted", "rejected"):
            label_id = self._write_training_label(fb, payload)
        return self._insert(
            fb,
            chain=payload["chain"],
            address=payload["wallet_address"],
            label=fb.label,
            model_ref=pred["source"],
            model_confidence=pred.get("confidence"),
            snapshot=snapshot,
            label_id=label_id,
        )

    def _write_training_label(self, fb: FeedbackIn, payload: J) -> int | None:
        """Analyst label for accept/reject; ``None`` when the label cannot be a training label
        for this chain (e.g. not in the taxonomy). Duplicate verdicts reuse the stored row."""
        assert fb.label is not None  # noqa: S101
        try:
            spec = taxonomy.get(fb.label)
        except KeyError:
            return None
        if payload["chain"] not in spec.chains:
            return None
        source = f"analyst_curation:{fb.reviewer}"[:120]
        as_of = datetime.fromisoformat(payload["as_of"])
        LabelStore(self._repo).add(
            [
                LabelIn(
                    chain=payload["chain"],
                    address=payload["wallet_address"],
                    label=fb.label,
                    value=fb.decision == "accepted",
                    status="analyst_reviewed",
                    source=source,
                    source_ref=fb.target_key,
                    observed_at=as_of,
                    note=fb.note,
                )
            ]
        )
        t = schema.wallet_labels
        with self._repo.transaction() as conn:
            lid = conn.execute(
                select(t.c.id)
                .where(
                    t.c.chain == payload["chain"],
                    t.c.address == self._canonical(payload),
                    t.c.label == fb.label,
                    t.c.source == source,
                    t.c.observed_at == as_of,
                )
                .order_by(t.c.id.desc())
            ).first()
        return int(lid[0]) if lid else None

    @staticmethod
    def _canonical(payload: J) -> str:
        from bip.db.repository import canonical_address

        return canonical_address(payload["chain"], payload["wallet_address"]).address

    def _record_pattern(self, fb: FeedbackIn) -> J:
        if not fb.target_key.isdigit():
            raise InvalidRecordError("pattern_match target_key must be a case evidence id")
        e = schema.case_evidence
        with self._repo.transaction() as conn:
            row = conn.execute(select(e).where(e.c.id == int(fb.target_key))).mappings().first()
        if row is None:
            raise RecordNotFoundError(f"case evidence {fb.target_key} not found")
        payload: J = json.loads(row["payload_json"]) if row["payload_json"] else {}
        name = fb.label or payload.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidRecordError("pattern name unknown: pass label or evidence with a name")
        snapshot: J = {
            "case_id": row["case_id"],
            "evidence_kind": row["kind"],
            "description": row["description"],
            "payload": payload,
            "source_kind": row["source_kind"],
            "source_ref": row["source_ref"],
            "collected_at": _iso(row["collected_at"]),
        }
        return self._insert(
            fb,
            chain=row["chain"],
            address=row["subject_ref"] if row["subject_type"] == "address" else None,
            label=name[:64],
            model_ref=row["source_ref"],
            model_confidence=None,
            snapshot=snapshot,
            label_id=None,
        )

    def _insert(
        self,
        fb: FeedbackIn,
        *,
        chain: str | None,
        address: str | None,
        label: str,
        model_ref: str | None,
        model_confidence: float | None,
        snapshot: J,
        label_id: int | None,
    ) -> J:
        with self._repo.transaction() as conn:
            res = conn.execute(
                insert(schema.curation_feedback).values(
                    target_type=fb.target_type,
                    target_key=fb.target_key,
                    label=label,
                    chain=chain,
                    address=address,
                    decision=fb.decision,
                    reviewer=fb.reviewer,
                    note=fb.note,
                    model_ref=model_ref,
                    model_confidence=model_confidence,
                    snapshot_json=json.dumps(snapshot, default=str),
                    training_label_id=label_id,
                    created_at=utcnow(),
                )
            )
            fid = res.inserted_primary_key[0]
            row = (
                conn.execute(
                    select(schema.curation_feedback).where(schema.curation_feedback.c.id == fid)
                )
                .mappings()
                .one()
            )
        return _row(row)

    # ------------------------------------------------------------------ read
    def list(self, *, target_type: str | None, limit: int, offset: int) -> tuple[list[J], int]:
        t = schema.curation_feedback
        q = select(t).order_by(t.c.id.desc())
        if target_type:
            q = q.where(t.c.target_type == target_type)
        with self._repo.transaction() as conn:
            rows = conn.execute(q).mappings().all()
        return [_row(r) for r in rows[offset : offset + limit]], len(rows)

    def stats(self) -> J:
        items, total = self.list(target_type=None, limit=10**9, offset=0)
        by_decision: dict[str, int] = {}
        per: dict[str, J] = {}
        for r in items:
            by_decision[r["decision"]] = by_decision.get(r["decision"], 0) + 1
            key = f"{r['target_type']}:{r['label']}"
            s = per.setdefault(
                key,
                {
                    "target_type": r["target_type"],
                    "label": r["label"],
                    "accepted": 0,
                    "rejected": 0,
                    "needs_evidence": 0,
                },
            )
            s[r["decision"]] += 1
        for s in per.values():
            judged = s["accepted"] + s["rejected"]
            s["judged"] = judged
            # an analyst-agreement rate, not a measured model precision
            s["agreement_rate"] = round(s["accepted"] / judged, 4) if judged else None
        return {
            "total": total,
            "by_decision": by_decision,
            "per_label": sorted(per.values(), key=lambda x: (x["target_type"], x["label"])),
            "note": "Agreement rate = accepted / (accepted + rejected) among analyst verdicts; "
            "it is a curation signal, not a measured precision.",
        }

    def queue(self, *, limit: int, chain: str | None = None) -> J:
        """Active-learning queue: unreviewed predictions nearest the decision boundary first.

        Rule-based labels carry no score and are skipped; ``needs_evidence`` verdicts keep an
        item queued, accepted/rejected remove it.
        """
        fb = schema.curation_feedback
        c = schema.wallet_classifications
        with self._repo.transaction() as conn:
            done = {
                (r[0], r[1])
                for r in conn.execute(
                    select(fb.c.target_key, fb.c.label).where(
                        fb.c.target_type == "classification_label",
                        fb.c.decision.in_(("accepted", "rejected")),
                    )
                )
            }
            q = select(c.c.payload_json).order_by(c.c.id.desc())
            if chain:
                q = q.where(c.c.chain == chain)
            rows = conn.execute(q).all()
        latest: dict[tuple[str, str], J] = {}
        for (raw,) in rows:  # newest first: keep each wallet's latest classification only
            p: J = json.loads(raw)
            latest.setdefault((p["chain"], p["wallet_address"]), p)
        items: list[J] = []
        for p in latest.values():
            contradicted = {
                lab.split(":", 1)[-1] for x in p.get("contradictions", []) for lab in x["between"]
            }
            for pred in p.get("behavioural_roles", []):
                conf = pred.get("confidence")
                if conf is None or (p["classification_id"], pred["label"]) in done:
                    continue
                uncertainty = round(1 - abs(conf - 0.5) * 2, 4)
                items.append(
                    {
                        "classification_id": p["classification_id"],
                        "chain": p["chain"],
                        "address": p["wallet_address"],
                        "label": pred["label"],
                        "status": pred["status"],
                        "confidence": conf,
                        "source": pred["source"],
                        "model_status": pred.get("model_status"),
                        "uncertainty": uncertainty,
                        "contradicted": pred["label"] in contradicted,
                        "reason": "contradicted by a rule"
                        if pred["label"] in contradicted
                        else "score near the decision boundary"
                        if uncertainty >= 0.5
                        else "low priority: score far from the boundary",
                        "explanation": pred["explanation"],
                        "as_of": p["as_of"],
                    }
                )
        items.sort(key=lambda x: (not x["contradicted"], -x["uncertainty"], x["address"]))
        return {
            "items": items[:limit],
            "total": len(items),
            "note": "Ordering prioritises review effort; it does not indicate which "
            "predictions are correct.",
        }
