"""Wallet strategy classifier (heuristic).

Scores four archetypes from simple counts in the retrieved dataset:
- copy_target: many transactions (activity only; no win rate or profit is computed)
- meme_trader: many transfers per transaction and many distinct assets
- investor: few transactions over few assets
- dev: contracts recorded as created by the wallet
``unknown`` means too little stored history. Every result is a hypothesis.
"""

from __future__ import annotations

from typing import Any

from bip.intel.dataset import WalletDataset
from bip.strategies.evidence import DATASET_LIMITATION, HYPOTHESIS, deployments

PROVENANCE = "Derived from the retrieved dataset by the BIP strategy classifier rules"
LIMITATIONS = [
    DATASET_LIMITATION,
    "Scores are fixed rule weights over counts, not calibrated probabilities.",
    "No prices are used, so no profit, win rate or holding return is measured.",
]


def classify_wallet_profile(address: str, ds: WalletDataset) -> dict[str, Any]:
    tx_count = len(ds.transactions)
    transfer_count = len(ds.transfers)
    unique_tokens = {t.asset for t in ds.transfers}
    metrics = {
        "transactions_observed": tx_count,
        "transfers_observed": transfer_count,
        "unique_assets": len(unique_tokens),
    }
    base = {
        "address": address,
        "metrics": metrics,
        "provenance": PROVENANCE,
        "evidence_class": HYPOTHESIS,
        "limitations": LIMITATIONS,
    }

    if tx_count < 2 and transfer_count < 2:
        return {
            **base,
            "primary_strategy": "unknown",
            "confidence": 0.0,
            "strategies": {},
            "reason": "Insufficient transaction or transfer history in retrieved dataset",
        }

    meme_score = min(1.0, (transfer_count / max(1, tx_count)) * 0.3 + (len(unique_tokens) * 0.1))
    copy_target_score = min(1.0, 0.4 if tx_count > 10 else 0.2)
    investor_score = 0.8 if tx_count <= 15 and len(unique_tokens) <= 3 else 0.2
    dev_score = 0.9 if deployments(ds).contracts else 0.1

    scores = {
        "meme_trader": meme_score,
        "copy_target": copy_target_score,
        "investor": investor_score,
        "dev": dev_score,
    }
    primary = max(scores, key=lambda k: scores[k])
    return {
        **base,
        "primary_strategy": primary,
        "confidence": scores[primary],
        "strategies": scores,
    }
