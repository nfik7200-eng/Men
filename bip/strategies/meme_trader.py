"""Meme momentum (heuristic).

Counts the distinct assets the wallet moved and lists its first stored transfers in time order.
"First" means first in the retrieved dataset, not an early entry into a token: BIP has no
token launch times or market data here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bip.intel.dataset import TransferRow, WalletDataset
from bip.strategies.evidence import DATASET_LIMITATION, HYPOTHESIS

FIRST_TRANSFERS = 5
LIMITATIONS = [
    DATASET_LIMITATION,
    "The score counts distinct assets only; no volume, liquidity or price data is used.",
    "First transfers are the earliest stored, not entries relative to a token's launch.",
]


def _order(t: TransferRow) -> tuple[bool, datetime]:
    # Rows without a time sort last; the placeholder is never compared with a real time.
    return (t.block_time is None, t.block_time or datetime.min.replace(tzinfo=UTC))


def detect_meme_momentum(ds: WalletDataset) -> dict[str, Any]:
    tokens = {t.asset for t in ds.transfers}
    ordered = sorted(ds.transfers, key=_order)
    return {
        "momentum_score": round(min(1.0, len(tokens) * 0.25), 2),
        "first_transfers": [
            {
                "asset": t.asset,
                "time": t.block_time.isoformat() if t.block_time else None,
                "direction": t.direction,
            }
            for t in ordered[:FIRST_TRANSFERS]
        ],
        "tokens_involved": sorted(tokens),
        "provenance": "Derived from the retrieved transfer dataset",
        "evidence_class": HYPOTHESIS,
        "limitations": LIMITATIONS,
    }
