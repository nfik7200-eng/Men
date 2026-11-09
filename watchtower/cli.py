"""python -m watchtower.cli notify-migrate|notify-rollback|notify-generate | init | collect [--source K] [--max-pages N] | status | health | policy | rematch | reparse | backup | verify | plan | coverage | schedule | cleanup-preview | source | fx"""
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
    cv = sub.add_parser("coverage", help="acquisition coverage per live source (read-only report on a snapshot/copy)")
    cv.add_argument("--db", required=True, help="SQLite snapshot/copy (never the live DB)")
    cv.add_argument("--json", action="store_true")
    sc = sub.add_parser("schedule", help="DISABLED-BY-DEFAULT scheduler entry point (status | run-due); installs nothing")
    sc.add_argument("action", choices=["status", "run-due"])
    sc.add_argument("--config", help="default data/schedule.json")
    cp = sub.add_parser("cleanup-preview", help="preview `rematch` on a TEMPORARY copy of a snapshot (before/after verify; modifies nothing)")
    cp.add_argument("--db", required=True, help="SQLite snapshot (never the configured live DB)")
    e = sub.add_parser("source")
    e.add_argument("action", choices=["enable", "disable"])
    e.add_argument("key", choices=list(ADAPTERS))
    for name, text in (("notify-migrate", "create the notifications table (dry run unless --apply)"),
                       ("notify-rollback", "drop the notifications table (dry run unless --apply)"),
                       ("notify-generate", "record watchlist alerts for one owner (idempotent)")):
        n = sub.add_parser(name, help=text + "; NEVER the live database: run on a copy")
        n.add_argument("--db", required=True, help="a COPY of the database")
        if name == "notify-generate":
            n.add_argument("--owner", required=True)
        else:
            n.add_argument("--apply", action="store_true")
        if name == "notify-rollback":
            n.add_argument("--discard-rows", action="store_true", help="also delete existing notification rows")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.cmd.startswith("notify-"):
        return _notify(args)
    if args.cmd == "backup":  # before Database(): opening the app DB normally runs init/ensure_* writes
        return _backup(args.out)
    if args.cmd == "cleanup-preview":
        return _cleanup_preview(args)
    if args.cmd == "schedule":  # decides BEFORE opening any database: a disabled scheduler touches nothing
        return _schedule(args)
    if args.cmd == "coverage":
        return _coverage(args)
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
                if r is None:
                    print(f"{key}: run {rid} is not in the database")
                    continue
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


def _notify(args) -> int:
    """Notification schema and generation. Refuses the configured live database: migrations run on copies only."""
    from pathlib import Path
    from sqlalchemy import create_engine
    from . import notifications
    target = Path(args.db).resolve()
    if _is_live_db(args.db):  # same path OR same inode (hard links, bind aliases)
        raise SystemExit("refusing: this is the configured live database. Copy it first "
                         "(python -m watchtower.cli backup --out COPY) and run the command on the copy.")
    if args.cmd == "notify-generate":
        with Database(f"sqlite:///{target}").session() as s:  # no init(): a copy already has the listing tables
            if not notifications.available(s):
                raise SystemExit("notifications table missing: run notify-migrate --apply on this copy first")
            print(f"{notifications.generate(s, args.owner)} new notification(s) for owner {args.owner}")
        return 0
    engine = create_engine(f"sqlite:///{target}")
    if args.cmd == "notify-migrate":
        print(notifications.migrate(engine, args.apply))
    else:
        print(notifications.rollback(engine, args.apply, args.discard_rows))
    return 0


def _configured_sqlite_path() -> str | None:
    """File path of the configured database when it is SQLite, else None."""
    from . import config
    prefix = "sqlite:///"
    return config.DB_URL[len(prefix):] if config.DB_URL.startswith(prefix) else None


def _is_live_db(path) -> bool:
    """True when `path` IS the configured live database: same resolved path OR same inode (a hardlink or bind alias)."""
    from pathlib import Path
    live = _configured_sqlite_path()
    if not live or not Path(live).exists():
        return bool(live) and Path(path).resolve() == Path(live).resolve()
    return Path(path).exists() and Path(path).samefile(live)


def _snapshot_copy(db_arg: str, cmd: str):
    """Resolve --db for the read-only snapshot commands; None (after printing why) unless it is an existing file that is
    NOT the configured live database."""
    from pathlib import Path
    path = Path(db_arg).resolve()
    if not path.is_file() or _is_live_db(path):
        print(f"{cmd}: --db must be an existing snapshot/copy, never the configured live database")
        return None
    return path


def _cleanup_preview(args) -> int:
    from pathlib import Path
    from .cleanup import preview_cleanup
    path = Path(args.db).resolve()
    if not path.is_file() or _is_live_db(path):
        print("cleanup-preview: --db must be an existing snapshot, never the configured live database")
        return 2
    r = preview_cleanup(path)
    for label, rep in (("BEFORE", r["before"]), ("AFTER", r["after"])):
        bad = [c for c in rep["checks"] if not c["ok"]]
        print(f"{label}: " + ("all integrity checks pass" if not bad else "; ".join(f"{c['name']}={c['value']}" for c in bad)))
        m = rep["metrics"]
        print(f"   pending review candidates={m['pending_review_candidates']} multi-merchant products={m['multi_merchant_products']} observations={m['live_observations']}")
    print(f"rematch: {r['rematch']}")
    print(f"listings whose label/confidence changed: {r['listings_changed']}; product groups changed: {r['products_regrouped']}; observations {r['observations_before']} -> {r['observations_after']}")
    for w in r["warnings"]:
        print(f"WARNING: {w}")
    print("safe to apply to the real database (after your approval and a fresh backup): " + ("YES" if r["safe_to_apply"] else "NO"))
    print("nothing was modified")
    return 0


def _schedule(args) -> int:
    from . import config, schedule
    path = args.config or str(config.ROOT / "data" / "schedule.json")
    try:
        cfg = schedule.load(path)
    except (schedule.ScheduleError, OSError, ValueError) as ex:
        print(f"schedule: invalid configuration: {ex}")
        return 2
    ok, why = schedule.enabled(cfg)
    if args.action == "status" or not ok:
        print(why or "scheduling is ENABLED (config + environment)")
        for k, v in cfg["sources"].items():
            print(f"  {k}: every >= {v['min_interval_hours']:g} h, up to {v['max_pages']} pages per run")
        return 0
    db = Database()
    db.init()
    ensure_sources(db)
    ensure_policies(db)
    res = schedule.run_due(db, path)
    print("ran: " + (", ".join(res.ran) or "nothing"))
    for k, why in res.skipped.items():
        print(f"skipped {k}: {why}")
    return 0


def _coverage(args) -> int:
    import json
    from datetime import datetime, timezone
    from .coverage import coverage_report
    path = _snapshot_copy(args.db, "coverage")
    if path is None:
        return 2
    db = Database(f"sqlite:///{path}")
    db.sync_columns()  # older COPY -> current schema
    with db.Session() as s:
        rep = coverage_report(s, datetime.now(timezone.utc).replace(tzinfo=None))
        s.rollback()
    if args.json:
        print(json.dumps(rep, indent=1, default=str))
        return 0
    print(f"live merchants={rep['live_merchants']} live listings={rep['live_listings']} cross-merchant products={rep['cross_merchant_products']}")
    pct = lambda v: "-" if v is None else f"{v:.0%}"
    for c in rep["sources"]:
        print(f"{c['key']}: discovered={c['discovered']} eligible={c['eligible']} acquired={c['acquired']} ({pct(c['eligible_coverage'])}) "
              f"fetched_ok={c['fetched_ok']} extraction={pct(c['extraction_success_rate'])} never_attempted={c['never_attempted']} "
              f"deferred_budget={c['deferred_budget']} with_counterpart={c['unfetched_with_counterpart']} failed={c['failed']} "
              f"rejected={c['rejected']} dup={c['duplicates']} non_product={c['non_product']} unknown={c['unknown_outcome']} health={c['health']}")
    return 0


def _plan(args) -> int:
    from datetime import datetime, timezone
    from sqlalchemy import select
    from . import acquisition, config
    from .collector import RECHECK_SHARE
    path = _snapshot_copy(args.db, "plan")
    if path is None:
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
        tiers = {u: adapter.tier_for_url(u) for u in known}
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
                new, scored = next(((i, sc) for i, sc in enumerate(plan.ranked, 1) if sc.crawl.url == args.explain), (None, None))
                old = legacy.index(args.explain) + 1 if args.explain in legacy else None
                print(f"  explain {args.explain}: legacy rank {old} of {len(legacy)} (tier {tiers[args.explain]}, alphabetical), new rank {new}")
                for c in (scored.components if scored else []):
                    print(f"      {c.name:14} {c.points:5g}  {c.why}")
        s.rollback()
    return 0


def _verify(db_path: str | None) -> int:
    from pathlib import Path
    from .integrity import integrity_report
    import sqlite3
    live = _configured_sqlite_path()
    if not db_path and live is None:
        print("verify: only SQLite databases are supported")
        return 2
    path = Path(db_path or live or "")
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
    live = _configured_sqlite_path()
    if live is None:
        print("backup: only SQLite databases are supported")
        return 2
    src = Path(live)
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
