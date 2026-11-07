"""Evaluation harness: rule-based matcher vs logistic regression / gradient boosting on group-split labelled pairs.

PROTOCOL
  1. Pairs come from dataset.build_labelled_pairs (LIVE listings only, label provenance + trust tier on every pair).
  2. Models are trained ONLY if `sufficiency_report` says supervised matching is justified (or `exploratory=True`, which stamps the
     report "EXPLORATORY - NOT EVIDENCE"). Otherwise the report contains the rule baseline and an explicit refusal.
  3. Out-of-fold predictions via group k-fold (dataset.group_folds). Groups = product listings UNIONED with identical
     (brand, name-signature) clusters, so near-duplicate listings share a fold (dataset.build_group_map); the duplication rate
     across folds is reported (0.0 expected).
  4. Out-of-fold predictions are evaluated PER TRUST TIER. Only the 'human' tier can ever be evidence; 'rule_derived' blocks are
     circular and a pooled block (human + rule + propagated) is never evidence. Every block carries `circular`, `underpowered`
     and `evidence_eligible`. See `decide_models_status`.
  5. TASK TRIVIALITY ABLATION (`ablation_single_feature_auc`): the ROC AUC of each model feature on its own. Negatives are
     rule-vetoed pairs, and every ambiguous blocked pair is excluded, so name equality alone separates the classes. When any
     single feature reaches AUC >= TRIVIAL_AUC the run is flagged `task_trivial` in the headline: the model is then not
     demonstrating anything beyond that feature.
  6. recall-at-precision picks its threshold on the same labels it is scored on (an 'oracle_threshold', optimistic); it comes with
     a bootstrap CI and is flagged when too few predicted positives exist to certify the target. A nested variant (threshold chosen
     on the training folds, applied to the test fold) is reported next to it.
  7. A JSON report with a versioned run id, seed, library versions, code commit and a CONTENT-based dataset fingerprint is
     written under models/reports/.
Scores are ADVISORY. Nothing here changes a product assignment.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import random
import subprocess
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from . import ML_SCHEMA_VERSION, dataset, pairs as P, sufficiency

TARGET_PRECISION = 0.99
MIN_POSITIVES_FOR_METRICS = 30  # below this (positives OR negatives), rates are flagged underpowered
MIN_NEGATIVES_FOR_METRICS = 30
TRIVIAL_AUC = 0.95              # a single feature reaching this AUC makes the task trivial
BOOTSTRAP_N = 200
BOOTSTRAP_SEED = 0
CALIBRATION_BINS = 5
DECISION_THRESHOLD = 0.5
GB_N_ESTIMATORS = 100
GB_MAX_DEPTH = 2
LR_MAX_ITER = 1000
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[2] / "models" / "reports"
REPO_ROOT = Path(__file__).resolve().parents[2]


def dataset_fingerprint(pairs, by_id=None) -> str:
    """SHA-256 over the labelled pairs. With `by_id` (listing id -> listing) it is CONTENT based: the listing fields the features
    read are hashed too, so a retitled/repriced listing changes the fingerprint (id+label only would not)."""
    h = hashlib.sha256()
    for p in pairs:
        h.update(f"{p.a_id},{p.b_id},{p.label},{p.label_source},{p.trust}\n".encode())
        if by_id is not None:
            for i in (p.a_id, p.b_id):
                v = P.view(by_id[i])
                h.update(json.dumps([v.id, v.source_id, v.product_id, v.title, v.brand, v.category, v.price_minor, v.currency,
                                     v.match_status, sorted((v.specs or {}).items(), key=str), v.gtin, v.mpn],
                                    default=str).encode())
    return h.hexdigest()


def library_versions() -> dict:
    out = {}
    for name in ("scikit-learn", "numpy", "scipy", "joblib"):
        try:
            out["sklearn" if name == "scikit-learn" else name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out["sklearn" if name == "scikit-learn" else name] = None
    return out


def code_commit() -> dict:
    """Git commit of the code that produced a report (None when git or the repository is unavailable)."""
    def git(*args):
        try:
            r = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    commit = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--", "watchtower", "tests", "scripts")
    return {"code_commit": commit, "code_dirty": None if dirty is None else bool(dirty)}


def _prf(y, pred) -> dict:
    tp = sum(1 for a, b in zip(y, pred) if a == 1 and b == 1)
    fp = sum(1 for a, b in zip(y, pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y, pred) if a == 1 and b == 0)
    tn = sum(1 for a, b in zip(y, pred) if a == 0 and b == 0)
    pos = sum(y)
    return {"n": len(y), "positives": pos, "negatives": len(y) - pos, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "underpowered": pos < MIN_POSITIVES_FOR_METRICS or len(y) - pos < MIN_NEGATIVES_FOR_METRICS}


# ------------------------------------------------------------------------------------------------ task-triviality ablation
def single_feature_auc(y, values) -> float | None:
    """Orientation-free ROC AUC of one feature used alone as a score: max(AUC, 1-AUC) (Mann-Whitney with mid-ranks).
    0.5 = carries nothing, 1.0 = separates the classes perfectly. None if only one class is present."""
    pos = sum(1 for a in y if a == 1)
    neg = len(y) - pos
    if not pos or not neg:
        return None
    order = sorted(range(len(y)), key=lambda i: values[i])
    ranks = [0.0] * len(y)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for q in range(i, j + 1):
            ranks[order[q]] = (i + j) / 2 + 1
        i = j + 1
    auc = (sum(r for r, a in zip(ranks, y) if a == 1) - pos * (pos + 1) / 2) / (pos * neg)
    return max(auc, 1 - auc)


def ablation_block(y, cols: dict) -> dict:
    """Per-feature single-feature AUC and the `task_trivial` flag (any feature >= TRIVIAL_AUC)."""
    feats = {n: single_feature_auc(y, v) for n, v in cols.items()}
    feats = {n: a for n, a in feats.items() if a is not None}
    best = max(feats, key=lambda n: (feats[n], n)) if feats else None
    return {"n_pairs": len(y), "positives": sum(y), "features": {n: round(a, 4) for n, a in sorted(feats.items())},
            "max_feature": best, "max_auc": round(feats[best], 4) if best else None, "threshold": TRIVIAL_AUC,
            "task_trivial": bool(best and feats[best] >= TRIVIAL_AUC)}


def ablation_report(pairs, feats: dict) -> dict | None:
    """Pooled and per-trust-tier ablation. `feats`: pair key -> full feature dict. None when only one class exists."""
    def block(ps):
        y = [p.label for p in ps]
        if len(set(y)) < 2:
            return None
        return ablation_block(y, {n: [feats[p.key()][n] for p in ps] for n in P.MODEL_FEATURE_NAMES})
    pooled = block(pairs)
    if pooled is None:
        return None
    return {"pooled": pooled,
            "by_trust_tier": {tier: block([p for p in pairs if p.trust == tier]) for tier in dataset.TRUST_TIERS},
            "note": ("single-feature ROC AUC of each model feature on its own. Negatives are rule-vetoed pairs only: every "
                     "ambiguous blocked pair is excluded (selection bias), so near-identical names almost define the label")}


def headline(abl: dict | None) -> str:
    if abl is None:
        return "task_trivial=unknown: labelled pairs contain a single class"
    b = abl["pooled"]
    if b["task_trivial"]:
        return (f"TASK TRIVIAL: single feature '{b['max_feature']}' alone reaches ROC AUC {b['max_auc']} (>= {TRIVIAL_AUC}) on the "
                f"labelled pairs. Negatives are only rule-vetoed pairs and all ambiguous candidates are excluded; model scores "
                f"below say nothing about real-world accuracy.")
    return f"task_trivial=false (max single-feature AUC {b['max_auc']} for '{b['max_feature']}' < {TRIVIAL_AUC})"


# ------------------------------------------------------------------------------------------------ recall at precision
def _best_recall(y, score, target: float):
    """(recall, threshold, n_predicted_positives) of the best operating point with precision >= target, scanning thresholds
    in one sorted pass (ties are taken together). (None, None, 0) if no such point exists or there are no positives."""
    pos = sum(y)
    if not pos:
        return None, None, 0
    order = sorted(range(len(y)), key=lambda i: -score[i])
    tp = fp = 0
    best = (None, None, 0)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and score[order[j]] == score[order[i]]:
            tp += y[order[j]] == 1
            fp += y[order[j]] != 1
            j += 1
        if tp / (tp + fp) >= target and (best[0] is None or tp / pos >= best[0]):
            best = (tp / pos, score[order[i]], tp + fp)
        i = j
    return best


def recall_at_precision(y, score, target: float = TARGET_PRECISION) -> dict:
    """Best recall over thresholds whose precision >= target. The threshold is chosen ON THE SAME LABELS it is scored on, so it is
    an `oracle_threshold` (optimistic). Includes a deterministic bootstrap 95% CI of the whole procedure (threshold re-chosen
    per resample), and `underpowered_for_target` when fewer predicted positives exist than are needed to certify the target
    (at 0.99 precision: >= 100, i.e. the allowed error must be able to be < 1 pair)."""
    need = math.ceil(1 / (1 - target)) if target < 1 else None
    base = {"target_precision": target, "threshold_selection": "oracle_threshold", "min_predicted_positives_to_certify": need}
    if not sum(y):
        return {**base, "recall": None, "threshold": None, "n_predicted_positives": 0, "recall_ci95": None,
                "underpowered_for_target": True}
    r, thr, npp = _best_recall(y, score, target)
    rng = random.Random(BOOTSTRAP_SEED)
    n, boots = len(y), []
    for _ in range(BOOTSTRAP_N):
        idx = [rng.randrange(n) for _ in range(n)]
        br = _best_recall([y[i] for i in idx], [score[i] for i in idx], target)[0]
        boots.append(0.0 if br is None else br)  # no operating point reaches the target in this resample
    boots.sort()
    ci = [boots[int(0.025 * (BOOTSTRAP_N - 1))], boots[int(math.ceil(0.975 * (BOOTSTRAP_N - 1)))]]
    return {**base, "recall": r, "threshold": thr, "n_predicted_positives": npp, "recall_ci95": ci,
            "underpowered_for_target": need is None or npp < need}


def apply_train_threshold(y_train, s_train, y_test, s_test, target: float = TARGET_PRECISION) -> dict:
    """Nested variant: choose the threshold on TRAINING scores/labels only, then apply it to the test fold."""
    _r, thr, _n = _best_recall(y_train, s_train, target)
    if thr is None:
        return {"threshold_selection": "nested_train_threshold", "threshold": None, **_prf(y_test, [0] * len(y_test))}
    return {"threshold_selection": "nested_train_threshold", "threshold": thr,
            **_prf(y_test, [1 if s >= thr else 0 for s in s_test])}


def calibration(y, prob, bins: int = CALIBRATION_BINS) -> dict:
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
    for tier in (*dataset.TRUST_TIERS, "all"):
        idx = [i for i, p in enumerate(pairs) if tier == "all" or p.trust == tier]
        y = [pairs[i].label for i in idx]
        m = _prf(y, [1 if dec[i][0] == "auto" else 0 for i in idx])
        m["review_decisions"] = sum(1 for i in idx if dec[i][0] == "review")
        m["recall_at_precision"] = recall_at_precision(y, [dec[i][1] for i in idx])
        m["circular"] = tier in ("rule_derived", "all")
        if m["circular"]:
            m["note"] = "labels derive from the rule matcher's own decisions/vetoes: agreement with itself, not accuracy"
        elif tier == "human_propagated":
            m["note"] = "a human decision carried to pairs the human did not look at: not an independent label"
        elif not idx:
            m["note"] = "no human-trust labels exist: accuracy of the rule matcher is NOT measurable from this data"
        out[tier] = m
    return out


# ------------------------------------------------------------------------------------------------ models
def _make_models(seed: int):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {"logistic_regression": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=LR_MAX_ITER, random_state=seed)),
            "gradient_boosting": lambda: GradientBoostingClassifier(n_estimators=GB_N_ESTIMATORS, max_depth=GB_MAX_DEPTH, random_state=seed)}


def _tier_block(rows, tier: str, nested=None) -> dict:
    y = [r[0] for r in rows]
    prob = [r[1] for r in rows]
    pos = sum(y)
    both = 0 < pos < len(y)
    under = pos < MIN_POSITIVES_FOR_METRICS or len(y) - pos < MIN_NEGATIVES_FOR_METRICS
    circular = tier == "rule_derived" or (tier == "pooled" and any(r[2] == "rule_derived" for r in rows))
    blk = {"n": len(y), "positives": pos, "negatives": len(y) - pos, "circular": circular, "underpowered": under,
           "evidence_eligible": bool(tier == "human" and both and not under and not circular)}
    if tier == "pooled":
        blk["note"] = "pooled across trust tiers: mixes independent and circular labels, never evidence"
    elif tier == "rule_derived":
        blk["note"] = "labels produced by the rule matcher itself: measures imitation of the rules, not accuracy"
    elif tier == "human_propagated":
        blk["note"] = "human decision carried to pairs no human inspected: not an independent label"
    if not both:
        blk["status"] = "single class: no ranking metrics"
        return blk
    from sklearn.metrics import average_precision_score
    blk.update({"average_precision": float(average_precision_score(y, prob)),
                f"at_{DECISION_THRESHOLD}": _prf(y, [1 if p >= DECISION_THRESHOLD else 0 for p in prob]),
                "recall_at_precision": recall_at_precision(y, prob), "calibration": calibration(y, prob)})
    if nested:
        ny = [r[0] for r in nested]
        blk["nested_train_threshold"] = {**_prf(ny, [r[1] for r in nested]), "threshold_selection": "nested_train_threshold"}
    return blk


def tier_blocks(oof, nested=None) -> dict:
    """oof: list of (label, probability, trust). Returns {tier: metrics block, ..., 'pooled': block}; see `_tier_block`."""
    out = {}
    for tier in dataset.TRUST_TIERS:
        rows = [r for r in oof if r[2] == tier]
        if rows:
            out[tier] = _tier_block(rows, tier, [r for r in (nested or []) if r[2] == tier] or None)
    if oof:
        out["pooled"] = _tier_block(oof, "pooled", nested)
    return out


def decide_models_status(justified: bool, exploratory: bool, blocks: dict, human_ablation: dict | None) -> str:
    """The ONLY place that can produce an 'evidence' stamp. Requires: sufficiency met, not exploratory, an eligible HUMAN-tier
    out-of-fold block (both classes, not underpowered, not circular) and a human-tier ablation that is not trivial. Pooled and
    rule-derived blocks can never produce it."""
    if exploratory or not justified:
        return "EXPLORATORY - NOT EVIDENCE: data is below the sufficiency thresholds"
    h = blocks.get("human")
    if not h or not h.get("evidence_eligible"):
        return ("NOT EVIDENCE: sufficiency thresholds are met but the human-tier out-of-fold evaluation is missing or underpowered; "
                "pooled and rule-derived blocks are never evidence")
    if human_ablation is None or human_ablation.get("task_trivial"):
        return "NOT EVIDENCE: the human-tier task is trivial (or its ablation is unavailable): a single feature separates the classes"
    return "evidence (human tier only; read models.<name>.by_trust_tier.human, never the pooled block)"


def evaluate_models(pairs, by_id, group_of, k: int, seed: int, folds=None, feats=None) -> dict:
    import numpy as np
    names = P.MODEL_FEATURE_NAMES
    X = {p.key(): ([feats[p.key()][n] for n in names] if feats else P.feature_vector(by_id[p.a_id], by_id[p.b_id], names))
         for p in pairs}
    out = {}
    folds = folds if folds is not None else dataset.group_folds(pairs, group_of, k, seed)
    for name, make in _make_models(seed).items():
        oof, nested, skipped = [], [], 0
        for train, test, _d in folds:
            if len({t.label for t in train}) < 2 or not test:
                skipped += 1
                continue
            m = make().fit(np.array([X[t.key()] for t in train]), [t.label for t in train])
            col = list(m.classes_).index(1)
            prob = m.predict_proba(np.array([X[t.key()] for t in test]))[:, col]
            tr_prob = m.predict_proba(np.array([X[t.key()] for t in train]))[:, col]
            _r, thr, _n = _best_recall([t.label for t in train], [float(x) for x in tr_prob], TARGET_PRECISION)
            for t, pr in zip(test, prob):
                oof.append((t.label, float(pr), t.trust))
                nested.append((t.label, 1 if thr is not None and float(pr) >= thr else 0, t.trust))
        res = {"folds": len(folds), "folds_skipped_single_class_or_empty": skipped, "n_oof": len(oof),
               "features": list(names), "advisory_only": True}
        if len({r[0] for r in oof}) == 2:
            res["by_trust_tier"] = tier_blocks(oof, nested)
        else:
            res["status"] = "no out-of-fold predictions with both classes"
            res["by_trust_tier"] = tier_blocks(oof, nested)
        out[name] = res
    return out


def run_evaluation(s, seed: int = 0, k: int = 3, exploratory: bool = False, out_dir: str | Path | None = DEFAULT_REPORT_DIR,
                   reviewed_labels_path: str | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    listings = dataset.load_live_listings(s)
    by_id = {l.id: l for l in listings}
    group_of = dataset.build_group_map(listings)
    reviewed = dataset.load_reviewed_labels(reviewed_labels_path, listings)
    decisions = dataset.load_candidate_decisions(s, set(by_id))
    pairs, stats = dataset.build_labelled_pairs(listings, decisions, reviewed)
    suff = sufficiency.sufficiency_report(s, reviewed_labels_path)
    fp = dataset_fingerprint(pairs, by_id)
    run_id = f"ml-{now.strftime('%Y%m%dT%H%M%SZ')}-s{seed}-{fp[:8]}"
    feats = {p.key(): P.pair_features(by_id[p.a_id], by_id[p.b_id]) for p in pairs}
    abl = ablation_report(pairs, feats) if pairs else None
    folds = dataset.group_folds(pairs, group_of, k, seed) if pairs else []
    key_of = dataset.name_signature_keys(listings)
    naive = {l.id: dataset.group_of_listing(l) for l in listings}
    sizes: dict[str, int] = {}
    for g in group_of.values():
        sizes[g] = sizes.get(g, 0) + 1
    keys_count: dict = {}
    for key in key_of.values():
        if key is not None:
            keys_count[key] = keys_count.get(key, 0) + 1
    report = {"run_id": run_id, "ml_schema_version": ML_SCHEMA_VERSION, "seed": seed, "k_folds": k,
              "created_at": now.isoformat(), "python": platform.python_version(), "libraries": library_versions(),
              **code_commit(), "dataset_fingerprint": fp, "dataset_fingerprint_kind": "content (pairs + listing fields)",
              "headline": headline(abl), "task_trivial": None if abl is None else abl["pooled"]["task_trivial"],
              "ablation_single_feature_auc": abl,
              "dataset": dataset.summarize(pairs), "label_build_stats": stats, "sufficiency": suff,
              "grouping": {"groups": len(set(group_of.values())), "listings": len(group_of),
                           "largest_group_listings": max(sizes.values(), default=0),
                           "listings_with_identical_brand_signature_elsewhere":
                               sum(c for c in keys_count.values() if c > 1),
                           "near_duplicate_listing_rate": (sum(c for c in keys_count.values() if c > 1) / len(listings)) if listings else 0.0,
                           "cross_fold_duplicate_rate": dataset.cross_fold_duplicate_rate(folds, key_of),
                           "cross_fold_duplicate_rate_if_grouped_by_product_only":
                               dataset.cross_fold_duplicate_rate(dataset.group_folds(pairs, naive, k, seed), key_of) if pairs else 0.0},
              "folds": dataset.fold_summary(pairs, folds),
              "rule_baseline": rule_baseline(pairs, by_id) if pairs else {"status": "no labelled pairs"},
              "advisory_only": True, "never_auto_merge": True}
    report["sklearn"] = report["libraries"]["sklearn"]
    justified = suff["justified"]["supervised_matching"]
    if report["sklearn"] is None:
        report["models"] = {"status": "skipped", "reason": "scikit-learn is not installed (optional dependency)"}
    elif not (justified or exploratory):
        report["models"] = {"status": "skipped", "reason": "refused: " + suff["verdict"]}
    elif len({p.label for p in pairs}) < 2:
        report["models"] = {"status": "skipped", "reason": "labelled pairs contain a single class"}
    else:
        report["models"] = evaluate_models(pairs, by_id, group_of, k, seed, folds, feats)
        human_abl = (abl or {}).get("by_trust_tier", {}).get("human")
        statuses = [decide_models_status(justified, exploratory, m["by_trust_tier"], human_abl) for m in report["models"].values()]
        report["models_status"] = next((st for st in statuses if not st.startswith("evidence")), statuses[0])
        if report["task_trivial"]:
            report["models_status"] += " | TASK TRIVIAL (see headline)"
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
    print("HEADLINE:", rep["headline"], "\n")
    print(json.dumps({k: v for k, v in rep.items() if k not in ("sufficiency", "headline")}, indent=2, default=str))
    print("\nVERDICT:", rep["sufficiency"]["verdict"])
