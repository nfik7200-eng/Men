"""Offline tests for profile, behaviour rules, anomaly detectors, flows and confidence."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Any

import pytest

from bip.config.settings import IntelSettings
from bip.intel import anomaly, behaviour, flows, profile
from bip.intel.common import confidence, verification_summary
from bip.intel.dataset import WalletDataset
from tests.stage3.builders import T0, WALLET, Builder, a, daily

CFG = IntelSettings()
NOW = T0 + timedelta(days=60)


J = dict[str, Any]


def ctx(now: datetime = NOW, seen: dict[str, datetime | None] | None = None) -> anomaly.Context:
    return anomaly.Context(now=now, first_seen_targets=seen or {})


def by_id(results: list[J], key: str, value: str) -> J:
    return next(r for r in results if r[key] == value)


# ---------------------------------------------------------------- confidence / verification


def test_confidence_is_sample_times_verification_and_capped_when_unverified() -> None:
    assert confidence(30, ["unverified"] * 30)["value"] == 0.5
    assert confidence(30, ["cross_verified"] * 30)["value"] == 1.0
    assert confidence(3, ["cross_verified"] * 3)["value"] == 0.1
    assert confidence(30, ["cross_verified"] * 29 + ["conflict"])["label"] == "medium"


def test_verification_summary_uses_stage3_labels() -> None:
    s = verification_summary(["cross_verified", "single_source", "unverified"])
    assert s["counts"] == {"corroborated": 1, "unverified": 2}
    assert s["overall"] == "partially corroborated"
    assert verification_summary(["conflict"])["overall"] == "conflicted"
    assert verification_summary([])["overall"] == "no evidence"


# ---------------------------------------------------------------- profile


def test_profile_metrics_are_scope_labelled_and_never_lifetime() -> None:
    b = Builder()
    for i in range(15):
        b.transfer(T0 + timedelta(hours=i), direction="in", party=a(1), amount=10**18)
    p = profile.build_profile(b.ds)
    tx = by_id(p["metrics"], "id", "transactions")
    assert tx["label"] == "15 transactions observed in retrieved dataset"
    assert all(m["label"].endswith("in retrieved dataset") for m in p["metrics"])
    assert "not the wallet's lifetime history" in p["scope"]["statement"]
    assert p["volumes"][0]["in"] == "15"  # 15 x 1 ETH, scaled from raw
    assert p["counterparties"]["items"][0]["relationship"].startswith("observed transfer")
    assert any("No USD" in x for x in p["limitations"])


def test_profile_unknown_decimals_are_not_scaled_and_erc721_not_summed() -> None:
    b = Builder()
    b.transfer(
        T0, direction="in", party=a(1), amount=5, asset=a(9), decimals=None, standard="erc20"
    )
    b.transfer(
        T0, direction="in", party=a(1), amount=77, asset=a(8), decimals=None, standard="erc721"
    )
    vols = {v["asset"]: v for v in profile.build_profile(b.ds)["volumes"]}
    assert vols[a(9)]["in"] is None
    assert vols[a(9)]["in_raw"] == "5"
    assert vols[a(8)]["in"] is None
    assert vols[a(8)]["unit_note"].startswith("token ids")


def test_profile_deployments_and_interactions() -> None:
    b = Builder()
    b.tx(T0, to=None, created=a(77))
    for i in range(3):
        b.tx(T0 + timedelta(minutes=i), to=a(5))
    p = profile.build_profile(b.ds)
    assert p["deployments"][0]["created_contract"] == a(77)
    top = p["interactions"]["items"][0]
    assert top["target"] == a(5)
    assert top["count"] == 3
    assert top["kind"] == "address (contract status not checked)"


def test_profile_solana_names_only_protocol_defined_programs() -> None:
    b = Builder(chain="solana", wallet="W" * 32)
    b.tx(T0, programs=("11111111111111111111111111111111", "Unknown1111111111111111111111111"))
    items = {i["target"]: i for i in profile.build_profile(b.ds)["interactions"]["items"]}
    assert items["11111111111111111111111111111111"]["name"] == "System Program"
    assert items["Unknown1111111111111111111111111"]["name"] is None


# ---------------------------------------------------------------- behaviour


def test_rules_report_not_enough_data_on_empty_dataset() -> None:
    results = behaviour.evaluate(Builder().ds, CFG, NOW)
    assert {r["status"] for r in results} == {"not_enough_data"}
    assert all(r["confidence"] is None for r in results)


def test_high_frequency_and_bursts_detected_with_evidence() -> None:
    b = Builder()
    for i in range(12):
        b.tx(T0 + timedelta(minutes=i), to=a(3))
    res = behaviour.evaluate(b.ds, CFG, NOW)
    hf = by_id(res, "rule_id", "high_frequency")
    assert hf["status"] == "detected"
    assert hf["magnitude"]["transactions_per_active_day"] == 12
    assert hf["supporting_count"] == 12
    burst = by_id(res, "rule_id", "bursts")
    assert burst["status"] == "detected"
    assert burst["magnitude"]["largest_burst"] >= 5
    rep = by_id(res, "rule_id", "repeated_interactions")
    assert rep["status"] == "detected"
    assert rep["magnitude"]["target"] == a(3)


def test_concentration_and_repeated_counterparties() -> None:
    b = Builder()
    for i in range(8):
        b.transfer(T0 + timedelta(days=i), direction="out", party=a(1), amount=100)
    for i in range(4):
        b.transfer(T0 + timedelta(days=i), direction="in", party=a(10 + i), amount=100)
    res = behaviour.evaluate(b.ds, CFG, NOW)
    conc = by_id(res, "rule_id", "concentration")
    assert conc["status"] == "detected"
    assert conc["magnitude"]["share"] == pytest.approx(8 / 12, abs=1e-3)
    assert by_id(res, "rule_id", "repeated_counterparties")["status"] == "detected"


def test_service_like_pattern_is_a_hypothesis_without_identity() -> None:
    b = Builder()
    for i in range(30):
        b.transfer(T0 + timedelta(hours=i), direction="in", party=a(100 + i), amount=1)
        b.transfer(T0 + timedelta(hours=i, minutes=30), direction="out", party=a(200 + i), amount=1)
    r = by_id(behaviour.evaluate(b.ds, CFG, NOW), "rule_id", "service_like_pattern")
    assert r["status"] == "detected"
    assert r["claim_type"] == "hypothesis"
    assert r["name"].startswith("Possible")
    assert "does not identify" in r["explanation"]


def test_newly_observed_states_first_observation_not_creation() -> None:
    b = Builder()
    b.tx(NOW - timedelta(days=2), to=a(1))
    r = by_id(behaviour.evaluate(b.ds, CFG, NOW), "rule_id", "newly_observed")
    assert r["status"] == "detected"
    assert "not proof of the wallet's creation date" in r["explanation"]


# ---------------------------------------------------------------- anomalies


def statuses(ds: WalletDataset) -> dict[str, str]:
    return {r["detector_id"]: r["status"] for r in anomaly.run_detectors(ds, CFG, ctx())}


def test_detectors_distinguish_insufficient_data_and_could_not_run() -> None:
    assert set(statuses(Builder().ds).values()) == {"insufficient_data"}
    b = Builder()
    b.tx(T0, to=a(1))
    tx = b.ds.transactions[0]
    b.ds.transactions[0] = dataclasses.replace(tx, block_time=None)
    assert statuses(b.ds)["tx_frequency_spike"] == "could_not_run"


def test_frequency_spike_detected_and_no_anomaly_on_flat_series() -> None:
    flat = Builder()
    daily(flat, days=10, per_day=2)
    assert statuses(flat.ds)["tx_frequency_spike"] == "no_anomaly"
    spike = Builder()
    daily(spike, days=10, per_day=2)
    for i in range(20):
        spike.tx(T0 + timedelta(days=10, minutes=i), to=a(1))
    r = by_id(anomaly.run_detectors(spike.ds, CFG, ctx()), "detector_id", "tx_frequency_spike")
    assert r["status"] == "anomaly"
    assert r["observed"]["count"] == 20
    assert r["baseline_values"]["median"] == 2
    assert r["evidence"]["total"] == 20
    assert r["severity"] in {"low", "medium"}  # 20 baseline tx < 30: never high
    for key in (
        "detector_id",
        "version",
        "window",
        "minimum_data",
        "baseline",
        "thresholds",
        "severity_rule",
        "limitations",
        "explanation",
        "confidence",
    ):
        assert r[key]


def test_no_high_severity_from_small_samples() -> None:
    b = Builder()
    daily(b, days=8, per_day=1)  # 7 baseline tx
    for i in range(60):
        b.tx(T0 + timedelta(days=8, minutes=i), to=a(1))
    r = by_id(anomaly.run_detectors(b.ds, CFG, ctx()), "detector_id", "tx_frequency_spike")
    assert r["status"] == "anomaly"
    assert r["severity"] == "medium"


def test_high_severity_allowed_with_large_baseline() -> None:
    b = Builder()
    daily(b, days=15, per_day=3)  # 42 baseline tx
    for i in range(60):
        b.tx(T0 + timedelta(days=15, minutes=i), to=a(1))
    r = by_id(anomaly.run_detectors(b.ds, CFG, ctx()), "detector_id", "tx_frequency_spike")
    assert r["severity"] == "high"


def test_large_transfer_needs_ten_prior_and_detects_outlier() -> None:
    b = Builder()
    for i in range(9):
        b.transfer(T0 + timedelta(hours=i), direction="in", party=a(1), amount=100 + i)
    assert statuses(b.ds)["large_transfer"] == "insufficient_data"
    for i in range(3):
        b.transfer(T0 + timedelta(hours=10 + i), direction="in", party=a(1), amount=105 + i)
    assert statuses(b.ds)["large_transfer"] == "no_anomaly"
    big = b.transfer(T0 + timedelta(days=2), direction="out", party=a(2), amount=10**9)
    r = by_id(anomaly.run_detectors(b.ds, CFG, ctx()), "detector_id", "large_transfer")
    assert r["status"] == "anomaly"
    assert r["evidence"]["transfers"][0]["tx_id"] == big


def test_rapid_repeat_transfers() -> None:
    b = Builder()
    for i in range(6):
        b.transfer(T0 + timedelta(minutes=i), direction="out", party=a(4), amount=1)
    r = by_id(anomaly.run_detectors(b.ds, CFG, ctx()), "detector_id", "rapid_repeat_transfers")
    assert r["status"] == "anomaly"
    assert r["observed"]["count"] == 6


def test_new_counterparty_surge() -> None:
    b = Builder()
    for d in range(9):
        b.transfer(T0 + timedelta(days=d), direction="in", party=a(1), amount=1)
    for i in range(8):
        b.transfer(T0 + timedelta(days=9, minutes=i), direction="in", party=a(50 + i), amount=1)
    r = by_id(anomaly.run_detectors(b.ds, CFG, ctx()), "detector_id", "new_counterparty_surge")
    assert r["status"] == "anomaly"
    assert r["observed"]["new_counterparties"] == 8


def test_new_wallet_new_contract_is_low_severity() -> None:
    b = Builder()
    b.tx(NOW - timedelta(days=1), to=a(9))
    r = by_id(
        anomaly.run_detectors(b.ds, CFG, ctx(seen={a(9): NOW - timedelta(days=2)})),
        "detector_id",
        "new_wallet_new_contract",
    )
    assert r["status"] == "anomaly"
    assert r["severity"] == "low"
    r2 = by_id(
        anomaly.run_detectors(b.ds, CFG, ctx(seen={a(9): NOW - timedelta(days=90)})),
        "detector_id",
        "new_wallet_new_contract",
    )
    assert r2["status"] == "no_anomaly"


def test_detectors_are_deterministic() -> None:
    b = Builder()
    daily(b, days=10, per_day=2)
    assert anomaly.run_detectors(b.ds, CFG, ctx()) == anomaly.run_detectors(b.ds, CFG, ctx())


# ---------------------------------------------------------------- flows


def test_flows_per_asset_net_and_no_usd() -> None:
    b = Builder()
    b.transfer(T0, direction="in", party=a(1), amount=3 * 10**18)
    b.transfer(T0 + timedelta(hours=1), direction="out", party=a(2), amount=10**18)
    b.transfer(T0 + timedelta(hours=2), direction="out", party=a(2), amount=10**18)
    f = flows.build_flows(b.ds)
    native = f["assets"][0]
    assert (native["in"], native["out"], native["net"]) == ("3", "2", "1")
    assert f["usd_values"] is None
    assert f["canonical_source"] == "transfers"
    assert f["repeated_paths"] == [{"from": WALLET, "to": a(2), "asset": "native", "count": 2}]
    assert f["largest_transfers"]["native"][0]["amount"] == "3"
    assert {b["date"] for b in f["time_buckets"]} == {"2026-01-01"}


def test_flows_window_is_bounded_and_self_transfers_not_counted_twice() -> None:
    b = Builder()
    b.transfer(T0, direction="in", party=a(1), amount=5)
    b.transfer(T0 + timedelta(days=400), direction="in", party=a(1), amount=7)
    b.transfer(T0 + timedelta(days=400), direction="self", party=WALLET, amount=9)
    f = flows.build_flows(
        b.ds, since=T0 - timedelta(days=1), until=T0 + timedelta(days=401), max_window_days=30
    )
    assert f["assets"][0]["in_raw"] == "7"
    assert f["assets"][0]["self_count"] == 1
    assert f["assets"][0]["out_raw"] == "0"
