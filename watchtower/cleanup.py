"""Preview of the production cleanup (`rematch`) on a TEMPORARY copy of a snapshot.

The real database is never opened for writing: the source is read through `db.snapshot` (WAL-safe, read-only) into a
temporary directory, `rematch_all` runs on that copy, and the integrity report is compared before/after. The owner can then
approve the identical operation on the real DB with evidence of exactly what it changes (labels and review queue only;
the grouping of listings into products and every observation must stay identical, which `safe_to_apply` asserts).
"""
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from .db import Database, snapshot
from .integrity import integrity_report
from .matching import rematch_all
from .models import Listing, Observation


def _state(db: Database) -> dict:
    with db.Session() as s:
        listings = {l.id: (l.product_id, l.match_status, round(l.match_confidence or 0, 4)) for l in s.scalars(select(Listing))}
        n_obs = s.query(Observation).count()
    return {"listings": listings, "observations": n_obs}


def _groups(listings: dict) -> set[frozenset]:
    by_product: dict[int, set[int]] = {}
    for lid, (pid, _, _) in listings.items():
        by_product.setdefault(pid, set()).add(lid)
    return {frozenset(v) for v in by_product.values()}


def preview_cleanup(source) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="wt-cleanup-preview-"))
    try:
        copy = tmp / "preview.db"
        snapshot(source, copy)
        db = Database(f"sqlite:///{copy}")
        db.sync_columns()  # the copy only: older snapshot -> current schema
        before_report = integrity_report(copy)
        before = _state(db)
        with db.session() as s:
            result = rematch_all(s)
        after = _state(db)
        db.engine.dispose()
        after_report = integrity_report(copy)
        regrouped = len(_groups(before["listings"]) ^ _groups(after["listings"])) // 2 if _groups(before["listings"]) != _groups(after["listings"]) else 0
        changed = sum(1 for k, v in before["listings"].items() if after["listings"].get(k) != v)
        warnings = []
        if regrouped:
            warnings.append(f"{regrouped} product group(s) changed: review before applying")
        if before["observations"] != after["observations"]:
            warnings.append("observation count changed")
        return {
            "before": before_report, "after": after_report,
            "metrics_before": before_report["metrics"], "metrics_after": after_report["metrics"],
            "rematch": result, "listings_changed": changed, "products_regrouped": regrouped,
            "observations_before": before["observations"], "observations_after": after["observations"],
            "warnings": warnings,
            "safe_to_apply": not warnings and after_report["ok"],
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
