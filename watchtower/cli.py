"""python -m watchtower.cli init | collect [--source K] [--max-pages N] | status | health | policy | rematch | reparse | backup | verify | plan | source | fx"""
import argparse
import logging

from .collector import SourceBusy, SourceDisabled, SourceNotConfigured, ensure_sources, run_source, set_enabled
from .fetcher import FetchError
from .policy import SourceNotApproved, ensure_policies
from .db import Database
from .models import CollectionRun, CrawlUrl, Listing, Observation, Source
from .sources import ADAPTERS


def main(argv=None):
    ap = argparse.ArgumentParser(prog="watchtower")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    c = sub.add_parser("collect")
    c.add_argument("--source", choices=list(ADAPTERS))
    c.add_argument("--max-pages", type=int, help="pages per start URL (default: adapter default)")
    sub.add_parser("status")
    sub.add_parser("health")
    sub.add_parser("policy")
    sub.add_parser("rematch", help="re-run cross-store matching for unmatched listings")
    sub.add_parser("reparse", help="re-derive title-parsed specs of live listings with the current rules")
    rc = sub.add_parser("recheck", help="mark the N oldest-fetched URLs of a product-page source as due now")
    rc.add_argument("--source", required=True, choices=list(ADAPTERS))
    rc.add_argument("-n", type=int, default=10)
    fx = sub.add_parser("fx", help="record or list sourced exchange rates")
    fx.add_argument("action", choices=["set", "list"])
    fx.add_argument("base", nargs="?")
    fx.add_argument("quote", nargs="?")
    fx.add_argument("rate", nargs="?")
    fx.add_argument("--as-of", help="YYYY-MM-DD the rate is valid for")
    fx.add_argument("--source", help="who published this rate (required)")
    fx.add_argument("--url")
    b = sub.add_parser("backup", help="verified, WAL-safe snapshot of the database (read-only; never modifies it)")
    b.add_argument("--out", help="destination file (default data/backups/watchtower-<UTC time>.db); never overwritten")
    v = sub.add_parser("verify", help="read-only data-integrity report (exit 1 if an invariant is violated)")
    v.add_argument("--db", help="SQLite file to check (default: the configured database); a snapshot is safest")
    pl = sub.add_parser("plan", help="OFFLINE dry run of the next collection's selection (no network, no writes)")
    pl.add_argument("--db", required=True, help="SQLite snapshot/copy to read (never the live DB; rolled back, nothing is written)")
    pl.add_argument("--source", required=True, choices=list(ADAPTERS))
    pl.add_argument("--max-pages", type=int)
    pl.add_argument("--show", type=int, default=15, help="how many selected/deferred URLs to list")
    pl.add_argument("--explain", help="URL whose legacy (tier, alphabetical) rank and new rank to compare")
    e = sub.add_parser("source")
    e.add_argument("action", choices=["enable", "disable"])
    e.add_argument("key", choices=list(ADAPTERS))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.cmd == "backup":  # before Database(): opening the app DB normally runs init/ensure_* writes
        return _backup(args.out)
    if args.cmd == "plan":  # offline and read-only as well
        return _plan(args)
    if args.cmd == "verify":  # read-only as well
        return _verify(args.db)
    db = Database()
    db.init()
    ensure_sources(db)
    ensure_policies(db)
    if args.cmd == "collect":
        for key in ([args.source] if args.source else ADAPTERS):
            try:
                rid = run_source(db, key, max_pages=args.max_pages)
            except (SourceNotConfigured, SourceDisabled, SourceBusy, SourceNotApproved, FetchError) as ex:
                print(f"{key}: SKIPPED — {ex}")
                continue
            with db.session() as s:
                r = s.get(CollectionRun, rid)
                print(f"{key}: {r.status} pages={r.pages_fetched} seen={r.items_seen} new={r.items_new} "
                      f"invalid={r.items_invalid} price_changes={r.price_changes} complete={r.complete}"
                      + (f" error={r.error}" if r.error else ""))
    elif args.cmd == "fx":
        from datetime import datetime
        from shopping.fx import record_rate
        from .models import FxRate
        with db.session() as s:
            if args.action == "set":
                if not (args.base and args.quote and args.rate and args.as_of and args.source):
                    ap.error("fx set BASE QUOTE RATE --as-of YYYY-MM-DD --source '<publisher>' [--url ...]")
                record_rate(s, args.base, args.quote, args.rate, datetime.strptime(args.as_of, "%Y-%m-%d"), args.source, args.url)
                print("recorded")
            for r in s.query(FxRate).order_by(FxRate.as_of.desc()).limit(20):
                print(f"1 {r.base} = {r.rate} {r.quote}  as of {r.as_of:%Y-%m-%d}  ({r.source})")
    elif args.cmd == "health":
        from .health import source_health
        with db.session() as s:
            for src in s.query(Source):
                h = source_health(s, src)
                print(f"{h.key:22} {h.kind:8} {h.state:10} runs={h.runs} ok={'-' if h.ok_rate is None else f'{h.ok_rate:.0%}'} "
                      f"listings={h.listings} obs={h.observations} since_success={'-' if h.hours_since_success is None else f'{h.hours_since_success:.1f}h'}")
    elif args.cmd == "policy":
        from .models import SourcePolicy
        with db.session() as s:
            for src in s.query(Source):
                p = s.query(SourcePolicy).filter_by(source_id=src.id).one_or_none()
                req = "n/a (demo/sandbox)" if src.kind in ("demo", "sandbox") else (p.status if p else "MISSING")
                print(f"{src.key:22} {src.kind:8} policy={req}" + (f" reviewed={p.reviewed_at:%Y-%m-%d} due={p.review_due_at:%Y-%m-%d}" if p and p.reviewed_at else ""))
    elif args.cmd == "recheck":
        from .models import CrawlUrl
        from .models import utcnow
        with db.session() as s:
            src = s.query(Source).filter_by(key=args.source).one()
            rows = s.query(CrawlUrl).filter(CrawlUrl.source_id == src.id, CrawlUrl.last_fetched_at.is_not(None), CrawlUrl.gone.is_(False),
                                            CrawlUrl.listing_id.is_not(None)).order_by(CrawlUrl.last_fetched_at).limit(args.n).all()
            for r in rows:
                r.next_check_at = utcnow()
            print(f"{len(rows)} URL(s) of {args.source} marked due")
    elif args.cmd == "reparse":
        from .ingest import reparse_specs
        with db.session() as s:
            print(f"{reparse_specs(s)} listing(s) re-parsed")
    elif args.cmd == "rematch":
        from .matching import rematch_all
        with db.session() as s:
            print(rematch_all(s))
    elif args.cmd == "source":
        set_enabled(db, args.key, args.action == "enable")
        print(f"{args.key}: {args.action}d")
    elif args.cmd == "status":
        with db.session() as s:
            for src in s.query(Source):
                n = s.query(Listing).filter_by(source_id=src.id).count()
                ready, missing = ADAPTERS[src.key].configured() if src.key in ADAPTERS else (True, [])
                state = "enabled" if src.enabled else "DISABLED"
                print(f"{src.key}: {n} listings, {state}, {'configured' if ready else 'NOT CONFIGURED (' + ', '.join(missing) + ')'}, "
                      f"last_success={src.last_success_at}, last_status={src.last_status}")
            print("observations:", s.query(Observation).count())


def _plan(args) -> int:
    from datetime import datetime, timezone
    from pathlib import Path
    from sqlalchemy import select
    from . import acquisition, config
    from .collector import RECHECK_SHARE
    path = Path(args.db).resolve()
    live = config.DB_URL[len("sqlite:///"):] if config.DB_URL.startswith("sqlite:///") else None
    if not path.is_file() or (live and path == Path(live).resolve()):
        print("plan: --db must be an existing snapshot/copy, never the configured live database")
        return 2
    adapter = ADAPTERS[args.source]
    cap = min(args.max_pages, config.MAX_PAGE_BUDGET) if args.max_pages else config.page_budget(adapter.key, adapter.default_max_pages)
    db = Database(f"sqlite:///{path}")
    added = db.sync_columns()  # forward-only: lets an older COPY be read with the current schema (the copy, never the live DB)
    if added:
        print(f"note: added {len(added)} new nullable column(s) to the COPY {path.name} (e.g. {added[0]})")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.Session() as s:  # never committed
        src = s.query(Source).filter_by(key=args.source).one()
        known = {c.url: c for c in s.scalars(select(CrawlUrl).where(CrawlUrl.source_id == src.id))}
        tiers = {u: (adapter.tier_for_url(u) if hasattr(adapter, "tier_for_url") else None) for u in known}
        tiers = {u: (2 if t is None else t) for u, t in tiers.items()}  # tier unknown offline: neutral default
        hints = {u: (c.name_hint or acquisition.hint_from_url(u)) for u, c in known.items()}
        legacy = sorted((u for u, c in known.items() if c.last_fetched_at is None and not c.gone), key=lambda u: (tiers[u], u))
        plan = acquisition.plan_run(s, src, known, set(known), tiers, hints, cap, now, RECHECK_SHARE)
        print(f"plan for {args.source} (offline, nothing fetched, nothing written): budget={cap} pages, known URLs={len(known)}")
        print("  decisions: " + ", ".join(f"{k}={v}" for k, v in plan.counts.items()))
        print(f"  never fetched: {len(plan.ranked)}; due re-checks selected: {len(plan.recheck)}; discovery slots: {len(plan.fresh)}")
        named = sum(1 for sc in plan.ranked if hints.get(sc.crawl.url))
        print(f"  never-fetched URLs with a usable name hint: {named} of {len(plan.ranked)}")
        for i, sc in enumerate(plan.ranked[: args.show], 1):
            why = "; ".join(f"{c.name} {c.points:g}" for c in sc.components if c.points)
            print(f"  {i:3}. {sc.score:6.1f}  {sc.crawl.url}  [{why}]")
        if args.explain:
            row = known.get(args.explain)
            if row is None:
                print(f"  explain: {args.explain} is not a known URL")
            else:
                new = next((i for i, sc in enumerate(plan.ranked, 1) if sc.crawl.url == args.explain), None)
                old = legacy.index(args.explain) + 1 if args.explain in legacy else None
                print(f"  explain {args.explain}: legacy rank {old} of {len(legacy)} (tier {tiers[args.explain]}, alphabetical), new rank {new}")
                for c in (next(sc for sc in plan.ranked if sc.crawl.url == args.explain).components if new else []):
                    print(f"      {c.name:14} {c.points:5g}  {c.why}")
        s.rollback()
    return 0


def _verify(db_path: str | None) -> int:
    from pathlib import Path
    from . import config
    from .integrity import integrity_report
    import sqlite3
    if not db_path and not config.DB_URL.startswith("sqlite:///"):
        print("verify: only SQLite databases are supported")
        return 2
    path = Path(db_path) if db_path else Path(config.DB_URL[len("sqlite:///"):])
    if not path.is_file():
        print(f"verify: database not found: {path}")
        return 2
    try:
        r = integrity_report(path)
    except sqlite3.Error as ex:  # not a database, or an un-migrated schema
        print(f"verify: cannot read {path}: {ex}")
        return 2
    for ch in r["checks"]:
        print(f"{'ok  ' if ch['ok'] else 'FAIL'} {ch['name']:46} {ch['value']}" + ("" if ch["ok"] else f"  ({ch['meaning']})"))
    for k, val in r["metrics"].items():
        print(f"     {k:46} {val}")
    print("integrity: OK" if r["ok"] else "integrity: VIOLATIONS FOUND")
    return 0 if r["ok"] else 1


def _backup(out: str | None) -> int:
    from datetime import datetime, timezone
    from pathlib import Path
    from . import config
    from .db import SnapshotError, snapshot
    if not config.DB_URL.startswith("sqlite:///"):
        print("backup: only SQLite databases are supported")
        return 2
    src = Path(config.DB_URL[len("sqlite:///"):])
    dst = Path(out) if out else config.ROOT / "data" / "backups" / f"watchtower-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.db"
    try:
        r = snapshot(src, dst)
    except SnapshotError as ex:
        print(f"backup FAILED: {ex}")
        return 1
    print(f"backup ok: {r['path']} integrity={r['integrity']} " + " ".join(f"{k}={v}" for k, v in sorted(r["tables"].items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
