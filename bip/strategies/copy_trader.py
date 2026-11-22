"""Copy-trader research leads (heuristic).

Rates how active a wallet is in the retrieved dataset. It does not measure trade success: no
prices are used, so there is no win rate, profit or timing analysis, and the output never says
to copy a wallet (ADR-024: research leads, not recommendations).
"""

from __future__ import annotations

from typing import Any

from bip.intel.dataset import WalletDataset
from bip.strategies.evidence import DATASET_LIMITATION, HYPOTHESIS

LIMITATIONS = [
    DATASET_LIMITATION,
    "Activity only: no win rate, profit or trade timing is measured (no price source).",
    "A research lead to investigate, not advice to copy this wallet.",
]
LEAD_THRESHOLD = 0.5


def evaluate_copy_trade_signals(ds: WalletDataset, target_address: str) -> dict[str, Any]:
    tx_count = len(ds.transactions)
    base = {
        "target_address": target_address,
        "provenance": "Evaluated from retrieved dataset transactions",
        "evidence_class": HYPOTHESIS,
        "limitations": LIMITATIONS,
    }
    if not ds.transactions and not ds.transfers:
        return {**base, "score": 0.0, "signals": [], "lead": "no_activity"}

    score = min(1.0, tx_count / 20.0)
    signals = [
        {
            "type": "activity_frequency",
            "value": tx_count,
            "description": f"Observed {tx_count} transactions in dataset",
        }
    ]
    lead = "research_lead" if score > LEAD_THRESHOLD else "low_activity"
    return {**base, "score": round(score, 2), "signals": signals, "lead": lead}
