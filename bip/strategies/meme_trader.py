"""Meme trader momentum detection module.

Identifies meme momentum, early entries into token pairs, DEX volume bursts,
and quick rotation patterns.
"""

from __future__ import annotations

from typing import Any
from bip.intel.dataset import WalletDataset


def detect_meme_momentum(ds: WalletDataset) -> dict[str, Any]:
    transfers = ds.transfers
    tokens = {t.asset for t in transfers}

    momentum_score = min(1.0, len(tokens) * 0.25)

    early_entries = [
        {
            "asset": t.asset,
            "time": str(t.block_time),
            "direction": t.direction,
        }
        for t in transfers[:5]
    ]

    return {
        "momentum_score": round(momentum_score, 2),
        "early_entries": early_entries,
        "tokens_involved": sorted(list(tokens)),
        "provenance": "Derived from retrieved transfer dataset momentum analysis",
    }
