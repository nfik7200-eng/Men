# pyright: basic
# scikit-learn ships without type information; this module is the only place that touches it
# and is checked in pyright "basic" mode. Everything it returns is plain Python data.
"""scikit-learn training, calibration, thresholding and evaluation (CPU, small models).

Per label (one-vs-rest, so a wallet can carry several roles):

* **Model:** ``RandomForestClassifier`` (balanced subsample class weights, fixed seed).
* **Missing values:** each feature becomes (value with the training median imputed, missing
  indicator). The model can tell "unavailable" from a real value, and from zero.
* **Calibration:** sigmoid (Platt) on the validation split, only if it has
  ≥ ``MIN_CAL`` positives and negatives; otherwise "uncalibrated".
* **Thresholds:** chosen on validation data, never below 0.5. ``high`` is the smallest score
  whose validation
  precision ≥ the high target with ≥ ``min_support`` predictions at or above it; ``moderate``
  likewise. If none qualifies, the label has no high/moderate threshold and predictions can
  never be reported above low confidence.
* **Evaluation:** on every split, including an out-of-time test split. The test split reports
  precision at the chosen thresholds, so threshold optimism is visible.
"""

from __future__ import annotations

import hashlib
import io
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.frozen import FrozenEstimator

from bip.ml.signal_ranking import average_precision

MIN_CAL = 10
J = dict[str, Any]


def matrix(
    rows: Sequence[dict[str, float | None]],
    names: Sequence[str],
    medians: Sequence[float] | None = None,
) -> tuple[Any, list[float]]:
    raw = np.array(
        [
            [np.nan if r.get(n) is None else float(r[n]) for n in names]  # type: ignore[arg-type]
            for r in rows
        ],
        dtype=float,
    ).reshape(len(rows), len(names))
    if medians is None:
        med = []
        for j in range(len(names)):
            col = raw[:, j]
            col = col[~np.isnan(col)]
            med.append(float(np.median(col)) if col.size else 0.0)
    else:
        med = list(medians)
    missing = np.isnan(raw)
    filled = np.where(missing, np.array(med)[None, :], raw)
    return np.hstack([filled, missing.astype(float)]), med


@dataclass
class LabelModel:
    label: str
    estimator: Any
    calibrated: bool
    thresholds: J
    train_counts: J
    calibration: J = field(default_factory=dict)


def _threshold(scores: Any, y: Any, target: float, min_support: int) -> float | None:
    order = np.argsort(-scores)
    s, t = scores[order], y[order]
    tp = np.cumsum(t)
    n = np.arange(1, len(s) + 1)
    prec = tp / n
    best = None
    for i in range(len(s)):
        # only cut between distinct scores, so the threshold selects exactly n[i] items
        if i + 1 < len(s) and s[i + 1] == s[i]:
            continue
        if s[i] < 0.5:
            break  # a calibrated score below 0.5 never supports a positive claim
        if n[i] >= min_support and prec[i] >= target:
            best = float(s[i])  # keep lowering while precision holds (maximises coverage)
    return best


def fit_label(
    label: str,
    x_tr: Any,
    y_tr: Any,
    x_val: Any,
    y_val: Any,
    *,
    seed: int,
    high_target: float,
    moderate_target: float,
    min_support: int,
) -> LabelModel:
    rf = RandomForestClassifier(
        n_estimators=150,
        max_depth=8,
        min_samples_leaf=2,
        class_weight="balanced_subsample",
        random_state=seed,
        n_jobs=1,
    )
    rf.fit(x_tr, y_tr)
    est: Any = rf
    calibrated = False
    cal: J = {"method": None, "reason": None}
    pos, neg = int(y_val.sum()), int(len(y_val) - y_val.sum())
    if pos >= MIN_CAL and neg >= MIN_CAL:
        est = CalibratedClassifierCV(FrozenEstimator(rf), method="sigmoid")
        est.fit(x_val, y_val)
        calibrated = True
        cal = {"method": "sigmoid (validation split)", "reason": None}
    else:
        cal["reason"] = (
            f"validation split has {pos} positive / {neg} negative examples; "
            f"calibration needs ≥ {MIN_CAL} of each"
        )
    scores = est.predict_proba(x_val)[:, 1]
    thresholds = {
        "high": _threshold(scores, y_val, high_target, min_support) if calibrated else None,
        "moderate": _threshold(scores, y_val, moderate_target, min_support) if calibrated else None,
        "low": 0.5,
        "high_precision_target": high_target,
        "moderate_precision_target": moderate_target,
        "min_support": min_support,
        "basis": "validation split" if calibrated else "not validated (uncalibrated model)",
    }
    return LabelModel(
        label,
        est,
        calibrated,
        thresholds,
        {
            "train_positive": int(y_tr.sum()),
            "train_negative": int(len(y_tr) - y_tr.sum()),
            "val_positive": pos,
            "val_negative": neg,
        },
        cal,
    )


def scores(m: LabelModel, x: Any) -> list[float]:
    return [float(p) for p in m.estimator.predict_proba(x)[:, 1]]


def metrics(y: Sequence[int], p: Sequence[float], thresholds: J) -> J:
    ya, pa = np.array(y, dtype=int), np.array(p, dtype=float)

    def at(t: float | None) -> J:
        if t is None or len(ya) == 0:
            return {"threshold": t, "predicted": 0, "precision": None, "recall": None}
        pred = pa >= t
        tp = int((pred & (ya == 1)).sum())
        n = int(pred.sum())
        pos = int(ya.sum())
        return {
            "threshold": t,
            "predicted": n,
            "precision": tp / n if n else None,
            "recall": tp / pos if pos else None,
        }

    pred = pa >= 0.5
    tp = int((pred & (ya == 1)).sum())
    fp = int((pred & (ya == 0)).sum())
    fn = int((~pred & (ya == 1)).sum())
    tn = int((~pred & (ya == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    bins: list[J] = []
    for lo in np.linspace(0, 0.9, 10):
        sel = (pa >= lo) & (pa < lo + 0.1 if lo < 0.9 else pa <= 1.0)
        if sel.any():
            bins.append(
                {
                    "from": round(float(lo), 1),
                    "to": round(float(lo) + 0.1, 1),
                    "count": int(sel.sum()),
                    "mean_score": float(pa[sel].mean()),
                    "observed_rate": float(ya[sel].mean()),
                }
            )
    return {
        "support": int(ya.sum()),
        "n": len(ya),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        "false_negative_rate": fn / (fn + tp) if fn + tp else None,
        "brier": float(np.mean((pa - ya) ** 2)) if len(ya) else None,
        "reliability_bins": bins,
        "at_high_threshold": at(thresholds.get("high")),
        "at_moderate_threshold": at(thresholds.get("moderate")),
        "pr_auc": average_precision(list(zip(p, y, strict=True))),
        "precision_at_10": _precision_at(p, y, 10),
    }


def _precision_at(p: Sequence[float], y: Sequence[int], k: int) -> float | None:
    ranked = sorted(zip(p, y, strict=True), key=lambda t: -t[0])[:k]
    return round(sum(label for _, label in ranked) / len(ranked), 4) if ranked else None


def summarize(per_label: dict[str, J]) -> J:
    f1s = [(m["f1"], m["support"]) for m in per_label.values() if m["f1"] is not None]
    total = sum(s for _, s in f1s)
    return {
        "labels_evaluated": len(per_label),
        "macro_f1": sum(f for f, _ in f1s) / len(f1s) if f1s else None,
        "weighted_f1": sum(f * s for f, s in f1s) / total if total else None,
    }


def fit_anomaly(x: Any, seed: int) -> Any:
    return IsolationForest(n_estimators=150, contamination="auto", random_state=seed, n_jobs=1).fit(
        x
    )


def anomaly_scores(model: Any, x: Any) -> list[float]:
    return [float(-s) for s in model.score_samples(x)]  # higher = more unusual


def cluster_summary(x: Any) -> J:
    if len(x) < 20:
        return {"status": "insufficient_data", "reason": f"{len(x)} samples; HDBSCAN needs ≥ 20"}
    from sklearn.cluster import HDBSCAN

    model = HDBSCAN(min_cluster_size=5, copy=True)  # type: ignore[arg-type]
    labels = model.fit_predict(x)
    counts: dict[int, int] = {}
    for lab in labels:
        counts[int(lab)] = counts.get(int(lab), 0) + 1
    return {
        "status": "computed",
        "clusters": len([k for k in counts if k >= 0]),
        "noise_points": counts.get(-1, 0),
        "sizes": sorted((v for k, v in counts.items() if k >= 0), reverse=True),
    }


def save(obj: Any, path: Path, max_bytes: int) -> tuple[str, int]:
    buf = io.BytesIO()
    joblib.dump(obj, buf, compress=3)
    data = buf.getvalue()
    if len(data) > max_bytes:
        raise ValueError(f"model artifact {len(data)} bytes exceeds the {max_bytes}-byte limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return hashlib.sha256(data).hexdigest(), len(data)


def load(path: Path, sha256: str, root: Path) -> Any:
    """Load an artifact BIP itself wrote: path must be inside the model root and the content
    hash must match the registry (joblib/pickle must never load untrusted files)."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("model artifact outside the model directory")
    data = resolved.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("model artifact hash does not match the registry (refusing to load)")
    return joblib.load(io.BytesIO(data))


def finite(x: float | None) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x
