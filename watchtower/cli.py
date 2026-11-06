"""python -m watchtower.cli init | collect [--source K] [--max-pages N] | status | health | policy | rematch | source | fx"""
import argparse
import logging

from .collector import SourceBusy, SourceDisabled, SourceNotConfigured, ensure_sources, run_source, set_enabled
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
    fx = sub.add_parser("fx", help="record or list sourced exchange rates")
    fx.add_argument("action", choices=["set", "list"])
    fx.add_argument("base", nargs="?")
    fx.add_argument("quote", nargs="?")
    fx.add_argument("rate", nargs="?")
    fx.add_argument("--as-of", help="YYYY-MM-DD the rate is valid for")
    fx.add_argument("--source", help="who published this rate (required)")
    fx.add_argument("--url")
    e = sub.add_parser("source")
    e.add_argument("action", choices=["enable", "disable"])
    e.add_argument("key", choices=list(ADAPTERS))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    db = Database()
    db.init()
    ensure_sources(db)
    ensure_policies(db)
    if args.cmd == "collect":
        for key in ([args.source] if args.source else ADAPTERS):
            try:
                rid = run_source(db, key, max_pages=args.max_pages)
            except (SourceNotConfigured, SourceDisabled, SourceBusy, SourceNotApproved) as ex:
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


if __name__ == "__main__":
    main()
