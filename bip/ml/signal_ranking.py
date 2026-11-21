"""Ranking discovery candidates against human review (Stage 4). Pure Python, deterministic.

What it does, and what it refuses to do:

* **Point-in-time features.** :func:`write_snapshot` stores a candidate's features once, when the
  signal is created, from data at or before its ``observed_at``. The signal row is refreshed by
  later runs, so evaluation reads only these snapshots. Status, review state and expiry are never
  features. Relationship rows can change after the fact (first-seen times, weights), so graph
  features read at creation are approximate; this is recorded in the run summary.
* **Labels** come only from :meth:`bip.discovery.review.ReviewService.ground_truth_set`: human,
  undisputed, and from a database marked as real network data.
* **Splits** are chronological and entity-aware: candidates are grouped by (chain, subject); each
  group sits wholly in one split, ordered by its first ``observed_at`` (60/20/20).
* **Scorers.** The rule score is the baseline. A deterministic unsupervised anomaly score (robust
  z-scores against *earlier* candidates of the same chain only) and graph/cluster features are
  always available. A supervised logistic model is trained only for chains with enough verified
  labels of both classes; otherwise it is reported ``not_ready``.
* **Metrics** on the test split: precision, recall, F1 at a threshold chosen on validation, PR-AUC
  (average precision), top-k precision, calibration (Brier score and reliability bins),
  false-positive volume, and per-chain results; plus feature drift (PSI).

The model only ranks. It never confirms, rejects or acts on a candidate: human review stays
authoritative.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, insert, or_, select, union
from sqlalchemy.engine import Connection

from bip.config.settings import MLSettings
from bip.core.logging import get_logger
from bip.db import schema
from bip.db.canonical import orphaned_tx_ids
from bip.db.errors import RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.discovery.review import ReviewService
from bip.ml.drift import population_stability_index

log = get_logger(__name__)

FEATURE_VERSION = "1"
RANKER_VERSION = "1"
NUMERIC_FEATURES = (
    "magnitude",
    "baseline_reliability",
    "confidence_cap",
    "rule_confidence",
    "evidence_txs",
    "prior_transfers_out",
    "prior_transfers_in",
    "prior_counterparties",
    "subject_age_days",
    "prior_signals",
    "relationship_degree",
    "shared_funder_cluster",
)
COUNT_FEATURES = frozenset(
    {
        "evidence_txs",
        "prior_transfers_out",
        "prior_transfers_in",
        "prior_counterparties",
        "subject_age_days",
        "prior_signals",
        "relationship_degree",
        "shared_funder_cluster",
    }
)
"""Heavy-tailed counts are compared on ``log1p``."""
GRAPH_FEATURES = ("relationship_degree", "shared_funder_cluster")
MIN_REFERENCE = 10
"""Earlier candidates of the same chain needed before an anomaly score is given."""
REFERENCE_WINDOW = 500
MAD_SCALE = 1.4826
TOP_K = (10, 25)
CALIBRATION_BINS = 5
LOGISTIC_STEPS = 400
LOGISTIC_RATE = 0.1
LOGISTIC_L2 = 0.01
LIMITATIONS = (
    "Features are snapshotted when a candidate is created; candidates created before this "
    "version have no snapshot and are excluded.",
    "Graph features read relationship rows whose first-seen times and weights can change "
    "later, so they are approximate at the snapshot time.",
    "Native transfers are stored only above the collector threshold, so transfer counts are "
    "censored.",
    "Rule scores are not probabilities; their calibration figures describe the score as given.",
)


# ----------------------------------------------------------------------------- snapshots


def write_snapshot(
    conn: Connection,
    signal_id: int,
    *,
    chain: str,
    family: str,
    detector_id: str,
    subject_ref: str,
    observed_at: datetime,
    magnitude: float,
    baseline_reliability: float,
    confidence_cap: float,
    evidence_txs: int,
    rule_score: float,
    rule_confidence: float,
) -> None:
    """Store a new candidate's point-in-time features (called inside the creating transaction).
    A failure is logged and leaves the candidate without a snapshot; it never blocks discovery."""
    savepoint: Any = None
    try:
        savepoint = conn.begin_nested()  # a failure here must not break the creating transaction
        features = {
            "magnitude": float(magnitude),
            "baseline_reliability": float(baseline_reliability),
            "confidence_cap": float(confidence_cap),
            "rule_confidence": float(rule_confidence),
            "evidence_txs": float(evidence_txs),
            **_subject_features(conn, chain, subject_ref, observed_at, signal_id),
        }
        conn.execute(
            insert(schema.signal_feature_snapshots).values(
                signal_id=signal_id,
                feature_version=FEATURE_VERSION,
                chain=chain,
                family=family,
                detector_id=detector_id,
                subject_ref=subject_ref,
                observed_at=observed_at,
                rule_score=float(rule_score),
                features_json=json.dumps(features, sort_keys=True),
                created_at=utcnow(),
            )
        )
        savepoint.commit()
    except Exception:
        if savepoint is not None and savepoint.is_active:
            savepoint.rollback()
        log.exception("feature snapshot for signal %s failed", signal_id)


def _subject_features(
    conn: Connection, chain: str, ref: str, at: datetime, signal_id: int
) -> dict[str, float | None]:
    """History of the subject strictly before ``at`` (transfers, peers, age, earlier candidates)
    and its relationship neighbourhood first seen at or before ``at``."""
    tr, t, sg = schema.transfers, schema.transactions, schema.signals
    rel = schema.entity_relationships
    orphaned = orphaned_tx_ids(chain)
    before = (tr.c.chain == chain, tr.c.block_time < at, tr.c.tx_id.not_in(orphaned))
    out_n = conn.execute(select(func.count()).where(*before, tr.c.from_address == ref)).scalar_one()
    in_n = conn.execute(select(func.count()).where(*before, tr.c.to_address == ref)).scalar_one()
    peers = union(
        select(tr.c.to_address.label("p")).where(
            *before, tr.c.from_address == ref, tr.c.to_address.is_not(None)
        ),
        select(tr.c.from_address.label("p")).where(
            *before, tr.c.to_address == ref, tr.c.from_address.is_not(None)
        ),
    ).subquery()
    peer_n = conn.execute(select(func.count()).select_from(peers)).scalar_one()
    first = conn.execute(
        select(func.min(t.c.block_time)).where(
            t.c.chain == chain,
            t.c.block_time < at,
            t.c.tx_id.not_in(orphaned),
            or_(t.c.from_address == ref, t.c.to_address == ref),
        )
    ).scalar()
    # no earlier stored transaction: the age is unknown, not zero (principle 8)
    age = None if first is None else max(0.0, (at - first).total_seconds() / 86_400)
    prior = conn.execute(
        select(func.count()).where(
            sg.c.chain == chain,
            sg.c.subject_ref == ref,
            sg.c.observed_at < at,
            sg.c.id != signal_id,
        )
    ).scalar_one()
    seen = (rel.c.chain == chain, rel.c.status == "active", rel.c.first_seen_at <= at)
    degree = conn.execute(
        select(func.count()).where(*seen, or_(rel.c.source_ref == ref, rel.c.target_ref == ref))
    ).scalar_one()
    funders = (
        select(rel.c.target_ref)
        .where(*seen, rel.c.kind == "funded_by", rel.c.source_ref == ref)
        .limit(5)
        .scalar_subquery()
    )
    cluster = conn.execute(
        select(func.count(func.distinct(rel.c.source_ref))).where(
            *seen,
            rel.c.kind == "funded_by",
            rel.c.target_ref.in_(funders),
            rel.c.source_ref != ref,
        )
    ).scalar_one()
    return {
        "prior_transfers_out": float(out_n),
        "prior_transfers_in": float(in_n),
        "prior_counterparties": float(peer_n),
        "subject_age_days": None if age is None else round(age, 4),
        "prior_signals": float(prior),
        "relationship_degree": float(degree),
        "shared_funder_cluster": float(cluster),
    }


# ----------------------------------------------------------------------------- the data


@dataclass(slots=True)
class _Row:
    signal_id: int
    chain: str
    family: str
    subject: str
    observed_at: datetime
    rule_score: float
    features: dict[str, float | None]
    """``None`` (or absent) means the value is unknown; it is never read as 0."""


Vector = list[float | None]


def _vector(f: dict[str, float | None]) -> Vector:
    """Feature values in :data:`NUMERIC_FEATURES` order; a missing value stays ``None``."""
    out: Vector = []
    for k in NUMERIC_FEATURES:
        v = f.get(k)
        out.append(None if v is None else math.log1p(max(0.0, v)) if k in COUNT_FEATURES else v)
    return out


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def anomaly_scores(rows: Sequence[_Row]) -> dict[int, tuple[float | None, list[dict[str, Any]]]]:
    """Unsupervised score in [0, 1] per candidate, from robust z-scores of its features against
    up to :data:`REFERENCE_WINDOW` *earlier* candidates of the same chain (point in time: never a
    later one). ``None`` when fewer than :data:`MIN_REFERENCE` earlier candidates exist."""
    out: dict[int, tuple[float | None, list[dict[str, Any]]]] = {}
    by_chain: dict[str, list[_Row]] = defaultdict(list)
    for r in sorted(rows, key=lambda x: (x.observed_at, x.signal_id)):
        by_chain[r.chain].append(r)
    for chain_rows in by_chain.values():
        vectors = [_vector(r.features) for r in chain_rows]
        for i, r in enumerate(chain_rows):
            earlier = [
                vectors[j]
                for j in range(max(0, i - REFERENCE_WINDOW), i)
                if chain_rows[j].observed_at < r.observed_at
            ]
            if len(earlier) < MIN_REFERENCE:
                out[r.signal_id] = (None, [])
                continue
            zs: list[tuple[float, str]] = []
            for k, name in enumerate(NUMERIC_FEATURES):
                value = vectors[i][k]
                col = [x for v in earlier if (x := v[k]) is not None]
                if value is None or len(col) < MIN_REFERENCE:
                    continue  # unknown here, or too little known earlier: not compared
                med = _median(col)
                mad = _median([abs(x - med) for x in col]) * MAD_SCALE
                spread = max(mad, 1e-3)
                zs.append((abs(value - med) / spread, name))
            if not zs:
                out[r.signal_id] = (None, [])
                continue
            zs.sort(reverse=True)
            top = zs[:3]
            strength = sum(min(z, 10.0) for z, _ in top) / len(top)
            score = round(1 - math.exp(-strength / 3), 4)
            out[r.signal_id] = (
                score,
                [{"feature": n, "robust_z": round(z, 2)} for z, n in top],
            )
    return out


def entity_split(
    labelled: Sequence[_Row],
) -> tuple[list[_Row], list[_Row], list[_Row], dict[str, Any]]:
    """Chronological, entity-aware 60/20/20 split: every candidate of one (chain, subject) sits in
    one split, groups ordered by their first ``observed_at``."""
    groups: dict[tuple[str, str], list[_Row]] = defaultdict(list)
    for r in labelled:
        groups[(r.chain, r.subject)].append(r)
    ordered = sorted(
        groups.values(), key=lambda g: (min(x.observed_at for x in g), g[0].chain, g[0].subject)
    )
    total = len(labelled)
    train: list[_Row] = []
    val: list[_Row] = []
    test: list[_Row] = []
    seen = 0
    for g in ordered:
        share = seen / total if total else 0
        (train if share < 0.6 else val if share < 0.8 else test).extend(g)
        seen += len(g)

    def window(part: list[_Row]) -> dict[str, Any]:
        if not part:
            return {"n": 0, "from": None, "to": None}
        times = [x.observed_at for x in part]
        return {"n": len(part), "from": min(times).isoformat(), "to": max(times).isoformat()}

    return (
        train,
        val,
        test,
        {
            "method": "chronological by first observed_at, grouped by (chain, subject)",
            "train": window(train),
            "validation": window(val),
            "test": window(test),
            "subjects_in_more_than_one_split": len(
                ({(r.chain, r.subject) for r in train} & {(r.chain, r.subject) for r in val})
                | ({(r.chain, r.subject) for r in train} & {(r.chain, r.subject) for r in test})
                | ({(r.chain, r.subject) for r in val} & {(r.chain, r.subject) for r in test})
            ),
        },
    )


# ----------------------------------------------------------------------------- the model


def _standardise(rows: Sequence[Vector]) -> tuple[list[float], list[float]]:
    """Per-feature mean and standard deviation over the *known* training values."""
    n = len(rows)
    if n == 0:
        raise ValueError("cannot standardise an empty training set")
    means: list[float] = []
    stds: list[float] = []
    for k in range(len(rows[0])):
        known = [x for r in rows if (x := r[k]) is not None]
        m = sum(known) / len(known) if known else 0.0
        means.append(m)
        stds.append(
            math.sqrt(sum((x - m) ** 2 for x in known) / len(known)) or 1.0 if known else 1.0
        )
    return means, stds


def _scaled(row: Vector, means: Sequence[float], stds: Sequence[float]) -> list[float]:
    """Standardised values; a missing value is imputed with the training mean, so it scales to 0
    and contributes nothing (it is never read as a literal 0)."""
    return [0.0 if v is None else (v - m) / s for v, m, s in zip(row, means, stds, strict=True)]


def train_logistic(x: Sequence[Vector], y: Sequence[int]) -> dict[str, Any]:
    """L2 logistic regression by full-batch gradient descent from zero weights (deterministic).
    Missing values are imputed with the training mean (zero contribution) and counted."""
    means, stds = _standardise(x)
    z = [_scaled(r, means, stds) for r in x]
    w = [0.0] * len(means)
    b = 0.0
    n = len(z)
    for _ in range(LOGISTIC_STEPS):
        gw = [LOGISTIC_L2 * wk for wk in w]
        gb = 0.0
        for row, label in zip(z, y, strict=True):
            p = _sigmoid(b + sum(wk * xk for wk, xk in zip(w, row, strict=True)))
            err = p - label
            gb += err / n
            for k, xk in enumerate(row):
                gw[k] += err * xk / n
        w = [wk - LOGISTIC_RATE * g for wk, g in zip(w, gw, strict=True)]
        b -= LOGISTIC_RATE * gb
    return {
        "kind": "logistic_regression",
        "features": list(NUMERIC_FEATURES),
        "weights": [round(v, 6) for v in w],
        "bias": round(b, 6),
        "means": [round(v, 6) for v in means],
        "stds": [round(v, 6) for v in stds],
        "missing_imputed": {
            name: sum(1 for r in x if r[k] is None)
            for k, name in enumerate(NUMERIC_FEATURES)
            if any(r[k] is None for r in x)
        },
        "missing_policy": "imputed with the training mean (zero contribution)",
        "steps": LOGISTIC_STEPS,
        "l2": LOGISTIC_L2,
    }


def _sigmoid(v: float) -> float:
    if v < -30:
        return 0.0
    if v > 30:
        return 1.0
    return 1 / (1 + math.exp(-v))


def predict(
    model: dict[str, Any], features: dict[str, float | None]
) -> tuple[float, list[dict[str, Any]]]:
    z = _scaled(_vector(features), model["means"], model["stds"])
    contributions = [
        (w * v, name) for w, v, name in zip(model["weights"], z, model["features"], strict=True)
    ]
    p = _sigmoid(model["bias"] + sum(c for c, _ in contributions))
    contributions.sort(key=lambda c: abs(c[0]), reverse=True)
    return round(p, 4), [{"feature": n, "contribution": round(c, 3)} for c, n in contributions[:5]]


# ----------------------------------------------------------------------------- metrics


def _ranked(scored: Sequence[tuple[float, int]]) -> list[tuple[float, int]]:
    """Highest score first; within a tie the negatives come first (pessimistic), so the order of
    the input never changes a metric and a tie never flatters a scorer."""
    return sorted(scored, key=lambda t: (-t[0], t[1]))


def average_precision(scored: Sequence[tuple[float, int]]) -> float | None:
    positives = sum(y for _, y in scored)
    if not positives:
        return None
    hits = 0
    total = 0.0
    for i, (_, y) in enumerate(_ranked(scored), start=1):
        if y:
            hits += 1
            total += hits / i
    return round(total / positives, 4)


def _confusion(scored: Sequence[tuple[float, int]], threshold: float) -> dict[str, int]:
    tp = sum(1 for s, y in scored if s >= threshold and y)
    fp = sum(1 for s, y in scored if s >= threshold and not y)
    fn = sum(1 for s, y in scored if s < threshold and y)
    tn = len(scored) - tp - fp - fn
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _prf(c: dict[str, int]) -> dict[str, float | None]:
    precision = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else None
    recall = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "precision": None if precision is None else round(precision, 4),
        "recall": None if recall is None else round(recall, 4),
        "f1": None if f1 is None else round(f1, 4),
    }


def choose_threshold(scored: Sequence[tuple[float, int]]) -> float:
    """The validation threshold with the best F1 (ties: the higher threshold); 0.5 without
    validation positives."""
    if not scored or not any(y for _, y in scored):
        return 0.5
    best = (-1.0, 0.5)
    for t in sorted({s for s, _ in scored}, reverse=True):
        f1 = _prf(_confusion(scored, t))["f1"] or 0.0
        if f1 > best[0]:
            best = (f1, t)
    return best[1]


def evaluate_scores(test: Sequence[tuple[float, int, str]], threshold: float) -> dict[str, Any]:
    scored = [(s, y) for s, y, _ in test]
    c = _confusion(scored, threshold)
    ranked = _ranked(scored)
    top_k = {
        f"precision_at_{k}": round(sum(y for _, y in ranked[:k]) / min(k, len(ranked)), 4)
        if ranked
        else None
        for k in TOP_K
    }
    brier = round(sum((s - y) ** 2 for s, y in scored) / len(scored), 4) if scored else None
    bins: list[dict[str, Any]] = []
    for b in range(CALIBRATION_BINS):
        lo, hi = b / CALIBRATION_BINS, (b + 1) / CALIBRATION_BINS
        members = [
            (s, y) for s, y in scored if lo <= s < hi or (b == CALIBRATION_BINS - 1 and s == 1)
        ]
        if members:
            bins.append(
                {
                    "range": [lo, hi],
                    "n": len(members),
                    "mean_score": round(sum(s for s, _ in members) / len(members), 4),
                    "positive_rate": round(sum(y for _, y in members) / len(members), 4),
                }
            )
    per_chain: dict[str, Any] = {}
    for chain in sorted({ch for _, _, ch in test}):
        part = [(s, y) for s, y, ch in test if ch == chain]
        cc = _confusion(part, threshold)
        per_chain[chain] = {
            "n": len(part),
            **_prf(cc),
            "pr_auc": average_precision(part),
            "false_positives": cc["fp"],
        }
    return {
        "n": len(scored),
        "positives": sum(y for _, y in scored),
        "threshold": threshold,
        **_prf(c),
        "pr_auc": average_precision(scored),
        **top_k,
        "brier": brier,
        "calibration_bins": bins,
        "false_positives": c["fp"],
        "confusion": c,
        "per_chain": per_chain,
    }


# ----------------------------------------------------------------------------- the service


class SignalRanking:
    def __init__(self, repo: Repository, review: ReviewService, cfg: MLSettings) -> None:
        self._repo = repo
        self._review = review
        self._cfg = cfg

    def _snapshots(self) -> list[_Row]:
        fs = schema.signal_feature_snapshots
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(fs)
                .where(fs.c.feature_version == FEATURE_VERSION)
                .order_by(fs.c.observed_at.desc(), fs.c.signal_id.desc())
                .limit(self._cfg.ranking_max_snapshots)
            ).all()
        return sorted(
            (
                _Row(
                    int(r.signal_id),
                    str(r.chain),
                    str(r.family),
                    str(r.subject_ref),
                    r.observed_at,
                    float(r.rule_score),
                    {
                        k: None if v is None else float(v)
                        for k, v in json.loads(r.features_json).items()
                    },
                )
                for r in rows
            ),
            key=lambda x: (x.observed_at, x.signal_id),
        )

    def _gate(self, counts: dict[str, dict[str, int]]) -> dict[str, Any]:
        need = self._cfg.ranking_min_labels_per_class
        per_chain = {
            chain: {
                **c,
                "needed_per_class": need,
                "supervised_ready": c["positive"] >= need and c["negative"] >= need,
            }
            for chain, c in sorted(counts.items())
        }
        return {"needed_per_class": need, "chains": per_chain}

    def status(self) -> dict[str, Any]:
        truth = self._review.ground_truth_set(limit=1)
        fs = schema.signal_feature_snapshots
        with self._repo.transaction() as conn:
            snaps = conn.execute(select(func.count()).select_from(fs)).scalar_one()
        gate = self._gate(truth["counts"])
        ready = any(c["supervised_ready"] for c in gate["chains"].values())
        return {
            "feature_version": FEATURE_VERSION,
            "ranker_version": RANKER_VERSION,
            "snapshots": int(snaps),
            "labels": truth["counts"],
            "labels_excluded_because": truth["excluded_because"],
            "gate": gate,
            "supervised": "ready" if ready else "not_ready",
            "scorers": ["rule_score (baseline)", "anomaly (unsupervised)", "supervised (gated)"],
            "last_run": self.last_run(),
            "authority": "Scores rank candidates for review; only a person confirms or rejects.",
        }

    def evaluate(
        self,
        actor: str,
        *,
        labels: dict[int, int] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Evaluate every scorer against ground truth. ``labels`` overrides ground truth for
        tests with synthetic labels: the result is marked experimental and never stored."""
        experimental = labels is not None
        if experimental:
            persist = False
            truth_labels = dict(labels or {})
            excluded = None
        else:
            truth = self._review.ground_truth_set(limit=10**9)
            truth_labels = {int(i["signal_id"]): int(i["label"]) for i in truth["items"]}
            excluded = truth["excluded_because"]
        rows = self._snapshots()
        anomalies = anomaly_scores(rows)
        labelled = [r for r in rows if r.signal_id in truth_labels]
        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"positive": 0, "negative": 0})
        for r in labelled:
            counts[r.chain]["positive" if truth_labels[r.signal_id] else "negative"] += 1
        gate = self._gate(dict(counts))
        train, val, test, split = entity_split(labelled)
        summary: dict[str, Any] = {
            "experimental": experimental,
            "labels_source": "synthetic (test only)" if experimental else "human ground truth",
            "labels_excluded_because": excluded,
            "snapshots": len(rows),
            "labelled": len(labelled),
            "labelled_without_snapshot": self._without_snapshot(truth_labels),
            "label_counts": dict(counts),
            "gate": gate,
            "split": split,
            "limitations": list(LIMITATIONS),
            "scorers": {},
        }
        status = "completed"
        test_labels = [truth_labels[r.signal_id] for r in test]
        if not test or len(set(test_labels)) < 2:
            status = "not_ready"
            summary["reason"] = (
                "the test split needs confirmed and rejected candidates; there are "
                f"{len(labelled)} labelled candidates with snapshots"
            )
        else:
            summary["scorers"]["rule_score"] = self._score(
                "baseline", val, test, truth_labels, lambda r: r.rule_score / 100
            )
            anomaly_test = [r for r in test if anomalies[r.signal_id][0] is not None]
            anomaly_val = [r for r in val if anomalies[r.signal_id][0] is not None]
            summary["scorers"]["anomaly"] = (
                self._score(
                    "unsupervised",
                    anomaly_val,
                    anomaly_test,
                    truth_labels,
                    lambda r: float(anomalies[r.signal_id][0] or 0.0),
                )
                if anomaly_test
                else {"status": "insufficient_reference", "kind": "unsupervised"}
            )
            # the gate counts labels across all splits; a chain also needs both classes in its
            # own training slice (a chain whose subjects all appear late may have none)
            need = self._cfg.ranking_min_labels_per_class
            for c, g in gate["chains"].items():
                tr_c = [truth_labels[r.signal_id] for r in train if r.chain == c]
                g["train_positive"], g["train_negative"] = sum(tr_c), len(tr_c) - sum(tr_c)
                g["train_ready"] = bool(
                    g["supervised_ready"]
                    and sum(tr_c) >= max(1, need // 2)
                    and len(tr_c) - sum(tr_c) >= max(1, need // 2)
                )
            ready_chains = {c for c, g in gate["chains"].items() if g["train_ready"]}
            model: dict[str, Any] | None = None
            if ready_chains:
                tr = [r for r in train if r.chain in ready_chains]
                model = train_logistic(
                    [_vector(r.features) for r in tr], [truth_labels[r.signal_id] for r in tr]
                )
                model["chains"] = sorted(ready_chains)
                summary["scorers"]["supervised"] = self._score(
                    "supervised",
                    [r for r in val if r.chain in ready_chains],
                    [r for r in test if r.chain in ready_chains],
                    truth_labels,
                    lambda r: predict(model or {}, r.features)[0],
                )
                summary["model"] = model
            else:
                summary["scorers"]["supervised"] = {
                    "status": "not_ready",
                    "kind": "supervised",
                    "reason": "no chain has enough verified labels of both classes",
                }
        summary["drift"] = _drift(train, rows)
        digest = hashlib.sha256(
            json.dumps(
                [
                    [r.signal_id, truth_labels.get(r.signal_id), r.features, r.rule_score]
                    for r in rows
                ],
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        times = [r.observed_at for r in rows]
        result: dict[str, Any] = {
            "status": status,
            "feature_version": FEATURE_VERSION,
            "ranker_version": RANKER_VERSION,
            "dataset_hash": digest,
            "window_from": min(times).isoformat() if times else None,
            "window_to": max(times).isoformat() if times else None,
            **summary,
        }
        if persist:
            with self._repo.transaction() as conn:
                pk = conn.execute(
                    insert(schema.signal_ranking_runs).values(
                        created_at=utcnow(),
                        actor=actor[:80],
                        status=status,
                        feature_version=FEATURE_VERSION,
                        ranker_version=RANKER_VERSION,
                        dataset_hash=digest,
                        window_from=min(times) if times else None,
                        window_to=max(times) if times else None,
                        summary_json=json.dumps(summary, default=str),
                    )
                ).inserted_primary_key
            result["run_id"] = int(pk[0]) if pk is not None else None
        return result

    def _without_snapshot(self, labels: dict[int, int]) -> int:
        """Labelled candidates that have no snapshot at all (created before snapshots existed,
        or the snapshot failed); not those merely beyond ``ranking_max_snapshots``."""
        if not labels:
            return 0
        fs = schema.signal_feature_snapshots
        ids = sorted(labels)
        have = 0
        with self._repo.transaction() as conn:
            for i in range(0, len(ids), 400):
                have += int(
                    conn.execute(
                        select(func.count()).where(fs.c.signal_id.in_(ids[i : i + 400]))
                    ).scalar_one()
                )
        return len(ids) - have

    @staticmethod
    def _score(
        kind: str,
        val: Sequence[_Row],
        test: Sequence[_Row],
        labels: dict[int, int],
        score: Callable[[_Row], float],
    ) -> dict[str, Any]:
        threshold = choose_threshold([(score(r), labels[r.signal_id]) for r in val])
        metrics = evaluate_scores(
            [(score(r), labels[r.signal_id], r.chain) for r in test], threshold
        )
        return {"kind": kind, "status": "evaluated", **metrics}

    # ------------------------------------------------------------------ runs and explanations

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        r = schema.signal_ranking_runs
        with self._repo.transaction() as conn:
            rows = conn.execute(select(r).order_by(r.c.id.desc()).limit(limit)).mappings().all()
        return [_run(x, full=False) for x in rows]

    def run(self, run_id: int) -> dict[str, Any]:
        r = schema.signal_ranking_runs
        with self._repo.transaction() as conn:
            row = conn.execute(select(r).where(r.c.id == run_id)).mappings().first()
        if row is None:
            raise RecordNotFoundError(f"ranking run {run_id} not found")
        return _run(row, full=True)

    def last_run(self) -> dict[str, Any] | None:
        runs = self.runs(1)
        return runs[0] if runs else None

    def explain(self, signal_id: int) -> dict[str, Any]:
        """Why a candidate ranks where it does: the rule score's inputs, its anomaly score with
        the most unusual features, and the supervised probability with its largest contributions
        when a model exists for its chain. Never a verdict."""
        rows = self._snapshots()
        snap = next((r for r in rows if r.signal_id == signal_id), None)
        if snap is None:
            return {
                "signal_id": signal_id,
                "available": False,
                "reason": "no point-in-time feature snapshot (created before Stage 4 or failed)",
            }
        score, top = anomaly_scores(rows).get(signal_id, (None, []))
        out: dict[str, Any] = {
            "signal_id": signal_id,
            "available": True,
            "feature_version": FEATURE_VERSION,
            "observed_at": snap.observed_at.isoformat(),
            "features": snap.features,
            "graph_features": {k: snap.features.get(k) for k in GRAPH_FEATURES},
            "evidence_class": "statistical_signal",
            "rule_score": {"value": snap.rule_score, "kind": "baseline (rules)"},
            "anomaly": {
                "value": score,
                "kind": "unsupervised",
                "most_unusual": top,
                "note": None
                if score is not None
                else f"fewer than {MIN_REFERENCE} earlier candidates on this chain",
            },
            "supervised": {"status": "not_ready"},
            "authority": "A ranking aid only. A person confirms or rejects; the model cannot.",
        }
        model = self._latest_model()
        if model is not None and snap.chain in model.get("chains", []):
            p, contributions = predict(model, snap.features)
            out["supervised"] = {
                "status": "available",
                "probability": p,
                "largest_contributions": contributions,
                "model_run": model.get("_run_id"),
            }
        return out

    def _latest_model(self) -> dict[str, Any] | None:
        """The newest stored model: a later completed run without one (supervised not ready)
        must not hide an earlier model. Bounded to the last 50 completed runs."""
        r = schema.signal_ranking_runs
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(r.c.id, r.c.summary_json)
                .where(r.c.status == "completed")
                .order_by(r.c.id.desc())
                .limit(50)
            ).all()
        for row in rows:
            model = json.loads(row.summary_json).get("model")
            if model:
                model["_run_id"] = int(row.id)
                return cast(dict[str, Any], model)
        return None


def _drift(train: Sequence[_Row], rows: Sequence[_Row]) -> dict[str, Any]:
    """PSI of each feature between the training window and the latest 200 candidates, over known
    values only (a missing value is left out, never counted as 0)."""
    recent = list(rows[-200:])
    if len(train) < 20 or len(recent) < 20:
        return {"status": "insufficient_data", "train": len(train), "recent": len(recent)}
    train_v = [_vector(r.features) for r in train]
    recent_v = [_vector(r.features) for r in recent]
    features: dict[str, Any] = {}
    for k, name in enumerate(NUMERIC_FEATURES):
        ref = [x for v in train_v if (x := v[k]) is not None]
        cur = [x for v in recent_v if (x := v[k]) is not None]
        psi = population_stability_index(ref, cur) if ref and cur else None
        features[name] = None if psi is None else round(psi, 4)
    return {"status": "computed", "psi": features, "recent": len(recent)}


def _run(x: Any, *, full: bool) -> dict[str, Any]:
    summary = json.loads(x["summary_json"])
    base = {
        "id": x["id"],
        "created_at": x["created_at"].isoformat(),
        "actor": x["actor"],
        "status": x["status"],
        "feature_version": x["feature_version"],
        "ranker_version": x["ranker_version"],
        "dataset_hash": x["dataset_hash"],
        "window_from": x["window_from"].isoformat() if x["window_from"] else None,
        "window_to": x["window_to"].isoformat() if x["window_to"] else None,
        "labelled": summary.get("labelled"),
        "label_counts": summary.get("label_counts"),
    }
    return {**base, **summary} if full else base
