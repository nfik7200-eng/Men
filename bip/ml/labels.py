"""Training-label store with provenance and append-only reviews.

Rules:

* a label must be in the taxonomy, valid for its chain, with a source and a status;
* imported labels are ``data_kind = "real"`` and can never be ``synthetic``-marked silently;
* model predictions are **never** written here (no automatic self-training);
* reviews are appended to ``wallet_label_reviews``; the original row is never edited.
  A ``rejected`` review excludes the label from training; ``accepted`` on an ``inferred`` or
  ``weak`` label makes it usable as ``analyst_reviewed`` (the stored status stays unchanged).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import insert, select

from bip.db import schema
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository, canonical_address
from bip.db.types import utcnow
from bip.ml import taxonomy

J = dict[str, Any]
TRAINABLE = {"verified", "corroborated", "analyst_reviewed"}
"""Label statuses used for training by default (weak/inferred only after an accepted review)."""


class LabelIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chain: Annotated[str, Field(min_length=1, max_length=32)]
    address: Annotated[str, Field(min_length=1, max_length=100)]
    label: Annotated[str, Field(pattern=r"^[a-z_]{3,48}$")]
    value: bool = True
    status: Literal["verified", "corroborated", "analyst_reviewed", "inferred", "weak", "unknown"]
    source: Annotated[str, Field(min_length=2, max_length=120)]
    source_ref: Annotated[str, Field(max_length=500)] | None = None
    observed_at: AwareDatetime
    note: Annotated[str, Field(max_length=500)] | None = None


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["accepted", "rejected", "needs_evidence"]
    reviewer: Annotated[str, Field(min_length=1, max_length=80)]
    note: Annotated[str, Field(max_length=500)] | None = None


class LabelStore:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def add(self, items: list[LabelIn], *, data_kind: str = "real") -> J:
        rows: list[J] = []
        for item in items:
            ref = canonical_address(item.chain, item.address)
            try:
                spec = taxonomy.get(item.label)
            except KeyError:
                raise InvalidRecordError(f"label {item.label!r} is not in the taxonomy") from None
            if ref.chain not in spec.chains:
                raise InvalidRecordError(f"label {item.label!r} does not apply to {ref.chain}")
            rows.append(
                {
                    "chain": ref.chain,
                    "address": ref.address,
                    "dimension": spec.dimension,
                    "label": spec.id,
                    "value": item.value,
                    "status": item.status,
                    "source": item.source,
                    "source_ref": item.source_ref,
                    "observed_at": item.observed_at,
                    "data_kind": data_kind,
                    "note": item.note,
                    "created_at": utcnow(),
                }
            )
        t = schema.wallet_labels
        added = skipped = 0
        with self._repo.transaction() as conn:
            for r in rows:
                exists = conn.execute(
                    select(t.c.id).where(
                        t.c.chain == r["chain"],
                        t.c.address == r["address"],
                        t.c.label == r["label"],
                        t.c.source == r["source"],
                        t.c.observed_at == r["observed_at"],
                    )
                ).first()
                if exists:
                    skipped += 1
                    continue
                conn.execute(insert(t).values(**r))
                added += 1
        return {"added": added, "duplicates_skipped": skipped}

    def review(self, label_id: int, review: ReviewIn) -> J:
        with self._repo.transaction() as conn:
            if (
                conn.execute(
                    select(schema.wallet_labels.c.id).where(schema.wallet_labels.c.id == label_id)
                ).first()
                is None
            ):
                raise RecordNotFoundError(f"label {label_id} not found")
            conn.execute(
                insert(schema.wallet_label_reviews).values(
                    label_id=label_id,
                    decision=review.decision,
                    reviewer=review.reviewer,
                    note=review.note,
                    reviewed_at=utcnow(),
                )
            )
        return {"label_id": label_id, "decision": review.decision}

    def list(
        self, *, chain: str | None, data_kind: str | None, limit: int, offset: int
    ) -> tuple[list[J], int]:
        t = schema.wallet_labels
        q = select(t)
        if chain:
            q = q.where(t.c.chain == chain)
        if data_kind:
            q = q.where(t.c.data_kind == data_kind)
        with self._repo.transaction() as conn:
            rows = conn.execute(q.order_by(t.c.id)).mappings().all()
            reviews = (
                conn.execute(
                    select(schema.wallet_label_reviews).order_by(schema.wallet_label_reviews.c.id)
                )
                .mappings()
                .all()
            )
        latest = {r["label_id"]: r["decision"] for r in reviews}
        items = [{**_row(r), "latest_review": latest.get(r["id"])} for r in rows]
        return items[offset : offset + limit], len(items)

    def training_rows(self, chain: str, data_kind: str) -> list[J]:
        """Usable labels: trainable statuses, or weak/inferred accepted by a reviewer; never a
        label whose latest review is 'rejected'."""
        items, _ = self.list(chain=chain, data_kind=data_kind, limit=10**9, offset=0)
        out: list[J] = []
        for r in items:
            if r["latest_review"] == "rejected" or r["status"] == "unknown":
                continue
            if r["status"] in TRAINABLE or r["latest_review"] == "accepted":
                out.append(r)
        return out

    def counts(self) -> J:
        items, total = self.list(chain=None, data_kind=None, limit=10**9, offset=0)
        by: dict[str, int] = {}
        for r in items:
            key = f"{r['data_kind']}:{r['status']}"
            by[key] = by.get(key, 0) + 1
        return {"total": total, "by_kind_and_status": by}


def _row(r: Any) -> J:
    out = {
        k: r[k]
        for k in (
            "id",
            "chain",
            "address",
            "dimension",
            "label",
            "value",
            "status",
            "source",
            "source_ref",
            "data_kind",
            "note",
        )
    }
    out["observed_at"] = _iso(r["observed_at"])
    out["created_at"] = _iso(r["created_at"])
    return out


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None
