"""Price-anomaly detection on Observation history of LIVE listings. ADVISORY ONLY: nothing is written, nothing is ranked.

Two detectors:
  * robust baseline (per listing): modified z-score from median/MAD (Iglewicz-Hoaglin, |z| > 3.5) and Tukey IQR fences.
  * IsolationForest (fleet-wide): over per-observation features (log price vs the listing's own median, log step change).

REFUSAL. A statistical detector on a handful of points would only produce confident-looking noise, so each detector refuses
(status="refused", with a reason) unless history is sufficient:
  per listing : >= MIN_OBS non-null observations in one currency on >= MIN_DAYS distinct calendar days
  fleet model : >= FOREST_MIN_LISTINGS qualifying listings and >= FOREST_MIN_OBS observations among them
Several crawls on one day are NOT multi-day history (they measure the same price level), hence the day requirement.
Missing prices (NULL) are skipped, never treated as 0. Prices are integer minor units; currencies are never mixed or converted.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchtower.models import Listing, Observation, Source

MIN_OBS = 8
MIN_DAYS = 7
Z_THRESHOLD = 3.5
IQR_K = 1.5
FOREST_MIN_LISTINGS = 30
FOREST_MIN_OBS = 300


@dataclass
class PriceSeries:
    listing_id: int
    currency: str
    points: list[tuple[datetime, int]]  # (observed_at, price_minor), ascending, price not null


def load_price_series(s: Session) -> dict[int, PriceSeries]:
    """Per-listing price history for LIVE listings. A listing observed in more than one currency is dropped (no conversion)."""
    q = (select(Observation.listing_id, Observation.observed_at, Observation.price_minor, Observation.currency)
         .join(Listing, Listing.id == Observation.listing_id).join(Source, Source.id == Listing.source_id)
         .where(Source.kind == "live", Observation.price_minor.is_not(None), Observation.price_minor > 0)
         .order_by(Observation.listing_id, Observation.observed_at, Observation.id))
    acc: dict[int, list] = {}
    for lid, at, price, cur in s.execute(q):
        acc.setdefault(lid, []).append((at, price, cur))
    out = {}
    for lid, rows in acc.items():
        if len({r[2] for r in rows}) == 1:
            out[lid] = PriceSeries(lid, rows[0][2], [(r[0], r[1]) for r in rows])
    return out


def n_days(points) -> int:
    return len({p[0].date() for p in points})


def series_ok(points) -> tuple[bool, str]:
    n, d = len(points), n_days(points)
    if n < MIN_OBS:
        return False, f"only {n} observation(s); need >= {MIN_OBS}"
    if d < MIN_DAYS:
        return False, f"observations span {d} distinct day(s); need >= {MIN_DAYS}"
    return True, "ok"


def robust_z(values: list[float]) -> list[float]:
    """Modified z = 0.6745 * (x - median) / MAD. If MAD == 0 (flat series), fall back to mean absolute deviation, and if that
    is also 0 to 1% of the median (at least 1 minor unit) so a constant series gives z = 0 and any real jump gives a large z."""
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    if mad > 0:
        return [0.6745 * (v - med) / mad for v in values]
    meanad = sum(abs(v - med) for v in values) / len(values)
    scale = 1.253314 * meanad if meanad > 0 else max(0.01 * abs(med), 1.0)
    return [(v - med) / scale for v in values]


def iqr_fences(values: list[float], k: float = IQR_K) -> tuple[float, float]:
    q = statistics.quantiles(values, n=4, method="inclusive")
    iqr = q[2] - q[0]
    return q[0] - k * iqr, q[2] + k * iqr


@dataclass
class Verdict:
    status: str  # ok | refused
    reason: str
    n_obs: int = 0
    n_days: int = 0
    flags: list[dict] = field(default_factory=list)
    advisory: bool = True


def detect_series(points: list[tuple[datetime, int]], z_threshold: float = Z_THRESHOLD, iqr_k: float = IQR_K) -> Verdict:
    ok, why = series_ok(points)
    base = dict(n_obs=len(points), n_days=n_days(points))
    if not ok:
        return Verdict("refused", why, **base)
    vals = [float(p[1]) for p in points]
    z = robust_z(vals)
    lo, hi = iqr_fences(vals, iqr_k)
    flags = []
    for (at, price), zi in zip(points, z):
        zf, iq = abs(zi) > z_threshold, (price < lo or price > hi)
        if zf or iq:
            flags.append({"observed_at": at.isoformat(), "price_minor": price, "robust_z": round(zi, 2),
                          "z_flag": zf, "iqr_flag": iq})
    return Verdict("ok", f"{len(flags)} flagged of {len(points)}", flags=flags, **base)


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


def isolation_forest_scan(series: dict[int, PriceSeries], contamination: float = 0.01, seed: int = 0) -> dict:
    """Fleet-wide IsolationForest over qualifying listings. Refuses (no model fitted) when data is insufficient."""
    qual = {lid: ps for lid, ps in series.items() if series_ok(ps.points)[0]}
    n_obs = sum(len(ps.points) for ps in qual.values())
    if len(qual) < FOREST_MIN_LISTINGS or n_obs < FOREST_MIN_OBS:
        return {"status": "refused", "advisory": True, "model": None,
                "reason": (f"{len(qual)} listing(s) with sufficient history ({n_obs} observations); need >= {FOREST_MIN_LISTINGS} "
                           f"listings and >= {FOREST_MIN_OBS} observations (each listing >= {MIN_OBS} obs on >= {MIN_DAYS} days)"),
                "listings_total": len(series), "listings_qualifying": len(qual)}
    from sklearn.ensemble import IsolationForest  # lazy: optional dependency
    X, index = _forest_rows(qual)
    clf = IsolationForest(n_estimators=200, contamination=contamination, random_state=seed).fit(X)
    pred, score = clf.predict(X), clf.score_samples(X)
    flagged = [{"listing_id": i[0], "observed_at": i[1].isoformat(), "price_minor": i[2], "score": round(float(sc), 4)}
               for i, p, sc in zip(index, pred, score) if p == -1]
    return {"status": "ok", "advisory": True, "model": "IsolationForest", "seed": seed, "contamination": contamination,
            "listings_qualifying": len(qual), "n_obs": n_obs, "flagged": flagged}


def scan_database(s: Session) -> dict:
    """Run both detectors over every live listing; returns counts of refusals (with reasons) and any flags. Read-only."""
    series = load_price_series(s)
    refusals: dict[str, int] = {}
    flagged, ok_n = [], 0
    for lid, ps in sorted(series.items()):
        v = detect_series(ps.points)
        if v.status == "refused":
            key = v.reason.split(";")[0]
            key = "too few observations" if key.startswith("only") else "history spans too few distinct days"
            refusals[key] = refusals.get(key, 0) + 1
        else:
            ok_n += 1
            if v.flags:
                flagged.append({"listing_id": lid, "flags": v.flags})
    return {"listings_with_prices": len(series), "listings_scanned": ok_n, "refused_by_reason": refusals,
            "robust_flagged_listings": flagged, "isolation_forest": isolation_forest_scan(series) if series else
            {"status": "refused", "reason": "no live price history", "model": None}}
