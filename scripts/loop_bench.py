"""Before/after benchmark for improvement loops: fixed assistant queries + deal ranking, timed, on a COPY of a snapshot.

    python -m watchtower.cli backup --out /tmp/snap.db
    python scripts/loop_bench.py /tmp/snap.db            # run on the old commit, then on the new one, diff the output

The given file is copied to a temporary directory first, so neither the snapshot nor the real DB is ever modified
(opening the app database runs init/ensure_* writes)."""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QUERIES = ["pi 5 2gb", "raspberry pi 4 4gb", "raspberry pi 5 8gb", "raspberry pi 5 8 GB", "pi zero 2 w",
           "raspberry pi 5 case", "raspberry pi 5 active cooler", "compute module 5 2gb", "usb c power supply raspberry pi 5"]


def main(snap: str) -> None:
    tmp = Path(tempfile.mkdtemp()) / "bench.db"
    shutil.copyfile(snap, tmp)  # a snapshot from `cli backup` is a single self-contained file
    os.environ["WATCHTOWER_DB_URL"] = f"sqlite:///{tmp}"
    from shopping import assistant
    from watchtower import deals
    from watchtower.db import Database
    db = Database(os.environ["WATCHTOWER_DB_URL"])
    with db.session() as s:
        t = time.perf_counter()
        top = deals.top_deals(s, 20)
        print(f"top_deals: {len(top)} results in {(time.perf_counter() - t) * 1000:.0f} ms")
        for q in QUERIES:
            t = time.perf_counter()
            r = assistant.answer(s, q, use_llm=False)
            ms = (time.perf_counter() - t) * 1000
            picks = "; ".join(f"{p.listing.source.key}:{p.listing.title[:40]}({p.listing.availability})" for p in r["picks"])
            print(f"{q:36} {ms:5.0f} ms | {picks or '-'}" + (" | " + " / ".join(c[:60] for c in r["caveats"]) if r["caveats"] else ""))
    shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 2 or Path(sys.argv[1]).resolve() == (Path(__file__).resolve().parent.parent / "data" / "watchtower.db"):
        sys.exit("usage: loop_bench.py <snapshot.db>  (a snapshot from `watchtower.cli backup`, never the live DB)")
    main(sys.argv[1])
