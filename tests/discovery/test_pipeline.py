"""Deduplication, cooldown, expiry, alerts, reorg invalidation and promotion."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from bip.config import load_settings
from bip.db.engine import create_db_engine
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.observations import ObservationStore
from bip.db.repository import Repository
from bip.discovery.service import DiscoveryService
from tests.discovery.seed import E18, ETH, NOW, Seed, eth_addr, eth_tx

TOKEN = eth_addr(0x7000)
OTHER_TOKEN = eth_addr(0x7001)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make(engine: Engine, clock: Clock | None = None, **cfg: object) -> DiscoveryService:
    settings = load_settings(data_root=Path("/unused"), env="test", discovery=cfg)
    return DiscoveryService(Repository(engine), settings, clock=clock or Clock(NOW))


def baseline(seed: Seed, token: str = TOKEN, n: int = 15) -> None:
    for i in range(n):
        when = NOW - timedelta(days=10, hours=i)
        # int(token, 16), not hash(token): str hashes are randomised per process, so two tokens
        # could land in one bucket and collide on a transaction id (a rare CI-only failure)
        tx = eth_tx(5_000 + int(token, 16) % 1000 * 100 + i)
        seed.tx(tx, eth_addr(0x100 + i), when)
        seed.transfer(
            tx, eth_addr(0x100 + i), eth_addr(0x200), (1000 + i * 10) * E18, when, token=token
        )


def big(seed: Seed, n: int, token: str = TOKEN, hours: float = 2) -> str:
    tx = eth_tx(n)
    when = NOW - timedelta(hours=hours)
    seed.tx(tx, eth_addr(0x300 + n), when)
    seed.transfer(tx, eth_addr(0x300 + n), eth_addr(0x400), 5_000_000 * E18, when, token=token)
    return tx


def items(svc: DiscoveryService, **f: Any) -> list[dict[str, Any]]:
    return svc.list_signals(limit=200, **f)["items"]


# ------------------------------------------------------------------ dedupe and cooldown


def test_rerun_refreshes_the_same_signal_instead_of_duplicating(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed)
    big(seed, 900)
    svc = make(engine)
    first = svc.run(ETH)
    second = svc.run(ETH)
    d1 = first["detectors"]["large_transfer_mad:ethereum"]
    d2 = second["detectors"]["large_transfer_mad:ethereum"]
    assert (d1["created"], d2["created"], d2["refreshed"]) == (1, 0, 1)
    sig = items(svc, detector_id="large_transfer_mad")[0]
    assert sig["occurrences"] == 2
    assert len(items(svc)) == 1


def test_cooldown_merges_new_events_for_the_same_subject(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed)
    a, b = big(seed, 900, hours=3), big(seed, 901, hours=2)
    svc = make(engine)
    stats = svc.run(ETH)["detectors"]["large_transfer_mad:ethereum"]
    assert (stats["created"], stats["merged"]) == (1, 1)
    sig = items(svc, detector_id="large_transfer_mad")[0]
    assert sig["occurrences"] == 2
    assert {e["ref"] for e in sig["evidence"]} == {a, b}


def test_different_subjects_are_separate_candidates(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed, TOKEN)
    baseline(seed, OTHER_TOKEN)
    big(seed, 900, TOKEN)
    big(seed, 901, OTHER_TOKEN)
    svc = make(engine)
    svc.run(ETH)
    assert {s["subject"]["ref"] for s in items(svc, detector_id="large_transfer_mad")} == {
        TOKEN,
        OTHER_TOKEN,
    }


def test_dismissed_signal_stays_dismissed_during_cooldown(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed)
    big(seed, 900, hours=3)
    svc = make(engine)
    svc.run(ETH)
    sig = items(svc)[0]
    svc.dismiss(sig["id"], "known exchange rebalance")
    big(seed, 901, hours=2)  # a new event for the same subject, inside the cooldown
    stats = svc.run(ETH)["detectors"]["large_transfer_mad:ethereum"]
    assert stats["created"] == 0
    assert stats["suppressed"] >= 1
    assert [s["status"] for s in items(svc)] == ["dismissed"]
    assert items(svc)[0]["status_note"] == "known exchange rebalance"


def test_new_signal_after_cooldown_ends(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed)
    big(seed, 900, hours=3)
    clock = Clock(NOW)
    svc = make(engine, clock, cooldown_seconds=3600)
    svc.run(ETH)
    clock.now = NOW + timedelta(hours=5)  # first signal is 5h old: outside the 1h cooldown
    seed2 = Seed(engine)
    # a later event inside the (shifted) lookback window
    tx = eth_tx(950)
    when = clock.now - timedelta(hours=1)
    seed2.tx(tx, eth_addr(0x999), when)
    seed2.transfer(tx, eth_addr(0x999), eth_addr(0x400), 7_000_000 * E18, when, token=TOKEN)
    svc.run(ETH)
    assert len(items(svc, detector_id="large_transfer_mad")) == 2


def test_per_run_signal_cap_is_applied_and_reported(engine: Engine) -> None:
    seed = Seed(engine)
    for k in range(3):
        t = eth_addr(0x7100 + k)
        baseline(seed, t)
        big(seed, 900 + k, t)
    svc = make(engine, max_signals_per_detector=2)
    stats = svc.run(ETH)["detectors"]["large_transfer_mad:ethereum"]
    assert stats["candidates"] == 3
    assert stats["dropped_by_signal_cap"] == 1
    assert len(items(svc)) == 2


# ------------------------------------------------------------------ expiry


def test_open_signals_expire_after_their_review_window(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed)
    big(seed, 900)
    clock = Clock(NOW)
    svc = make(engine, clock, ttl_days=7)
    svc.run(ETH)
    assert [s["status"] for s in items(svc)] == ["open"]
    clock.now = NOW + timedelta(days=8)
    summary = svc.run(ETH)
    assert summary["expired"] == 1
    assert [s["status"] for s in items(svc)] == ["expired"]
    assert items(svc, status="open") == []
    assert svc.prune(older_than_days=30)["signals_deleted"] == 0  # only 8 days old
    assert svc.prune(older_than_days=7)["signals_deleted"] == 1
    assert items(svc) == []


# ------------------------------------------------------------------ alerts


def test_alerts_follow_rules_and_never_duplicate(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed)
    big(seed, 900)
    svc = make(engine)
    rule = svc.create_rule("everything", min_severity="low")
    svc.create_rule("only related wallets", min_severity="low", families=["related_wallets"])
    svc.create_rule("off", min_severity="low", enabled=False)
    first = svc.run(ETH)
    assert first["alerts_created"] == 1
    second = svc.run(ETH)
    assert second["alerts_created"] == 0
    listing = svc.list_alerts(unread_only=True, limit=10, offset=0)
    assert (listing["total"], listing["unread"]) == (1, 1)
    alert = listing["items"][0]
    assert alert["rule_id"] == rule["id"]
    assert alert["delivery"] == "delivered_in_app"
    svc.mark_read(alert["id"])
    after = svc.list_alerts(unread_only=True, limit=10, offset=0)
    assert (after["total"], after["unread"]) == (0, 0)
    assert svc.list_alerts(unread_only=False, limit=10, offset=0)["total"] == 1
    assert svc.mark_all_read() == 0


def test_rule_thresholds_filter_alerts(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed)
    big(seed, 900)
    svc = make(engine)
    svc.create_rule("too strict", min_severity="high", min_score=99)
    assert svc.run(ETH)["alerts_created"] == 0


def test_rule_validation(engine: Engine) -> None:
    svc = make(engine)
    with pytest.raises(InvalidRecordError):
        svc.create_rule("bad", min_severity="critical")
    with pytest.raises(InvalidRecordError):
        svc.create_rule("bad", families=["nonexistent"])
    svc.create_rule("dup")
    with pytest.raises(InvalidRecordError):
        svc.create_rule("dup")
    rule = svc.create_rule("edit")
    changed = svc.update_rule(rule["id"], {"enabled": False, "min_score": 50.0})
    assert (changed["enabled"], changed["min_score"]) == (False, 50.0)
    svc.delete_rule(rule["id"])
    with pytest.raises(RecordNotFoundError):
        svc.update_rule(rule["id"], {"enabled": True})


def test_only_in_app_delivery_is_available(engine: Engine) -> None:
    channels = make(engine).channels()
    assert channels["available"] == ["in_app"]
    assert set(channels["unavailable"]) == {"email", "push", "sms"}


# ------------------------------------------------------------------ reorg invalidation


def test_signals_are_invalidated_when_their_evidence_is_orphaned(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed)
    tx = eth_tx(900)
    when = NOW - timedelta(hours=2)
    seed.tx(tx, eth_addr(0x300), when, block_hash="0x" + "ee" * 32)
    seed.transfer(tx, eth_addr(0x300), eth_addr(0x400), 5_000_000 * E18, when, token=TOKEN)
    svc = make(engine)
    svc.run(ETH)
    sig = items(svc)[0]
    assert sig["status"] == "open"
    with engine.connect() as conn:
        number = conn.execute(
            text("SELECT block_number FROM transactions WHERE tx_id = :t"), {"t": tx}
        ).scalar_one()
    effects = ObservationStore(Repository(engine)).mark_ethereum_block_orphaned(
        int(number), "0x" + "ee" * 32, "block no longer canonical"
    )
    assert (effects.transactions, effects.signals_invalidated) == (1, 1)
    after = svc.get_signal(sig["id"])
    assert after["status"] == "invalidated"
    assert "no longer canonical" in after["status_note"]
    assert items(svc, status="open") == []
    # a fresh pass does not resurrect it
    svc.run(ETH)
    assert items(svc, status="open") == []


def test_dismissed_signals_are_not_touched_by_orphaning(engine: Engine) -> None:
    seed = Seed(engine)
    baseline(seed)
    tx = eth_tx(901)
    when = NOW - timedelta(hours=2)
    seed.tx(tx, eth_addr(0x300), when, block_hash="0x" + "ef" * 32)
    seed.transfer(tx, eth_addr(0x300), eth_addr(0x400), 5_000_000 * E18, when, token=TOKEN)
    svc = make(engine)
    svc.run(ETH)
    svc.dismiss(items(svc)[0]["id"], None)
    with engine.connect() as conn:
        number = conn.execute(
            text("SELECT block_number FROM transactions WHERE tx_id = :t"), {"t": tx}
        ).scalar_one()
    ObservationStore(Repository(engine)).mark_ethereum_block_orphaned(
        int(number), "0x" + "ef" * 32, "reorg"
    )
    assert items(svc)[0]["status"] == "dismissed"


# ------------------------------------------------------------------ promotion


def seeded_funding_signal(engine: Engine) -> tuple[DiscoveryService, dict[str, Any]]:
    seed = Seed(engine)
    source = eth_addr(0xF0)
    for i in range(3):
        when = NOW - timedelta(hours=2, minutes=i)
        seed.tx(eth_tx(60 + i), source, when, to=eth_addr(0xF1 + i))
        seed.transfer(eth_tx(60 + i), source, eth_addr(0xF1 + i), 2 * E18, when)
    svc = make(engine)
    svc.run(ETH)
    return svc, items(svc, detector_id="common_funding_source")[0]


def test_promote_to_watchlist(engine: Engine) -> None:
    svc, sig = seeded_funding_signal(engine)
    repo = Repository(engine)
    wl = repo.create_watchlist("Review", None)
    promoted = svc.promote_to_watchlist(sig["id"], wl.id, None)
    assert promoted["status"] == "promoted"
    assert promoted["promoted_to"] == f"watchlist:{wl.id}"
    entries = repo.get_watchlist(wl.id).entries
    assert [e.address for e in entries] == [eth_addr(0xF0)]
    assert "from signal" in (entries[0].note or "")


def test_promote_to_investigation_creates_an_unverified_finding(engine: Engine) -> None:
    svc, sig = seeded_funding_signal(engine)
    repo = Repository(engine)
    case = repo.create_investigation("Funding review", None, None)
    promoted = svc.promote_to_investigation(sig["id"], case.id)
    assert promoted["promoted_to"] == f"investigation:{case.id}"
    findings = repo.get_investigation(case.id).findings
    assert len(findings) == 1
    assert findings[0].status == "unverified"
    with engine.connect() as conn:
        kinds = {
            k for (k,) in conn.execute(text("SELECT kind FROM evidence_refs ORDER BY id")).all()
        }
    assert kinds == {"derived_metric", "chain_record"}


def test_a_closed_signal_cannot_be_promoted_or_dismissed_again(engine: Engine) -> None:
    svc, sig = seeded_funding_signal(engine)
    svc.dismiss(sig["id"], None)
    with pytest.raises(InvalidRecordError):
        svc.promote_to_watchlist(sig["id"], 1, None)
    with pytest.raises(InvalidRecordError):
        svc.dismiss(sig["id"], None)
    with pytest.raises(RecordNotFoundError):
        svc.get_signal(99_999)


def test_native_asset_signal_has_nothing_to_watch(engine: Engine) -> None:
    seed = Seed(engine)
    for i in range(15):
        when = NOW - timedelta(days=10, hours=i)
        seed.tx(eth_tx(7_000 + i), eth_addr(0x100 + i), when)
        seed.transfer(
            eth_tx(7_000 + i), eth_addr(0x100 + i), eth_addr(0x200), (1 + i // 5) * E18, when
        )
    when = NOW - timedelta(hours=1)
    seed.tx(eth_tx(7_100), eth_addr(0x300), when)
    seed.transfer(eth_tx(7_100), eth_addr(0x300), eth_addr(0x400), 90_000 * E18, when)
    svc = make(engine)
    svc.run(ETH)
    sig = items(svc, detector_id="large_transfer_mad")[0]
    assert sig["subject"] == {"kind": "asset", "ref": "native"}
    with pytest.raises(InvalidRecordError):
        svc.promote_to_watchlist(sig["id"], 1, None)


# ------------------------------------------------------------------ as-of (historical) runs


def deploy(seed: Seed, n: int, when: datetime) -> str:
    contract = eth_addr(0xC000 + n)
    seed.tx(eth_tx(9_000 + n), eth_addr(0xD000 + n), when, created=contract)
    return contract


def subjects(svc: DiscoveryService, detector: str) -> set[str]:
    return {i["subject"]["ref"] for i in items(svc) if i["detector"]["id"] == detector}


def test_an_as_of_run_finds_candidates_in_backfilled_history(engine: Engine) -> None:
    seed = Seed(engine)
    as_of = NOW - timedelta(days=60)
    old = deploy(seed, 1, as_of - timedelta(hours=5))  # backfilled: far outside the live window
    later = deploy(seed, 2, as_of + timedelta(hours=1))  # after the evaluation time
    svc = make(engine)
    live = svc.run(ETH)
    assert subjects(svc, "new_contract_deployment") == set()  # the live window never sees it
    assert live["evaluated_as_of"] is None
    summary = svc.run(ETH, as_of=as_of)
    assert summary["evaluated_as_of"] == as_of.isoformat()
    found = subjects(svc, "new_contract_deployment")
    assert old in found
    assert later not in found  # nothing after the evaluation time leaks into the run
    [sig] = [i for i in items(svc) if i["subject"]["ref"] == old]
    detail = svc.get_signal(sig["id"])
    assert detail["explanation"]["evaluated_as_of"] == as_of.isoformat()
    assert detail["status"] == "open"  # stored on the wall clock: not born expired
    again = svc.run(ETH, as_of=as_of)
    assert again["detectors"]["new_contract_deployment:ethereum"]["created"] == 0  # idempotent


def test_an_as_of_run_skips_current_state_detectors_and_never_alerts(engine: Engine) -> None:
    seed = Seed(engine)
    as_of = NOW - timedelta(days=60)
    deploy(seed, 3, as_of - timedelta(hours=2))
    svc = make(engine)
    svc.create_rule("everything", min_severity="low")
    summary = svc.run(ETH, as_of=as_of)
    assert summary["alerts_created"] == 0  # history is not news
    skipped = set(summary["not_evaluable_as_of"])
    assert {"new_token_activity", "holder_concentration"} <= skipped
    assert not any(k.split(":")[0] in skipped for k in summary["detectors"])


def test_as_of_must_be_aware_past_and_within_the_limit(engine: Engine) -> None:
    svc = make(engine, as_of_max_days=90)
    with pytest.raises(InvalidRecordError, match="future"):
        svc.run(ETH, as_of=NOW + timedelta(hours=1))
    with pytest.raises(InvalidRecordError, match="90 days"):
        svc.run(ETH, as_of=NOW - timedelta(days=91))
    with pytest.raises(InvalidRecordError, match="time zone"):
        svc.run(ETH, as_of=NOW.replace(tzinfo=None) - timedelta(days=1))


# ------------------------------------------------------------------ freshness by run kind


def test_historical_and_expansion_runs_never_make_live_discovery_look_fresh(engine: Engine) -> None:
    clock = Clock(NOW)
    svc = make(engine, clock)
    svc.run(ETH)  # the live pass
    clock.now = NOW + timedelta(hours=30)  # more than a day later ...
    svc.run(ETH, as_of=NOW - timedelta(days=30))  # ... only a historical run happened
    from bip.discovery.detectors import DetectorOutput

    class _Producer:
        class info:  # noqa: N801 - mimics the detector interface
            id = "expansion_test"
            version = "1"
            family = "related_wallets"
            confidence_cap = 0.4
            coverage = limitations = "test"

    svc.run_producer(_Producer(), DetectorOutput([], 0, False), ETH, {"policy": "test"})
    feed = svc.feed(limit=10, offset=0)
    fresh = feed["freshness"]
    assert fresh["stale"] is True  # live discovery is 30 hours old
    assert fresh["last_run_at"] == NOW.isoformat()
    assert fresh["last_historical_run_at"] == (NOW + timedelta(hours=30)).isoformat()
    assert fresh["last_expansion_run_at"] == (NOW + timedelta(hours=30)).isoformat()
    kinds = [r["summary"]["kind"] for r in svc.runs(10)]
    assert kinds == ["producer", "historical", "live"]
    clock.now = NOW + timedelta(hours=31)
    svc.run(ETH)
    assert svc.feed(limit=10, offset=0)["freshness"]["stale"] is False


def test_a_run_that_crashes_is_closed_as_failed_not_left_running(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = make(engine)

    def boom(_now: object) -> int:
        raise RuntimeError("disk gone")

    monkeypatch.setattr(svc, "_expire", boom)
    with pytest.raises(RuntimeError):
        svc.run(ETH)
    [run] = svc.runs(1)
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    assert svc.feed(limit=10, offset=0)["freshness"]["stale"] is True  # a failed run is not fresh
