"""Read-only data-integrity report: the measurement every improvement loop runs before and after a change.

Opens the SQLite file with a READ-ONLY connection (never initialises, migrates or writes), so it is safe on the real
database and on snapshots made with `watchtower.cli backup`. Each check is an invariant that must hold (expected 0);
metrics are informational baselines (coverage, history depth, review-queue size).
"""
import sqlite3
from pathlib import Path

_LIVE = "JOIN sources s ON s.id = l.source_id WHERE s.kind = 'live'"

# name -> (SQL returning one count that must be 0, what a violation means)
CHECKS: dict[str, tuple[str, str]] = {
    "nonpositive_prices": ("SELECT (SELECT count(*) FROM observations WHERE price_minor <= 0) + "
                           "(SELECT count(*) FROM listings WHERE price_minor <= 0)",
                           "a missing price must be NULL, never 0 or negative"),
    # only the LATEST observation: older ones legitimately keep the old currency after a recorded currency_change
    "latest_observation_currency_matches_listing": (
        f"SELECT count(*) FROM listings l {_LIVE} AND l.currency IS NOT (SELECT o.currency FROM observations o "
        "WHERE o.listing_id = l.id ORDER BY o.observed_at DESC, o.id DESC LIMIT 1)", "current currency not backed by history"),
    "one_observation_per_listing_per_run": ("SELECT count(*) FROM (SELECT 1 FROM observations WHERE run_id IS NOT NULL "
                                            "GROUP BY listing_id, run_id HAVING count(*) > 1)", "duplicate observations inflate history"),
    "live_listing_has_observation": (f"SELECT count(*) FROM listings l {_LIVE} AND NOT EXISTS "
                                     "(SELECT 1 FROM observations o WHERE o.listing_id = l.id)", "a price with no provenance"),
    "listing_price_matches_last_observation": (
        f"SELECT count(*) FROM listings l {_LIVE} AND l.price_minor IS NOT (SELECT o.price_minor FROM observations o "
        "WHERE o.listing_id = l.id ORDER BY o.observed_at DESC, o.id DESC LIMIT 1)", "current price not backed by history"),
    "listing_availability_matches_last_observation": (
        f"SELECT count(*) FROM listings l {_LIVE} AND l.availability IS NOT (SELECT o.availability FROM observations o "
        "WHERE o.listing_id = l.id ORDER BY o.observed_at DESC, o.id DESC LIMIT 1)", "current stock not backed by history"),
    "one_listing_per_merchant_per_product": ("SELECT count(*) FROM (SELECT 1 FROM listings WHERE product_id IS NOT NULL "
                                             "GROUP BY product_id, source_id HAVING count(*) > 1)", "matcher merged variants"),
    "products_never_mix_data_classes": ("SELECT count(*) FROM (SELECT 1 FROM listings l JOIN sources s ON s.id = l.source_id "
                                        "WHERE l.product_id IS NOT NULL GROUP BY l.product_id HAVING count(DISTINCT s.kind = 'live') > 1)", "demo data mixed into real"),
    "no_orphan_products": ("SELECT count(*) FROM products p WHERE NOT EXISTS (SELECT 1 FROM listings l WHERE l.product_id = p.id)",
                           "product left behind by a merge"),
    "candidate_points_at_own_product": ("SELECT count(*) FROM match_candidates m JOIN listings l ON l.id = m.listing_id "
                                        "WHERE m.status = 'pending' AND m.product_id = l.product_id", "meaningless review item"),
    "mirrored_pending_candidates": ("SELECT count(*) FROM match_candidates a JOIN listings la ON la.id = a.listing_id "
                                    "JOIN match_candidates b ON b.status = 'pending' AND b.id > a.id JOIN listings lb ON lb.id = b.listing_id "
                                    "WHERE a.status = 'pending' AND a.product_id = lb.product_id AND b.product_id = la.product_id",
                                    "the same pair asked twice (contradictory decisions possible)"),
    "review_status_has_pending_question": (
        "SELECT count(*) FROM listings l WHERE l.match_status = 'review' AND l.active = 1 AND NOT EXISTS "
        "(SELECT 1 FROM match_candidates m WHERE m.status = 'pending' AND (m.listing_id = l.id OR m.product_id = l.product_id))",
        "listing shown as 'in review' with nothing to review"),
    "live_text_or_images_stored": (f"SELECT count(*) FROM listings l {_LIVE} AND (length(coalesce(l.description, '')) > 0 "
                                   "OR l.image_url IS NOT NULL)", "facts-only storage rule for live sources"),
}


def _count(c, sql):
    return c.execute(sql).fetchone()[0] or 0


def _has_table(c, name: str) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def integrity_report(path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    c = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        checks = [{"name": n, "value": (v := _count(c, sql)), "ok": v == 0, "meaning": why} for n, (sql, why) in CHECKS.items()]
        fk = c.execute("PRAGMA foreign_key_check").fetchall()
        checks.append({"name": "foreign_keys", "value": len(fk), "ok": not fk, "meaning": "dangling references"})
        ic = c.execute("PRAGMA integrity_check").fetchone()[0]
        checks.append({"name": "sqlite_integrity", "value": ic, "ok": ic == "ok", "meaning": "file corruption"})
        running = _count(c, "SELECT count(*) FROM collection_runs WHERE status = 'running'")
        m = {
            "live_merchants": _count(c, "SELECT count(DISTINCT l.source_id) FROM listings l " + _LIVE),
            "live_listings": _count(c, "SELECT count(*) FROM listings l " + _LIVE),
            "live_active_listings": _count(c, f"SELECT count(*) FROM listings l {_LIVE} AND l.active = 1"),
            "live_observations": _count(c, f"SELECT count(*) FROM observations o JOIN listings l ON l.id = o.listing_id {_LIVE}"),
            "live_observation_days": _count(c, "SELECT count(DISTINCT date(o.observed_at)) FROM observations o "
                                               f"JOIN listings l ON l.id = o.listing_id {_LIVE}"),
            # approximation of the deal rule (3+ collections over 7+ days) by distinct days; deals.py is authoritative
            "verified_history_listings": _count(c, "SELECT count(*) FROM (SELECT l.id FROM observations o JOIN listings l "
                                                   f"ON l.id = o.listing_id {_LIVE} AND o.price_minor IS NOT NULL GROUP BY l.id "
                                                   "HAVING count(DISTINCT date(o.observed_at)) >= 3 AND "
                                                   "julianday(max(o.observed_at)) - julianday(min(o.observed_at)) >= 7)"),
            "multi_merchant_products": _count(c, "SELECT count(*) FROM (SELECT 1 FROM listings l " + _LIVE +
                                                 " AND l.product_id IS NOT NULL GROUP BY l.product_id HAVING count(DISTINCT l.source_id) > 1)"),
            # legacy aliases: several crawl URLs pointing at one listing (metric, not an invariant: old data has them)
            "crawl_rows_sharing_a_listing": _count(c, "SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) n FROM crawl_urls "
                                                      "WHERE listing_id IS NOT NULL AND gone = 0 GROUP BY listing_id HAVING n > 1)")
            if _has_table(c, "crawl_urls") else 0,
            "pending_review_candidates": _count(c, "SELECT count(*) FROM match_candidates WHERE status = 'pending'"),
            "price_change_events_live": _count(c, f"SELECT count(*) FROM events e JOIN listings l ON l.id = e.listing_id {_LIVE} "
                                                  "AND e.kind IN ('price_drop', 'price_rise')"),
            "unknown_stock_live": _count(c, f"SELECT count(*) FROM listings l {_LIVE} AND l.availability = 'unknown'"),
            "runs_still_running": running,
            "db_bytes": path.stat().st_size + sum(p.stat().st_size for p in path.parent.glob(path.name + "-wal")),
        }
    finally:
        c.close()
    return {"ok": all(ch["ok"] for ch in checks), "checks": checks, "metrics": m}
