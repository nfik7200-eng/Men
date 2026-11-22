"""Strategy outputs claim only what the stored dataset shows (CLAUDE.md §2.7, §2.11, §2.12).

Every output is labelled a hypothesis with its limitations. Facts BIP never checked (GitHub
links, upgrade authority) are ``None`` and listed as not checked, never a made-up boolean.
No output tells anyone to copy a wallet (ADR-024: research leads, not recommendations).
"""

from __future__ import annotations

from datetime import timedelta

from bip.strategies.classifier import classify_wallet_profile
from bip.strategies.copy_trader import evaluate_copy_trade_signals
from bip.strategies.dev_tracker import track_developer_activity
from bip.strategies.meme_trader import detect_meme_momentum
from tests.stage3.builders import T0, WALLET, Builder, a


def _labelled(out: dict[str, object]) -> None:
    assert out["evidence_class"] == "hypothesis"
    limitations = out["limitations"]
    assert isinstance(limitations, list)
    assert limitations


# ------------------------------------------------------------------ classifier


def test_unknown_classification_keeps_the_full_shape() -> None:
    out = classify_wallet_profile(WALLET, Builder().ds)
    assert out["primary_strategy"] == "unknown"
    assert out["metrics"] == {
        "transactions_observed": 0,
        "transfers_observed": 0,
        "unique_assets": 0,
    }
    assert out["provenance"]
    _labelled(out)


def test_classification_is_labelled_a_hypothesis() -> None:
    b = Builder()
    for i in range(3):
        b.transfer(T0 + timedelta(hours=i), direction="out", party=a(10 + i), amount=10**18)
    out = classify_wallet_profile(WALLET, b.ds)
    assert out["primary_strategy"] != "unknown"
    _labelled(out)


def test_solana_transactions_without_a_recipient_are_not_deployments() -> None:
    b = Builder(chain="solana", wallet="So1anaWa11et1111111111111111111111111111111")
    for i in range(5):
        b.tx(T0 + timedelta(minutes=i))  # Solana rows have no to_address
    out = classify_wallet_profile(b.ds.address, b.ds)
    assert out["primary_strategy"] != "dev"
    dev = track_developer_activity(b.ds, b.ds.address)
    assert dev["deployments_count"] == 0


# ------------------------------------------------------------------ copy trader


def test_copy_trader_never_recommends_copying() -> None:
    b = Builder()
    for i in range(30):
        b.tx(T0 + timedelta(minutes=i), to=a(5))
    out = evaluate_copy_trade_signals(b.ds, WALLET)
    assert "recommendation" not in out
    assert out["lead"] == "research_lead"
    _labelled(out)
    assert any("win rate" in str(x) for x in out["limitations"])  # type: ignore[union-attr]


def test_copy_trader_leads_by_activity() -> None:
    assert evaluate_copy_trade_signals(Builder().ds, WALLET)["lead"] == "no_activity"
    b = Builder()
    b.tx(T0, to=a(5))
    out = evaluate_copy_trade_signals(b.ds, WALLET)
    assert out["lead"] == "low_activity"
    _labelled(out)


# ------------------------------------------------------------------ developer tracker


def test_unchecked_facts_are_unknown_not_invented() -> None:
    b = Builder()
    b.tx(T0, created=a(77))
    out = track_developer_activity(b.ds, WALLET)
    assert out["github_linked"] is None
    assert out["upgrade_authority_held"] is None
    assert set(out["not_checked"]) == {"github_linked", "upgrade_authority_held"}  # type: ignore[arg-type]
    assert out["deployments"] == [a(77)]
    _labelled(out)


def test_funder_is_the_earliest_inbound_native_sender_never_the_wallet_itself() -> None:
    b = Builder()
    b.tx(T0, to=a(5))  # the wallet's own outgoing transaction comes first
    b.transfer(T0 + timedelta(hours=2), direction="in", party=a(9), amount=10**18)
    b.transfer(T0 + timedelta(hours=1), direction="in", party=a(8), amount=10**18)
    b.transfer(
        T0, direction="in", party=a(7), amount=10**18, asset=a(500), standard="erc20"
    )  # a token, not native funding
    out = track_developer_activity(b.ds, WALLET)
    assert out["funder"] == a(8)
    assert out["funder"] != WALLET
    assert "hypothesis" in str(out["funder_basis"])


def test_no_inbound_native_transfer_means_no_funder() -> None:
    b = Builder()
    b.tx(T0, to=a(5))
    out = track_developer_activity(b.ds, WALLET)
    assert out["funder"] is None
    assert out["funder_basis"] is None


def test_ethereum_creation_without_a_recorded_contract_is_reported_separately() -> None:
    b = Builder()
    b.tx(T0)  # to=None, but no created_contract recorded (receipt not parsed)
    out = track_developer_activity(b.ds, WALLET)
    assert out["deployments_count"] == 0
    assert out["unconfirmed_creations"] == 1


# ------------------------------------------------------------------ meme momentum


def test_first_transfers_are_chronological_and_named_for_what_they_are() -> None:
    b = Builder()
    for i, h in enumerate([5, 1, 3, 2, 4, 6]):
        b.transfer(T0 + timedelta(hours=h), direction="in", party=a(i), amount=1, asset=a(100 + i))
    out = detect_meme_momentum(b.ds)
    assert "early_entries" not in out
    times = [e["time"] for e in out["first_transfers"]]  # type: ignore[union-attr]
    assert len(times) == 5
    assert times == sorted(times)
    _labelled(out)
