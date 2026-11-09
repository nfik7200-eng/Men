"""Each detector against seeded stored data: it fires on the pattern and stays quiet otherwise."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from bip.config import load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.discovery.service import DiscoveryService
from tests.discovery.seed import E18, ETH, NOW, SOL, Seed, eth_addr, eth_tx

TOKEN = eth_addr(0x7000)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


def service(engine: Engine, **cfg: object) -> DiscoveryService:
    settings = load_settings(data_root=Path("/unused"), env="test", discovery=cfg)
    return DiscoveryService(Repository(engine), settings, clock=lambda: NOW)


def signals(svc: DiscoveryService, **filters: Any) -> list[dict[str, Any]]:
    return svc.list_signals(limit=200, **filters)["items"]


def seed_token_baseline(seed: Seed, n: int = 15) -> None:
    for i in range(n):
        when = NOW - timedelta(days=10, hours=i)
        seed.tx(eth_tx(i + 1), eth_addr(0x100 + i), when)
        seed.transfer(
            eth_tx(i + 1),
            eth_addr(0x100 + i),
            eth_addr(0x200),
            (1000 + i * 10) * E18,
            when,
            token=TOKEN,
        )


# ------------------------------------------------------------------ large transfers


def test_large_transfer_fires_with_an_explainable_score(engine: Engine) -> None:
    seed = Seed(engine)
    seed_token_baseline(seed)
    big = eth_tx(900)
    seed.tx(big, eth_addr(0x300), NOW - timedelta(hours=2))
    seed.transfer(
        big,
        eth_addr(0x300),
        eth_addr(0x301),
        5_000_000 * E18,
        NOW - timedelta(hours=2),
        token=TOKEN,
    )
    svc = service(engine)
    result = svc.run(ETH)
    assert result["status"] == "succeeded"
    found = signals(svc, detector_id="large_transfer_mad")
    assert len(found) == 1
    sig = found[0]
    assert sig["subject"] == {"kind": "token", "ref": TOKEN}
    assert sig["status"] == "open"
    assert sig["evidence"] == [{"kind": "transaction", "chain": ETH, "ref": big}]
    exp = sig["explanation"]
    assert exp["baseline"]["observations"] == 15
    assert exp["baseline"]["robust_z"] > 3.5
    names = {c["name"] for c in exp["score_components"]}
    assert names == {"magnitude", "baseline_reliability", "evidence_finality"}
    assert sum(c["contribution"] for c in exp["score_components"]) == pytest.approx(
        sig["score"], abs=0.05
    )
    assert 0 < sig["confidence"] <= 0.8
    assert sig["coverage"]["rows_scanned"] >= 16
    assert "Local database reads only" in sig["coverage"]["cost"]


def test_no_signal_without_enough_baseline(engine: Engine) -> None:
    seed = Seed(engine)
    seed_token_baseline(seed, n=5)  # below min_baseline (10)
    big = eth_tx(901)
    seed.tx(big, eth_addr(0x300), NOW - timedelta(hours=2))
    seed.transfer(
        big,
        eth_addr(0x300),
        eth_addr(0x301),
        5_000_000 * E18,
        NOW - timedelta(hours=2),
        token=TOKEN,
    )
    svc = service(engine)
    svc.run(ETH)
    assert signals(svc, detector_id="large_transfer_mad") == []


def test_ordinary_transfer_does_not_fire(engine: Engine) -> None:
    seed = Seed(engine)
    seed_token_baseline(seed)
    ok = eth_tx(902)
    seed.tx(ok, eth_addr(0x300), NOW - timedelta(hours=2))
    seed.transfer(
        ok, eth_addr(0x300), eth_addr(0x301), 1_050 * E18, NOW - timedelta(hours=2), token=TOKEN
    )
    svc = service(engine)
    svc.run(ETH)
    assert signals(svc, detector_id="large_transfer_mad") == []


def test_provisional_evidence_lowers_score_and_is_declared(engine: Engine) -> None:
    seed = Seed(engine)
    seed_token_baseline(seed)
    big = eth_tx(903)
    seed.tx(big, eth_addr(0x300), NOW - timedelta(hours=2), finality="observed")
    seed.transfer(
        big,
        eth_addr(0x300),
        eth_addr(0x301),
        5_000_000 * E18,
        NOW - timedelta(hours=2),
        token=TOKEN,
    )
    svc = service(engine)
    svc.run(ETH)
    sig = signals(svc, detector_id="large_transfer_mad")[0]
    assert sig["coverage"]["provisional_evidence"] is True
    finality = next(
        c for c in sig["explanation"]["score_components"] if c["name"] == "evidence_finality"
    )
    assert finality["value"] == 0.0
    assert "0 of 1" in finality["note"]


# ------------------------------------------------------------------ dormancy


def test_dormant_wallet_reactivation(engine: Engine) -> None:
    seed = Seed(engine)
    old, new = eth_tx(10), eth_tx(11)
    wallet = eth_addr(0xAA)
    seed.tx(old, wallet, NOW - timedelta(days=200))
    seed.tx(new, wallet, NOW - timedelta(hours=1))
    # a wallet whose only stored activity is recent is NOT dormant: nothing earlier is stored
    seed.tx(eth_tx(12), eth_addr(0xBB), NOW - timedelta(hours=1))
    svc = service(engine)
    svc.run(ETH)
    found = signals(svc, detector_id="dormant_reactivation")
    assert [s["subject"]["ref"] for s in found] == [wallet]
    assert found[0]["explanation"]["observed"]["gap_days"] > 190
    assert found[0]["confidence"] <= 0.5
    assert "stored data only" in found[0]["explanation"]["caveat"]


# ------------------------------------------------------------------ bursts


def test_activity_burst_against_the_wallets_own_baseline(engine: Engine) -> None:
    seed = Seed(engine)
    wallet = eth_addr(0xCC)
    n = 100
    for day in range(2, 14):  # 12 active days, two transactions each
        for k in range(2):
            n += 1
            seed.tx(eth_tx(n), wallet, NOW - timedelta(days=day, hours=k))
    for k in range(30):  # 30 transactions in the last 24 hours
        n += 1
        seed.tx(eth_tx(n), wallet, NOW - timedelta(minutes=10 * k + 5))
    svc = service(engine)
    svc.run(ETH)
    found = signals(svc, detector_id="activity_burst")
    assert len(found) == 1
    assert found[0]["explanation"]["observed"]["transactions_24h"] == 30
    assert found[0]["explanation"]["baseline"]["active_days"] == 12
    assert len(found[0]["evidence"]) == 20  # evidence is capped


def test_busy_but_steady_wallet_is_not_a_burst(engine: Engine) -> None:
    seed = Seed(engine)
    wallet = eth_addr(0xCD)
    n = 200
    for day in range(1, 14):
        for k in range(12):
            n += 1
            seed.tx(eth_tx(n), wallet, NOW - timedelta(days=day, minutes=k))
    for k in range(12):  # same pace in the last 24h
        n += 1
        seed.tx(eth_tx(n), wallet, NOW - timedelta(hours=1, minutes=k))
    svc = service(engine)
    svc.run(ETH)
    assert signals(svc, detector_id="activity_burst") == []


# ------------------------------------------------------------------ deployments


def test_contract_deployment_and_repeat_deployer(engine: Engine) -> None:
    seed = Seed(engine)
    single = eth_addr(0xD1)
    repeat = eth_addr(0xD2)
    seed.tx(eth_tx(30), single, NOW - timedelta(hours=3), created=eth_addr(0xC1))
    for i in range(5):
        seed.tx(
            eth_tx(40 + i), repeat, NOW - timedelta(hours=3, minutes=i), created=eth_addr(0xC10 + i)
        )
    svc = service(engine)
    svc.run(ETH)
    found = {s["subject"]["ref"]: s for s in signals(svc, detector_id="new_contract_deployment")}
    assert len(found) == 6
    assert found[eth_addr(0xC1)]["severity"] == "low"
    assert found[eth_addr(0xC1)]["explanation"]["observed"]["deployer"] == single
    assert found[eth_addr(0xC10)]["score"] > found[eth_addr(0xC1)]["score"]
    assert "intent" in found[eth_addr(0xC1)]["explanation"]["caveat"]


def test_solana_has_no_deployment_detector(engine: Engine) -> None:
    svc = service(engine)
    info = {d["id"]: d for d in svc.detectors()}
    assert info["new_contract_deployment"]["chains"] == ["ethereum"]
    assert SOL not in info["new_contract_deployment"]["chains"]


# ------------------------------------------------------------------ common funding


def test_common_funding_source_is_an_observation_not_attribution(engine: Engine) -> None:
    seed = Seed(engine)
    source = eth_addr(0xF0)
    old_recipient = eth_addr(0xF9)
    seed.tx(eth_tx(50), old_recipient, NOW - timedelta(days=5))  # has earlier stored activity
    for i, r in enumerate([eth_addr(0xF1), eth_addr(0xF2), eth_addr(0xF3), old_recipient]):
        when = NOW - timedelta(hours=2, minutes=i)
        seed.tx(eth_tx(60 + i), source, when, to=r)
        seed.transfer(eth_tx(60 + i), source, r, 2 * E18, when, decimals=18)
    svc = service(engine)
    svc.run(ETH)
    found = signals(svc, detector_id="common_funding_source")
    assert len(found) == 1
    sig = found[0]
    assert sig["subject"] == {"kind": "wallet", "ref": source}
    assert sig["explanation"]["observed"]["recipient_count"] == 3  # the known wallet is excluded
    assert old_recipient not in sig["explanation"]["observed"]["newly_seen_recipients"]
    assert "same owner" in sig["explanation"]["not_claimed"]
    assert sig["confidence"] <= 0.5
    assert sig["family"] == "related_wallets"


# ------------------------------------------------------------------ orphans


def test_orphaned_transactions_never_produce_signals(engine: Engine) -> None:
    seed = Seed(engine)
    seed_token_baseline(seed)
    big = eth_tx(910)
    seed.tx(big, eth_addr(0x300), NOW - timedelta(hours=2), finality="orphaned")
    seed.transfer(
        big,
        eth_addr(0x300),
        eth_addr(0x301),
        5_000_000 * E18,
        NOW - timedelta(hours=2),
        token=TOKEN,
    )
    seed.tx(eth_tx(911), eth_addr(0xAA), NOW - timedelta(days=200))
    seed.tx(eth_tx(912), eth_addr(0xAA), NOW - timedelta(hours=1), finality="orphaned")
    svc = service(engine)
    svc.run(ETH)
    assert signals(svc) == []


def test_detector_failure_is_reported_and_does_not_abort_the_pass(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bip.discovery import detectors as module
    from bip.discovery import service as service_module

    def boom(_ctx: object) -> object:
        raise RuntimeError("detector bug")

    broken = module.Detector(module.DETECTORS[0].info, boom)  # type: ignore[arg-type]
    monkeypatch.setattr(service_module, "DETECTORS", (broken, *module.DETECTORS[1:]))
    Seed(engine).tx(eth_tx(1), eth_addr(1), NOW - timedelta(hours=1))
    result = service(engine).run(ETH)
    assert result["status"] == "partial"
    assert result["errors"] == [
        {"detector": "large_transfer_mad:ethereum", "error": "RuntimeError"}
    ]
    assert "dormant_reactivation:ethereum" in result["detectors"]


def test_run_is_recorded_with_freshness(engine: Engine) -> None:
    svc = service(engine)
    assert svc.feed(10, 0)["freshness"]["stale"] is True  # never run
    svc.run(ETH)
    feed = svc.feed(10, 0)
    assert feed["freshness"]["last_run_status"] == "succeeded"
    assert feed["freshness"]["stale"] is False
    assert "not attribution" in feed["scope"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM discovery_runs")).scalar_one() == 1
