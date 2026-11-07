"""Evaluation harness: rule-based matcher vs logistic regression / gradient boosting on group-split labelled pairs.

PROTOCOL
  1. Pairs come from dataset.build_labelled_pairs (LIVE listings only, label provenance + trust tier on every pair).
  2. Models are trained ONLY if `sufficiency_report` says supervised matching is justified (or `exploratory=True`, which stamps the
     report "EXPLORATORY - NOT EVIDENCE"). Otherwise the report contains the rule baseline and an explicit refusal.
  3. Out-of-fold predictions via group k-fold (dataset.group_folds): no listing/product on both sides of any fold.
  4. Metrics are reported per trust tier. Anything computed on 'rule_derived' labels measures agreement with the rule matcher
     itself (circular) and is marked as such. Precision/recall at fixed precision, Brier score and reliability bins are given.
  5. A JSON report with a versioned run id, seed, library versions and dataset fingerprint is written under models/reports/.
Scores are ADVISORY. Nothing here changes a product assignment.
"""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

from . import ML_SCHEMA_VERSION, dataset, pairs as P, sufficiency

TARGET_PRECISION = 0.99
MIN_POSITIVES_FOR_METRICS = 30  # below this, rates are flagged underpowered
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[2] / "models" / "reports"


def dataset_fingerprint(pairs) -> str:
    h = hashlib.sha256()
    for p in pairs:
        h.update(f"{p.a_id},{p.b_id},{p.label},{p.label_source}\n".encode())
    return h.hexdigest()


def _prf(y, pred) -> dict:
    tp = sum(1 for a, b in zip(y, pred) if a == 1 and b == 1)
    fp = sum(1 for a, b in zip(y, pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y, pred) if a == 1 and b == 0)
    tn = sum(1 for a, b in zip(y, pred) if a == 0 and b == 0)
    return {"n": len(y), "positives": sum(y), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "underpowered": sum(y) < MIN_POSITIVES_FOR_METRICS}


def recall_at_precision(y, score, target: float = TARGET_PRECISION) -> dict:
    """Best recall over thresholds whose precision >= target (None if unreachable or no positives)."""
    pos = sum(y)
    if not pos:
        return {"target_precision": target, "recall": None, "threshold": None}
    best = (None, None)
    for t in sorted(set(score), reverse=True):
        pred = [1 if s >= t else 0 for s in score]
        tp = sum(1 for a, b in zip(y, pred) if a and b)
        fp = sum(1 for a, b in zip(y, pred) if not a and b)
        if tp + fp and tp / (tp + fp) >= target:
            r = tp / pos
            if best[0] is None or r > best[0]:
                best = (r, t)
    return {"target_precision": target, "recall": best[0], "threshold": best[1]}


def calibration(y, prob, bins: int = 5) -> dict:
    n = len(y)
    brier = sum((p - a) ** 2 for a, p in zip(y, prob)) / n if n else None
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(prob) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        rows.append({"bin": f"[{lo:.1f},{hi:.1f})", "n": len(idx),
                     "mean_predicted": sum(prob[i] for i in idx) / len(idx) if idx else None,
                     "fraction_positive": sum(y[i] for i in idx) / len(idx) if idx else None})
    return {"brier": brier, "reliability": rows}


def rule_baseline(pairs, by_id) -> dict:
    """The existing matcher's decision on each labelled pair: 'auto' = predicted same product; review/none = not auto."""
    dec = [P.rule_decision(by_id[p.a_id], by_id[p.b_id]) for p in pairs]
    out = {}
    for tier in ("human", "rule_derived", "all"):
        idx = [i for i, p in enumerate(pairs) if tier == "all" or p.trust == tier]
        y = [pairs[i].label for i in idx]
        m = _prf(y, [1 if dec[i][0] == "auto" else 0 for i in idx])
        m["review_decisions"] = sum(1 for i in idx if dec[i][0] == "review")
        m["recall_at_precision"] = recall_at_precision(y, [dec[i][1] for i in idx])
        if tier != "human":
            m["circular"] = True
            m["note"] = "labels derive from the rule matcher's own decisions/vetoes: agreement with itself, not accuracy"
        elif not idx:
            m["note"] = "no human-trust labels exist: accuracy of the rule matcher is NOT measurable from this data"
        out[tier] = m
    return out


def _make_models(seed: int):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {"logistic_regression": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, random_state=seed)),
            "gradient_boosting": lambda: GradientBoostingClassifier(n_estimators=100, max_depth=2, random_state=seed)}


def evaluate_models(pairs, by_id, group_of, k: int, seed: int) -> dict:
    import numpy as np
    names = P.MODEL_FEATURE_NAMES
    X = {(p.a_id, p.b_id): P.feature_vector(by_id[p.a_id], by_id[p.b_id], names) for p in pairs}
    out = {}
    folds = dataset.group_folds(pairs, group_of, k, seed)
    for name, make in _make_models(seed).items():
        oof_y, oof_p, skipped, dropped = [], [], 0, 0
        for train, test, d in folds:
            dropped += d
            if len({t.label for t in train}) < 2 or not test:
                skipped += 1
                continue
            m = make().fit(np.array([X[t.key()] for t in train]), [t.label for t in train])
            prob = m.predict_proba(np.array([X[t.key()] for t in test]))[:, list(m.classes_).index(1)]
            oof_y += [t.label for t in test]
            oof_p += [float(p) for p in prob]
        res = {"folds": len(folds), "folds_skipped_single_class_or_empty": skipped, "pairs_dropped_straddling_groups": dropped,
               "n_oof": len(oof_y), "features": list(names), "advisory_only": True}
        if oof_y and len(set(oof_y)) == 2:
            from sklearn.metrics import average_precision_score
            res.update({"average_precision": float(average_precision_score(oof_y, oof_p)),
                        "at_0.5": _prf(oof_y, [1 if p >= 0.5 else 0 for p in oof_p]),
                        "recall_at_precision": recall_at_precision(oof_y, oof_p), "calibration": calibration(oof_y, oof_p)})
        else:
            res["status"] = "no out-of-fold predictions with both classes"
        out[name] = res
    return out


def run_evaluation(s, seed: int = 0, k: int = 3, exploratory: bool = False, out_dir: str | Path | None = DEFAULT_REPORT_DIR,
                   reviewed_labels_path: str | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    listings = dataset.load_live_listings(s)
    by_id = {l.id: l for l in listings}
    group_of = {l.id: dataset.group_of_listing(l) for l in listings}
    reviewed = dataset.load_reviewed_labels(reviewed_labels_path, listings)
    decisions = dataset.load_candidate_decisions(s, set(by_id))
    pairs, stats = dataset.build_labelled_pairs(listings, decisions, reviewed)
    suff = sufficiency.sufficiency_report(s, reviewed_labels_path)
    fp = dataset_fingerprint(pairs)
    run_id = f"ml-{now.strftime('%Y%m%dT%H%M%SZ')}-s{seed}-{fp[:8]}"
    report = {"run_id": run_id, "ml_schema_version": ML_SCHEMA_VERSION, "seed": seed, "k_folds": k,
              "created_at": now.isoformat(), "python": platform.python_version(), "dataset_fingerprint": fp,
              "dataset": dataset.summarize(pairs), "label_build_stats": stats, "sufficiency": suff,
              "rule_baseline": rule_baseline(pairs, by_id) if pairs else {"status": "no labelled pairs"},
              "advisory_only": True, "never_auto_merge": True}
    try:
        import sklearn
        report["sklearn"] = sklearn.__version__
    except ImportError:
        report["sklearn"] = None
    justified = suff["justified"]["supervised_matching"]
    if report["sklearn"] is None:
        report["models"] = {"status": "skipped", "reason": "scikit-learn is not installed (optional dependency)"}
    elif not (justified or exploratory):
        report["models"] = {"status": "skipped", "reason": "refused: " + suff["verdict"]}
    elif len({p.label for p in pairs}) < 2:
        report["models"] = {"status": "skipped", "reason": "labelled pairs contain a single class"}
    else:
        report["models"] = evaluate_models(pairs, by_id, group_of, k, seed)
        report["models_status"] = "evidence" if justified else "EXPLORATORY - NOT EVIDENCE: data is below the sufficiency thresholds"
    if out_dir is not None:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{run_id}.json"
        path.write_text(json.dumps(report, indent=2, default=str, sort_keys=True))
        report["report_path"] = str(path)
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Evaluation harness on a SNAPSHOT COPY of the DB (never the live DB).")
    ap.add_argument("--db", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--exploratory", action="store_true", help="also fit models on insufficient data (stamped NOT EVIDENCE)")
    ap.add_argument("--out-dir", default=str(DEFAULT_REPORT_DIR))
    ap.add_argument("--reviewed-labels")
    a = ap.parse_args()
    with dataset.open_readonly_session(a.db) as sess:
        rep = run_evaluation(sess, a.seed, a.k, a.exploratory, a.out_dir, a.reviewed_labels)
    print(json.dumps({k: v for k, v in rep.items() if k != "sufficiency"}, indent=2, default=str))
    print("\nVERDICT:", rep["sufficiency"]["verdict"])
