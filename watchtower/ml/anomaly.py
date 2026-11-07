"""Price-anomaly detection on Observation history of LIVE listings. ADVISORY ONLY: nothing is written, nothing is ranked.

Two detectors:
  * robust baseline (per listing): modified z-score from median/MAD (Iglewicz-Hoaglin, |z| > 3.5) and Tukey IQR fences.
  * IsolationForest (fleet-wide): over per-observation features (log price vs the listing's own median, log step change).

REFUSAL. A statistical detector on a handful of points would only produce confident-looking noise, so each detector refuses
(status="refused", with a reason) unless history is sufficient:
  per listing : series is first reduced to ONE price per calendar day (the day's last observation; several crawls a day are
                one price level), then needs >= MIN_OBS such daily prices on >= MIN_DAYS days AND price variation: >=
                MIN_DISTINCT_PRICES distinct prices or >= MIN_PRICE_CHANGES day-to-day changes (a flat series has MAD = 0, so any
                small move would otherwise look infinitely anomalous). Scales are floored at MIN_REL_SCALE x median.
  fleet model : >= FOREST_MIN_LISTINGS qualifying listings and >= FOREST_MIN_OBS observations among them
Several crawls on one day are NOT multi-day history (they measure the same price level), hence the day requirement.
Missing prices (NULL) are skipped, never treated as 0. Prices are integer minor units; currencies are never mixed or converted.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchtower.models import Listing, Observation, Source

MIN_OBS = 8
MIN_DAYS = 7
Z_THRESHOLD = 3.5
IQR_K = 1.5
FOREST_MIN_LISTINGS = 30
FOREST_MIN_OBS = 300
MIN_DISTINCT_PRICES = 5
MIN_PRICE_CHANGES = 2
MIN_REL_SCALE = 0.05      # dispersion floor as a fraction of the median: moves below ~1.5x this (7.5%) are never flagged
MAD_CONSTANT = 0.6745     # Iglewicz-Hoaglin modified z-score constant
MEANAD_CONSTANT = 1.253314
IF_N_ESTIMATORS = 200


@dataclass
class PriceSeries:
    listing_id: int
    currency: str
    points: list[tuple[datetime, int]]  # (observed_at, price_minor), ascending, price not null


def load_price_series_with_stats(s: Session) -> tuple[dict[int, PriceSeries], dict]:
    """Per-listing price history for LIVE listings plus accounting of what was dropped. Only finite, positive, INTEGER prices
    (minor units) are kept; NULL is skipped (never 0); inf/NaN, fractional and non-positive values are counted and dropped, so
    one corrupt row can never crash a scan. A listing observed in more than one currency is dropped (no conversion)."""
    q = (select(Observation.listing_id, Observation.observed_at, Observation.price_minor, Observation.currency)
         .join(Listing, Listing.id == Observation.listing_id).join(Source, Source.id == Listing.source_id)
         .where(Source.kind == "live", Observation.price_minor.is_not(None))
         .order_by(Observation.listing_id, Observation.observed_at, Observation.id))
    dropped = {"non_finite": 0, "non_integer": 0, "non_positive": 0}
    acc: dict[int, list] = {}
    for lid, at, price, cur in s.execute(q):
        if isinstance(price, float) and not math.isfinite(price):
            dropped["non_finite"] += 1
            continue
        if isinstance(price, float):
            if not price.is_integer():
                dropped["non_integer"] += 1
                continue
            price = int(price)
        if not isinstance(price, int):
            dropped["non_integer"] += 1
            continue
        if price <= 0:
            dropped["non_positive"] += 1
            continue
        acc.setdefault(lid, []).append((at, price, cur))
    out = {}
    mixed = 0
    for lid, rows in acc.items():
        if len({r[2] for r in rows}) == 1:
            out[lid] = PriceSeries(lid, rows[0][2], [(r[0], r[1]) for r in rows])
        else:
            mixed += 1
    return out, {"rows_dropped": dropped, "listings_dropped_mixed_currency": mixed}


def load_price_series(s: Session) -> dict[int, PriceSeries]:
    return load_price_series_with_stats(s)[0]


def daily_last(points) -> list[tuple[datetime, int]]:
    """One price per calendar day: the day's LAST observation (points must be ascending)."""
    last: dict = {}
    for at, price in points:
        last[at.date()] = (at, price)
    return [last[d] for d in sorted(last)]


def n_days(points) -> int:
    return len({p[0].date() for p in points})


def series_check(points) -> tuple[str, str]:
    """(code, reason); code is 'ok' or why the history is refused. See the module docstring for the rules."""
    n, d = len(points), n_days(points)
    if n < MIN_OBS:
        return "too_few_observations", f"only {n} observation(s); need >= {MIN_OBS}"
    if d < MIN_DAYS:
        return "too_few_days", f"observations span {d} distinct day(s); need >= {MIN_DAYS}"
    daily = daily_last(points)
    if len(daily) < MIN_OBS:
        return "too_few_days", f"only {len(daily)} daily price(s) after keeping one per day; need >= {MIN_OBS}"
    prices = [p for _, p in daily]
    changes = sum(1 for x, y in zip(prices, prices[1:]) if x != y)
    if len(set(prices)) < MIN_DISTINCT_PRICES and changes < MIN_PRICE_CHANGES:
        return "low_variation", (f"price variation too low: {len(set(prices))} distinct price(s) and {changes} change(s); need >= "
                                 f"{MIN_DISTINCT_PRICES} distinct prices or >= {MIN_PRICE_CHANGES} changes")
    return "ok", "ok"


def series_ok(points) -> tuple[bool, str]:
    code, why = series_check(points)
    return code == "ok", why


def robust_z(values: list[float], min_rel_scale: float = MIN_REL_SCALE) -> list[float]:
    """Modified z = 0.6745 * (x - median) / MAD, with the MAD floored at `min_rel_scale` x |median| (and 1 minor unit) so a
    flat or near-flat series cannot turn a small move into a huge z. If MAD == 0 the mean absolute deviation is used first."""
    med = statistics.median(values)
    floor = max(min_rel_scale * abs(med), 1.0)
    mad = statistics.median(abs(v - med) for v in values)
    if mad <= 0:
        meanad = sum(abs(v - med) for v in values) / len(values)
        mad = MEANAD_CONSTANT * meanad * MAD_CONSTANT  # keeps z comparable to the MAD branch
    mad = max(mad, floor)
    return [MAD_CONSTANT * (v - med) / mad for v in values]


def iqr_fences(values: list[float], k: float = IQR_K, min_rel_scale: float = MIN_REL_SCALE) -> tuple[float, float]:
    q = statistics.quantiles(values, n=4, method="inclusive")
    iqr = max(q[2] - q[0], min_rel_scale * abs(statistics.median(values)), 1.0)
    return q[0] - k * iqr, q[2] + k * iqr


@dataclass
class Verdict:
    status: str  # ok | refused
    reason: str
    code: str = "ok"  # series_check code
    n_obs: int = 0
    n_days: int = 0
    flags: list[dict] = field(default_factory=list)
    advisory: bool = True


def detect_series(points: list[tuple[datetime, int]], z_threshold: float = Z_THRESHOLD, iqr_k: float = IQR_K) -> Verdict:
    code, why = series_check(points)
    if code != "ok":
        return Verdict("refused", why, code, n_obs=len(points), n_days=n_days(points))
    daily = daily_last(points)  # one price per day: repeated crawls of one day must not count as independent evidence
    vals = [float(p[1]) for p in daily]
    z = robust_z(vals)
    lo, hi = iqr_fences(vals, iqr_k)
    flags = []
    for (at, price), zi in zip(daily, z):
        zf, iq = abs(zi) > z_threshold, (price < lo or price > hi)
        if zf or iq:
            flags.append({"observed_at": at.isoformat(), "price_minor": price, "robust_z": round(zi, 2),
                          "z_flag": zf, "iqr_flag": iq})
    return Verdict("ok", f"{len(flags)} flagged of {len(daily)}", "ok", n_obs=len(daily), n_days=len(daily), flags=flags)


def _forest_rows(series: dict[int, PriceSeries]):
    X, index = [], []
    for lid in sorted(series):
        pts = series[lid].points
        med = statistics.median(p[1] for p in pts)
        prev = None
        for at, price in pts:
            X.append([math.log(price / med), 0.0 if prev is None else math.log(price / prev)])
            index.append((lid, at, price))
            prev = price
    return X, index


def isolation_forest_scan(series: dict[int, PriceSeries], contamination: float | Literal["auto"] = "auto", seed: int = 0) -> dict:
    """Fleet-wide IsolationForest over qualifying listings (daily-deduplicated). Refuses (no model fitted) when data is
    insufficient. contamination="auto" (default) does not force any fraction of flags; a float FORCES ~that fraction of
    observations to be flagged whatever the data looks like, which is reported as `forced_flag_fraction`."""
    qual = {lid: PriceSeries(lid, ps.currency, daily_last(ps.points)) for lid, ps in series.items() if series_ok(ps.points)[0]}
    n_obs = sum(len(ps.points) for ps in qual.values())
    if len(qual) < FOREST_MIN_LISTINGS or n_obs < FOREST_MIN_OBS:
        return {"status": "refused", "advisory": True, "model": None,
                "reason": (f"{len(qual)} listing(s) with sufficient history ({n_obs} observations); need >= {FOREST_MIN_LISTINGS} "
                           f"listings and >= {FOREST_MIN_OBS} observations (each listing >= {MIN_OBS} obs on >= {MIN_DAYS} days)"),
                "listings_total": len(series), "listings_qualifying": len(qual)}
    from sklearn.ensemble import IsolationForest  # lazy: optional dependency
    X, index = _forest_rows(qual)
    clf = IsolationForest(n_estimators=IF_N_ESTIMATORS, contamination=contamination, random_state=seed).fit(X)  # pyright: ignore[reportArgumentType] - sklearn accepts "auto" | float; its stub says str
    pred, score = clf.predict(X), clf.score_samples(X)
    flagged = [{"listing_id": i[0], "observed_at": i[1].isoformat(), "price_minor": i[2], "score": round(float(sc), 4)}
               for i, p, sc in zip(index, pred, score) if p == -1]
    return {"status": "ok", "advisory": True, "model": "IsolationForest", "seed": seed, "contamination": contamination,
            "forced_flag_fraction": None if contamination == "auto" else contamination,
            "flagged_fraction": len(flagged) / n_obs,
            "listings_qualifying": len(qual), "n_obs": n_obs, "flagged": flagged}


_REFUSAL_LABEL = {"too_few_observations": "too few observations", "too_few_days": "history spans too few distinct days",
                  "low_variation": "insufficient price variation"}


def scan_database(s: Session) -> dict:
    """Run both detectors over every live listing; returns counts of refusals (with reasons) and any flags. Read-only."""
    series, load_stats = load_price_series_with_stats(s)
    refusals: dict[str, int] = {}
    flagged, ok_n = [], 0
    for lid, ps in sorted(series.items()):
        v = detect_series(ps.points)
        if v.status == "refused":
            key = _REFUSAL_LABEL[v.code]
            refusals[key] = refusals.get(key, 0) + 1
        else:
            ok_n += 1
            if v.flags:
                flagged.append({"listing_id": lid, "flags": v.flags})
    if not series:
        forest = {"status": "refused", "reason": "no live price history", "model": None}
    else:
        try:
            forest = isolation_forest_scan(series)
        except ImportError as e:  # scikit-learn is optional; the robust baseline above still ran
            forest = {"status": "refused", "advisory": True, "model": None, "reason": f"scikit-learn is not available ({e})"}
    return {"listings_with_prices": len(series), "listings_scanned": ok_n, "refused_by_reason": refusals,
            "price_rows_dropped": load_stats["rows_dropped"],
            "listings_dropped_mixed_currency": load_stats["listings_dropped_mixed_currency"],
            "robust_flagged_listings": flagged, "isolation_forest": forest}
