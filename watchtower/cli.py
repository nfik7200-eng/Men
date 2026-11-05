"""python -m watchtower.cli init | collect [--source K] [--max-pages N] | status"""
import argparse
import logging

from .collector import ensure_sources, run_source
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
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    db = Database()
    db.init()
    ensure_sources(db)
    if args.cmd == "collect":
        for key in ([args.source] if args.source else ADAPTERS):
            rid = run_source(db, key, max_pages=args.max_pages)
            with db.session() as s:
                r = s.get(CollectionRun, rid)
                print(f"{key}: {r.status} pages={r.pages_fetched} seen={r.items_seen} new={r.items_new} "
                      f"invalid={r.items_invalid} price_changes={r.price_changes} complete={r.complete}"
                      + (f" error={r.error}" if r.error else ""))
    elif args.cmd == "status":
        with db.session() as s:
            for src in s.query(Source):
                n = s.query(Listing).filter_by(source_id=src.id).count()
                print(f"{src.key}: {n} listings, last_success={src.last_success_at}, last_status={src.last_status}")
            print("observations:", s.query(Observation).count())


if __name__ == "__main__":
    main()
