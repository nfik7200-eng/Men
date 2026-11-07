"""Honest answer to "does the data justify ML yet?". Read-only; never trains anything.

Thresholds are deliberately conservative rules of thumb, documented in docs/ML.md. Only HUMAN-trust labels count toward the
supervised requirement: labels produced by the rule matcher itself are circular (see dataset.py).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from watchtower.models import Listing, Observation, Source

from . import anomaly, dataset

SUPERVISED_MIN_HUMAN_POS = 100
SUPERVISED_MIN_HUMAN_NEG = 100
SUPERVISED_MIN_GROUPS = 30       # distinct products among human-labelled pairs (so group-split CV is meaningful)
FORECAST_MIN_DAYS = 90           # distinct calendar days of live price history
FORECAST_MIN_LISTINGS = 30       # listings with >= 30 observations on >= 30 days


def sufficiency_report(s: Session, reviewed_labels_path: str | None = None) -> dict:
    listings = dataset.load_live_listings(s)
    live_ids = {l.id for l in listings}
    reviewed = dataset.load_reviewed_labels(reviewed_labels_path, listings)
    decisions = dataset.load_candidate_decisions(s, live_ids)
    pairs, build_stats = dataset.build_labelled_pairs(listings, decisions, reviewed)
    summ = dataset.summarize(pairs)
    group_of = {l.id: dataset.group_of_listing(l) for l in listings}
    human_groups = {group_of[i] for p in pairs if p.trust == "human" for i in (p.a_id, p.b_id)}

    sources = [(k, n) for k, n in s.execute(select(Source.key, func.count(Listing.id)).join(Listing, Listing.source_id == Source.id)
                                            .where(Source.kind == "live").group_by(Source.key).order_by(Source.key))]
    prod_sources: dict[int, set[int]] = {}
    for l in listings:
        if l.product_id:
            prod_sources.setdefault(l.product_id, set()).add(l.source_id)
    cross = sum(1 for v in prod_sources.values() if len(v) >= 2)

    series = anomaly.load_price_series(s)
    all_days = {at.date() for ps in series.values() for at, _ in ps.points}
    max_days = max((anomaly.n_days(ps.points) for ps in series.values()), default=0)
    max_obs = max((len(ps.points) for ps in series.values()), default=0)
    n_anom = sum(anomaly.series_ok(ps.points)[0] for ps in series.values())
    n_fc = sum(len(ps.points) >= 30 and anomaly.n_days(ps.points) >= 30 for ps in series.values())
    live_obs = s.scalar(select(func.count(Observation.id)).join(Listing, Listing.id == Observation.listing_id)
                        .join(Source, Source.id == Listing.source_id).where(Source.kind == "live"))

    sup_ok = (summ["positives_human"] >= SUPERVISED_MIN_HUMAN_POS and summ["negatives_human"] >= SUPERVISED_MIN_HUMAN_NEG
              and len(human_groups) >= SUPERVISED_MIN_GROUPS)
    anom_ok = n_anom > 0
    forest_ok = n_anom >= anomaly.FOREST_MIN_LISTINGS and sum(len(ps.points) for ps in series.values()
                                                             if anomaly.series_ok(ps.points)[0]) >= anomaly.FOREST_MIN_OBS
    fc_ok = len(all_days) >= FORECAST_MIN_DAYS and n_fc >= FORECAST_MIN_LISTINGS
    rep = {
        "live_sources": dict(sources),
        "live_listings": len(listings),
        "cross_merchant_products": cross,
        "labelled_pairs": summ,
        "label_build_stats": build_stats,
        "human_labelled_groups": len(human_groups),
        "live_observations": int(live_obs or 0),
        "distinct_observation_days_all_listings": len(all_days),
        "max_distinct_days_single_listing": max_days,
        "max_observations_single_listing": max_obs,
        "listings_meeting_anomaly_history": n_anom,
        "listings_meeting_forecast_history": n_fc,
        "thresholds": {"supervised": {"human_pos": SUPERVISED_MIN_HUMAN_POS, "human_neg": SUPERVISED_MIN_HUMAN_NEG,
                                      "groups": SUPERVISED_MIN_GROUPS},
                       "anomaly_per_listing": {"obs": anomaly.MIN_OBS, "days": anomaly.MIN_DAYS},
                       "anomaly_isolation_forest": {"listings": anomaly.FOREST_MIN_LISTINGS, "obs": anomaly.FOREST_MIN_OBS},
                       "forecasting": {"days": FORECAST_MIN_DAYS, "listings_with_30_obs_30_days": FORECAST_MIN_LISTINGS}},
        "justified": {"supervised_matching": sup_ok, "anomaly_robust_baseline": anom_ok,
                      "anomaly_isolation_forest": forest_ok, "price_forecasting": fc_ok},
    }
    rep["verdict"] = _verdict(rep)
    return rep


def _verdict(r: dict) -> str:
    j, lp = r["justified"], r["labelled_pairs"]
    parts = []
    if not j["supervised_matching"]:
        parts.append(f"supervised matching is NOT justified: {lp['positives_human']} human-verified positive(s) / "
                     f"{lp['negatives_human']} human-verified negative(s) (need >= {SUPERVISED_MIN_HUMAN_POS} each); "
                     f"{lp['positives_rule_derived']} + {lp['negatives_rule_derived']} rule-derived labels are circular and do not count")
    if not j["anomaly_robust_baseline"]:
        parts.append(f"anomaly detection is NOT justified: no listing has {anomaly.MIN_OBS}+ observations on {anomaly.MIN_DAYS}+ days "
                     f"(best: {r['max_observations_single_listing']} obs / {r['max_distinct_days_single_listing']} day(s))")
    elif not j["anomaly_isolation_forest"]:
        parts.append("IsolationForest is NOT justified (too few qualifying listings); per-listing robust baseline only")
    if not j["price_forecasting"]:
        parts.append(f"forecasting is NOT justified: {r['distinct_observation_days_all_listings']} distinct observation day(s), need >= {FORECAST_MIN_DAYS}")
    if not parts:
        return "Data meets the documented thresholds; models may be trained, still advisory-only and subject to group-split evaluation."
    return "Insufficient for supervised models and forecasting; harness + rules baseline only. " + "; ".join(parts) + "."


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Sufficiency report on a SNAPSHOT COPY of the DB (never the live DB).")
    ap.add_argument("--db", required=True)
    ap.add_argument("--reviewed-labels")
    a = ap.parse_args()
    with dataset.open_readonly_session(a.db) as sess:
        print(json.dumps(sufficiency_report(sess, a.reviewed_labels), indent=2, default=str))
