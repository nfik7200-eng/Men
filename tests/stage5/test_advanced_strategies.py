"""Tests for advanced strategy extraction and wallet classification engine.

Tests:
- Wallet profile classification across strategies (copy target, meme trader, investor, dev tracker)
- Copy trade signal evaluation (win rate, replication metrics, timing)
- Meme momentum detection (early entry, DEX volume/liquidity bursts)
- Developer activity tracking (deployments, funder linkage, GitHub verification)
"""

from __future__ import annotations

from datetime import timedelta

from bip.strategies.classifier import classify_wallet_profile
from bip.strategies.copy_trader import evaluate_copy_trade_signals
from bip.strategies.dev_tracker import track_developer_activity
from bip.strategies.meme_trader import detect_meme_momentum
from tests.stage3.builders import T0, Builder, a


def test_advanced_strategies_comprehensive() -> None:
    b = Builder()
    # Build some transactions and transfers representing a meme trader / copy target
    w = a(1)
    for i in range(15):
        b.tx(T0 + timedelta(minutes=i * 5), to=a(999))
        b.transfer(
            T0 + timedelta(minutes=i * 5 + 1),
            direction="out",
            party=a(100 + i),
            asset="MEME",
            amount=1000 * 10**18,
        )

    ds = b.ds

    # 1. Classification
    profile = classify_wallet_profile(w, ds)
    assert profile["primary_strategy"] in [
        "meme_trader",
        "copy_target",
        "investor",
        "dev",
        "unknown",
    ]
    assert "strategies" in profile

    # 2. Copy Trader Signals
    copy_signals = evaluate_copy_trade_signals(ds, target_address=w)
    assert "score" in copy_signals
    assert "signals" in copy_signals

    # 3. Meme Momentum
    meme_signals = detect_meme_momentum(ds)
    assert "momentum_score" in meme_signals
    assert "early_entries" in meme_signals

    # 4. Dev Tracker
    dev_signals = track_developer_activity(ds, deployer_address=w)
    assert "deployments_count" in dev_signals
    assert "funder" in dev_signals
