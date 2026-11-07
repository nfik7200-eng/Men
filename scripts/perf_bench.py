"""Scale benchmark on a SYNTHETIC temporary database (never real data, never the live DB): timings and peak memory of the
user-facing queries at N listings. Synthetic numbers measure performance only; they say nothing about data quality.

    python scripts/perf_bench.py [N=5000]"""
import os
import random
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(n: int) -> None:
    tmp = Path(tempfile.mkdtemp()) / "perf.db"
    os.environ["WATCHTOWER_DB_URL"] = f"sqlite:///{tmp}"
    from shopping import assistant
    from shopping.search import search_listings
    from watchtower import deals
    from watchtower.collector import ensure_sources
    from watchtower.coverage import coverage_report
    from watchtower.db import Database
    from watchtower.integrity import integrity_report
    from watchtower.models import CrawlUrl, Listing, Observation, Product, Source
    db = Database()
    db.init()
    ensure_sources(db)
    rng = random.Random(7)
    t0 = datetime(2026, 11, 1)
    with db.session() as s:
        srcs = [s.query(Source).filter_by(key=k).one() for k in ("adafruit", "sparkfun")]
        for k, src in enumerate(srcs):
            src.kind = "live"
        words = ["board", "cable", "case", "sensor", "display", "shield", "kit", "module", "adapter", "battery"]
        for i in range(n):
            src = srcs[i % 2]
            p = Product(title=f"Synthetic {i}")
            s.add(p)
            s.flush()
            price = rng.randint(300, 20000)
            l = Listing(source_id=src.id, external_id=str(i), product_id=p.id, url=f"https://{src.key}.example/p/{i}", title=f"Synthetic {rng.choice(words)} {i}",
                        brand="Synth", category="maker-electronics", currency="USD", price_minor=price, availability="in_stock", first_seen_at=t0,
                        last_seen_at=t0 + timedelta(days=2), last_observed_at=t0 + timedelta(days=2), specs={}, identifiers={})
            s.add(l)
            s.flush()
            for d in range(3):
                s.add(Observation(listing_id=l.id, observed_at=t0 + timedelta(days=d), price_minor=price + rng.randint(-50, 50), currency="USD", availability="in_stock"))
            s.add(CrawlUrl(source_id=src.id, url=l.url, listing_id=l.id, first_seen_at=t0, last_fetched_at=t0, last_outcome="ok"))
        for i in range(n):  # discovered-but-unfetched URLs
            s.add(CrawlUrl(source_id=srcs[i % 2].id, url=f"https://{srcs[i % 2].key}.example/u/{i}", first_seen_at=t0))
    now = t0 + timedelta(days=2, hours=1)
    print(f"synthetic database: {n} listings, {n * 3} observations, {2 * n} crawl rows, {tmp.stat().st_size / 1e6:.1f} MB")
    jobs = {
        "search 'board' (page 1)": lambda s: search_listings(s, "board", None, None, None, None, None, False, "price_asc", 1, kinds=("live",)),
        "top_deals(20)": lambda s: deals.top_deals(s, 20, now=now),
        "assistant 'cheapest synthetic board'": lambda s: assistant.answer(s, "cheapest synthetic board", now=now, use_llm=False),
        "assistant 'which products have insufficient price history'": lambda s: assistant.answer(s, "which products have insufficient price history?", now=now, use_llm=False),
        "coverage_report(next_up)": lambda s: coverage_report(s, now, with_next_up=True),
    }
    with db.session() as s:
        for name, fn in jobs.items():
            tracemalloc.start()
            t = time.perf_counter()
            fn(s)
            ms = (time.perf_counter() - t) * 1000
            peak = tracemalloc.get_traced_memory()[1] / 1e6
            tracemalloc.stop()
            print(f"{name:62} {ms:9.0f} ms  peak {peak:7.1f} MB")
    t = time.perf_counter()
    r = integrity_report(tmp)
    print(f"{'integrity_report (read-only SQL)':62} {(time.perf_counter() - t) * 1000:9.0f} ms  ok={r['ok']}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5000)
