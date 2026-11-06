"""python -m watchtower.cli init | collect [--source K] [--max-pages N] | status | health | policy | rematch | reparse | backup | verify | source | fx"""
import argparse
import logging

from .collector import SourceBusy, SourceDisabled, SourceNotConfigured, ensure_sources, run_source, set_enabled
from .fetcher import FetchError
from .policy import SourceNotApproved, ensure_policies
from .db import Database
from .models import CollectionRun, Listing, Observation, Source
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
    e = sub.add_parser("source")
    e.add_argument("action", choices=["enable", "disable"])
    e.add_argument("key", choices=list(ADAPTERS))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.cmd == "backup":  # before Database(): opening the app DB normally runs init/ensure_* writes
        return _backup(args.out)
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


def _verify(db_path: str | None) -> int:
    from pathlib import Path
    from . import config
    from .integrity import integrity_report
    path = Path(db_path) if db_path else Path(config.DB_URL[len("sqlite:///"):])
    if not path.is_file():
        print(f"verify: database not found: {path}")
        return 2
    r = integrity_report(path)
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
