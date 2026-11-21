"""Wallet strategy classifier.

Classifies wallet profiles into specific trading and operational strategies:
- copy_target (high win rate, consistent replication potential)
- meme_trader (high frequency token swaps, early buys, short holding times)
- investor (low frequency, high value, long holding duration)
- dev (deployments, contract interactions, funding source)
- unknown (insufficient data)
"""

from __future__ import annotations

from typing import Any

from bip.intel.dataset import WalletDataset


def classify_wallet_profile(address: str, ds: WalletDataset) -> dict[str, Any]:
    tx_count = len(ds.transactions)
    transfer_count = len(ds.transfers)

    if tx_count < 2 and transfer_count < 2:
        return {
            "address": address,
            "primary_strategy": "unknown",
            "confidence": 0.0,
            "strategies": {},
            "reason": "Insufficient transaction or transfer history in retrieved dataset",
        }

    unique_tokens = {t.asset for t in ds.transfers}

    meme_score = min(1.0, (transfer_count / max(1, tx_count)) * 0.3 + (len(unique_tokens) * 0.1))
    copy_target_score = min(1.0, 0.4 if tx_count > 10 else 0.2)
    investor_score = 0.8 if tx_count <= 15 and len(unique_tokens) <= 3 else 0.2
    dev_score = (
        0.9
        if any(t.created_contract is not None or t.to_address is None for t in ds.transactions)
        else 0.1
    )

    scores = {
        "meme_trader": meme_score,
        "copy_target": copy_target_score,
        "investor": investor_score,
        "dev": dev_score,
    }

    primary = max(scores, key=lambda k: scores[k])

    return {
        "address": address,
        "primary_strategy": primary,
        "confidence": scores[primary],
        "strategies": scores,
        "metrics": {
            "transactions_observed": tx_count,
            "transfers_observed": transfer_count,
            "unique_assets": len(unique_tokens),
        },
        "provenance": "Derived from retrieved dataset via BIP strategy classifier rules",
    }
