"""Lake inspection: ``python -m bip.tools.lake measure|verify|stats``.

``verify`` checks every file a table's manifest references (re-hashing by default) and reports
crash leftovers; it exits 1 when the table has integrity errors. ``stats`` prints committed
files, rows, bytes and bytes per row for every table with a manifest (or one table). ``measure``
encodes the newest stored database rows as Parquet and reports bytes per row, with the database's
data origin. Output is JSON. Nothing in the lake is written, repaired or deleted (``measure`` only
writes a temporary file it removes), and no network is used.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from bip.config import load_settings
from bip.core.exceptions import BIPError
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.storage.lake.integrity import table_stats, verify_table
from bip.storage.lake.partition import DATA_LAYERS
from bip.storage.paths import Layer, StorageRoot


def _tables(root: StorageRoot, layer: Layer) -> list[str]:
    folder = root.root / Layer.MANIFESTS.value / layer.value
    if not folder.is_dir():
        return []
    return sorted(p.name.removesuffix(".jsonl") for p in folder.glob("*.jsonl") if p.is_file())


def _emit(doc: object) -> None:
    sys.stdout.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inspect the BIP lake (read-only).")
    ap.add_argument("--data-root", type=Path, help="storage root (default: BIP_DATA_ROOT)")
    sub = ap.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="check one table's files against its manifest")
    v.add_argument("--layer", choices=sorted(x.value for x in DATA_LAYERS), required=True)
    v.add_argument("--chain", required=True)
    v.add_argument("--dataset", required=True)
    v.add_argument("--shallow", action="store_true", help="compare sizes only, do not re-hash")
    m = sub.add_parser("measure", help="Parquet bytes per row of stored database rows")
    m.add_argument("--limit", type=int, default=10_000, help="newest rows per table (max 1e6)")
    s = sub.add_parser("stats", help="committed files, rows and bytes per row")
    s.add_argument("--layer", choices=sorted(x.value for x in DATA_LAYERS))
    s.add_argument("--table", help="<chain>.<dataset>; default: every table with a manifest")
    args = ap.parse_args(argv)

    try:
        data_root = args.data_root or load_settings().resolved_data_root
        root = StorageRoot(data_root)
        root.require_exists()
        if args.command == "measure":
            from bip.storage.lake.datasets import measure_bytes_per_row
            from bip.storage.lake.runtime import require_lake

            require_lake()
            settings = load_settings()
            engine = create_db_engine(settings)
            try:
                limit = max(1, min(int(args.limit), 1_000_000))
                _emit(measure_bytes_per_row(Repository(engine), root.root, limit=limit))
            finally:
                engine.dispose()
            return 0
        if args.command == "verify":
            report = verify_table(
                root, Layer(args.layer), args.chain, args.dataset, deep=not args.shallow
            )
            _emit({**asdict(report), "ok": report.ok})
            return 0 if report.ok else 1
        layers = [Layer(args.layer)] if args.layer else sorted(DATA_LAYERS, key=lambda x: x.value)
        out: list[dict[str, object]] = []
        for layer in layers:
            for table in [args.table] if args.table else _tables(root, layer):
                st = table_stats(root, layer, table)
                out.append({**asdict(st), "bytes_per_row": st.bytes_per_row})
        _emit(out)
        return 0
    except (BIPError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
