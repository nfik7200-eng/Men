"""Intelligence memory, candidate review and bounded expansion (Stage 3). SYNTHETIC rows only."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from bip.config.settings import Settings
from bip.db import schema
from bip.db.errors import InvalidRecordError
from bip.db.observations import ObservationStore
from bip.db.repository import Repository
from bip.discovery.review import ReviewService
from bip.intel.memory import MemoryService

API = "/api/v1"
CHAIN = "ethereum"
T0 = datetime(2026, 11, 1, tzinfo=UTC)


def addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def txid(n: int) -> str:
    return "0x" + f"{n:064x}"


def _pk(result: Any) -> int:
    return int(result.inserted_primary_key[0])


class Seed:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.n = 0

    def _next(self) -> int:
        self.n += 1
        return self.n

    def deploy(self, deployer: str, contract: str, at: datetime = T0) -> str:
        tx = txid(1000 + self._next())
        with self.repo.transaction() as conn:
            conn.execute(
                insert(schema.transactions).values(
                    chain=CHAIN,
                    tx_id=tx,
                    block_number=100 + self.n,
                    block_time=at,
                    from_address=deployer,
                    to_address=None,
                    status="success",
                    provenance_json="{}",
                    stored_at=at,
                    finality="finalized",
                    created_contract=contract,
                )
            )
        return tx

    def transfer(self, frm: str, to: str, at: datetime = T0, token: str | None = None) -> str:
        tx = txid(5000 + self._next())
        with self.repo.transaction() as conn:
            conn.execute(
                insert(schema.transfers).values(
                    chain=CHAIN,
                    tx_id=tx,
                    event_key="native" if token is None else "log:0",
                    block_number=200 + self.n,
                    standard="native" if token is None else "erc20",
                    token=token,
                    from_address=frm,
                    to_address=to,
                    amount_raw="1",
                    classification="unclassified",
                    provenance_json="{}",
                    stored_at=at,
                    block_time=at,
                )
            )
        return tx

    def relationship(self, kind: str, source: str, target: str, txs: list[str]) -> None:
        with self.repo.transaction() as conn:
            rid = _pk(
                conn.execute(
                    insert(schema.entity_relationships).values(
                        chain=CHAIN,
                        kind=kind,
                        source_kind="contract" if kind == "deployed_by" else "wallet",
                        source_ref=source,
                        target_kind="wallet",
                        target_ref=target,
                        basis="observed",
                        confidence=0.8,
                        weight=1,
                        status="active",
                        first_seen_at=T0,
                        last_seen_at=T0,
                    )
                )
            )
            for tx in txs:
                conn.execute(
                    insert(schema.relationship_evidence).values(
                        relationship_id=rid, chain=CHAIN, tx_id=tx
                    )
                )

    def label(self, ref: str, category: str, name: str, status: str = "approved") -> int:
        with self.repo.transaction() as conn:
            return _pk(
                conn.execute(
                    insert(schema.entity_labels).values(
                        chain=CHAIN,
                        ref=ref,
                        entity_kind="wallet",
                        category=category,
                        name=name,
                        status=status,
                        source="analyst",
                        proposed_by="ana",
                        evidence_json="[]",
                        reviewed_by="rev",
                        created_at=T0,
                        reviewed_at=T0,
                    )
                )
            )

    def signal(self, ref: str, severity: str = "high", detector: str = "large_transfer_mad") -> int:
        with self.repo.transaction() as conn:
            return _pk(
                conn.execute(
                    insert(schema.signals).values(
                        signal_key=f"k:{self._next()}",
                        detector_id=detector,
                        detector_version="1",
                        family="large_transfers",
                        chain=CHAIN,
                        subject_kind="wallet",
                        subject_ref=ref,
                        title="SYNTHETIC signal",
                        severity=severity,
                        confidence=0.5,
                        score=70.0,
                        status="open",
                        explanation_json="{}",
                        evidence_json="[]",
                        coverage_json="{}",
                        observed_at=T0,
                        first_seen_at=T0,
                        last_seen_at=T0,
                        occurrences=1,
                    )
                )
            )


@pytest.fixture
def seed(repo: Repository) -> Seed:
    return Seed(repo)


@pytest.fixture
def memory(repo: Repository, settings: Settings) -> MemoryService:
    return MemoryService(repo, settings.memory)


def facts(repo: Repository, ref: str, key: str | None = None) -> list[dict[str, Any]]:
    e, f = schema.entities, schema.entity_facts
    with repo.transaction() as conn:
        q = select(f).join(e, e.c.id == f.c.entity_id).where(e.c.ref == ref).order_by(f.c.id)
        if key:
            q = q.where(f.c.fact_key == key)
        return [dict(r) for r in conn.execute(q).mappings()]


# --------------------------------------------------------------------------------- memory


def test_entities_and_facts_are_created_with_provenance_and_refresh_is_idempotent(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    dep, con = addr(1), addr(2)
    tx = seed.deploy(dep, con)
    first = memory.refresh(CHAIN)
    assert first["facts_new"] >= 2
    profile = memory.entity(CHAIN, con)
    assert profile is not None
    assert profile["kind"] == "contract"
    fact = next(f for f in profile["current_facts"] if f["key"] == "deployed_by")
    assert fact["value"] == {"deployer": dep}
    assert (fact["evidence_class"], fact["source"], fact["source_tx"]) == (
        "observed",
        "transactions",
        tx,
    )
    deployer = memory.entity(CHAIN, dep)
    assert deployer is not None
    role = next(f for f in deployer["current_facts"] if f["key"] == "role:deployer")
    assert role["value"]["contracts_deployed"] == 1
    count = len(facts(repo, con)) + len(facts(repo, dep))
    again = memory.refresh(CHAIN)
    assert (again["facts_new"], again["facts_changed"]) == (0, 0)
    assert len(facts(repo, con)) + len(facts(repo, dep)) == count  # nothing duplicated


def test_identity_is_never_merged_and_kind_upgrades_are_recorded(
    seed: Seed, memory: MemoryService
) -> None:
    a, b = addr(10), addr(11)
    seed.transfer(a, b)
    seed.transfer(addr(12), b)
    memory.refresh(CHAIN)
    assert memory.entity(CHAIN, b)["kind"] == "wallet"  # type: ignore[index]
    seed.deploy(addr(13), b, at=T0 + timedelta(hours=1))  # b turns out to be a contract
    memory.refresh(CHAIN)
    prof = memory.entity(CHAIN, b)
    assert prof is not None
    assert prof["kind"] == "contract"
    kind = next(f for f in prof["history"] if f["key"] == "kind")
    assert kind["value"] == {"from": "wallet", "to": "contract"}
    assert memory.entity(CHAIN, a)["ref"] == a  # type: ignore[index]  # still its own entity


def test_a_behaviour_change_appends_history_instead_of_overwriting(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    w = addr(20)
    seed.transfer(w, addr(21))
    memory.refresh(CHAIN)
    for k in range(3):
        seed.transfer(w, addr(30 + k))
    memory.refresh(CHAIN)
    rows = facts(repo, w, "activity")
    assert [(r["change"], r["status"]) for r in rows] == [
        ("new", "superseded"),
        ("changed", "current"),
    ]
    assert rows[1]["supersedes_id"] == rows[0]["id"]
    assert json.loads(rows[1]["value_json"])["stored_transfers_out"] == 4


def test_conflicting_reviewed_labels_stay_visible_as_a_contradiction(
    seed: Seed, memory: MemoryService
) -> None:
    w = addr(40)
    seed.label(w, "exchange", "SYNTHETIC Exchange")
    seed.label(w, "bridge", "SYNTHETIC Bridge")
    out = memory.refresh(CHAIN)
    assert out["contradictions"] == 1
    prof = memory.entity(CHAIN, w)
    assert prof is not None
    assert [c["value"]["categories"] for c in prof["contradictions"]] == [["bridge", "exchange"]]
    assert len([f for f in prof["current_facts"] if f["key"].startswith("label:")]) == 2


def test_a_label_that_is_no_longer_approved_becomes_outdated(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    w = addr(41)
    lid = seed.label(w, "exchange", "SYNTHETIC Exchange")
    memory.refresh(CHAIN)
    with repo.transaction() as conn:
        conn.execute(text("UPDATE entity_labels SET status='rejected' WHERE id=:i"), {"i": lid})
    memory.refresh(CHAIN)
    assert [r["status"] for r in facts(repo, w, f"label:{lid}")] == ["outdated"]


def test_a_reorg_invalidates_facts_that_rest_on_the_orphaned_transaction(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    con = addr(51)
    tx = seed.deploy(addr(50), con)
    memory.refresh(CHAIN)
    with repo.transaction() as conn:
        ObservationStore._invalidate_derived(conn, CHAIN, [tx], "SYNTHETIC reorg")  # pyright: ignore[reportPrivateUsage]
    rows = facts(repo, con, "deployed_by")
    assert [r["status"] for r in rows] == ["invalidated"]  # kept, not deleted


def test_a_reorg_withdraws_funder_facts_and_expansion_candidates_built_on_the_link(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    w, funder = addr(52), addr(53)
    tx = seed.transfer(funder, w)
    seed.relationship("funded_by", w, funder, [tx])
    memory.refresh(CHAIN)
    expansion = seed.signal(addr(54), detector="expansion_shared_neighbourhood")
    unrelated = seed.signal(addr(55), detector="large_transfer_mad")
    with repo.transaction() as conn:
        ObservationStore._invalidate_derived(conn, CHAIN, [tx], "SYNTHETIC reorg")  # pyright: ignore[reportPrivateUsage]
        status = {
            int(i): str(st) for i, st in conn.execute(text("SELECT id, status FROM signals")).all()
        }
    # the funder fact stops being current at once, not only when the rotating scan revisits it
    assert [r["status"] for r in facts(repo, w, f"funded_by:{funder}")] == ["invalidated"]
    # a multi-hop expansion candidate may rest on the withdrawn link through any hop
    assert status[expansion] == "invalidated"
    assert status[unrelated] == "open"  # only candidates that walk relationships are withdrawn


def test_activity_counts_never_freeze_between_levels(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    w, peer = addr(56), addr(57)
    seed.transfer(w, peer)
    seed.transfer(w, peer)
    memory.refresh(CHAIN)
    seed.transfer(w, peer)  # 2 -> 3 transfers: the same activity level, a different count
    memory.refresh(CHAIN)
    history = facts(repo, w, "activity")
    current = [r for r in history if r["status"] == "current"]
    assert len(current) == 1
    assert json.loads(current[0]["value_json"])["stored_transfers_out"] == 3  # not frozen at 2
    assert [r["status"] for r in history] == ["superseded", "current"]  # the old count is kept
    memory.refresh(CHAIN)
    assert len(facts(repo, w, "activity")) == 2  # an unchanged count writes nothing new


def test_signals_become_facts_with_their_evidence_class(seed: Seed, memory: MemoryService) -> None:
    w = addr(60)
    seed.signal(w, detector="large_transfer_mad")
    seed.signal(w, detector="common_funding_source")
    memory.refresh(CHAIN)
    prof = memory.entity(CHAIN, w)
    assert prof is not None
    classes = sorted(
        f["evidence_class"] for f in prof["current_facts"] if f["key"].startswith("signal:")
    )
    assert classes == ["heuristic", "statistical"]


def test_refresh_is_bounded_and_resumes_from_its_cursor(
    seed: Seed, repo: Repository, settings: Settings
) -> None:
    small = MemoryService(repo, settings.memory.model_copy(update={"max_rows_per_source": 10}))
    for k in range(25):
        seed.transfer(addr(100 + k), addr(200 + k))
    first = small.refresh(CHAIN)
    assert first["rows_read"]["transfers"] == 10
    assert "transfers" in first["truncated_sources"]
    second = small.refresh(CHAIN)
    third = small.refresh(CHAIN)
    assert second["rows_read"]["transfers"] == 10
    assert third["rows_read"]["transfers"] == 5
    assert small.refresh(CHAIN)["rows_read"]["transfers"] == 0


# --------------------------------------------------------------------------------- review


def test_review_needs_a_reason_keeps_history_and_never_repeats_a_state(
    seed: Seed, repo: Repository
) -> None:
    sid = seed.signal(addr(70))
    review = ReviewService(repo)
    assert review.state(sid) == "new"
    with pytest.raises(InvalidRecordError, match="note is required"):
        review.decide(sid, "rejected", "other", None, "ana")
    with pytest.raises(InvalidRecordError, match="reason_code"):
        review.decide(sid, "accepted", "because", None, "ana")
    review.decide(sid, "pending_review", "needs_more_data", None, "ana")
    out = review.decide(sid, "accepted", "evidence_sufficient", "SYNTHETIC note", "bea")
    assert out["review_state"] == "confirmed"  # 'accepted' is the pre-v19 alias
    assert [(h["from_state"], h["to_state"], h["actor"]) for h in out["history"]] == [
        ("new", "pending_review", "ana"),
        ("pending_review", "confirmed", "bea"),
    ]
    with pytest.raises(InvalidRecordError, match="already confirmed"):
        review.decide(sid, "accepted", "evidence_sufficient", None, "ana")


def test_review_api_filters_and_dismiss_promote_record_decisions(
    seed: Seed, client: TestClient, repo: Repository
) -> None:
    a, b, c = seed.signal(addr(80)), seed.signal(addr(81)), seed.signal(addr(82))
    r = client.post(
        f"{API}/signals/{a}/review",
        json={"state": "accepted", "reason_code": "evidence_sufficient"},
    )
    assert r.status_code == 200, r.text
    assert client.post(f"{API}/signals/{b}/dismiss", json={"note": "noise"}).status_code == 200
    wl = client.post(f"{API}/watchlists", json={"name": "SYNTHETIC watch"}).json()
    promo = client.post(
        f"{API}/signals/{c}/promote", json={"target": "watchlist", "watchlist_id": wl["id"]}
    )
    assert promo.status_code == 200, promo.text
    states = {i["id"]: i["review_state"] for i in client.get(f"{API}/signals").json()["items"]}
    assert (states[a], states[b], states[c]) == ("confirmed", "rejected", "monitoring")
    accepted = client.get(f"{API}/signals", params={"review_state": "accepted"}).json()
    assert [i["id"] for i in accepted["items"]] == [a]
    detail = client.get(f"{API}/signals/{b}").json()
    assert detail["review_state"] == "rejected"
    assert detail["decisions"][0]["reason_code"] == "dismissed"
    assert client.get(f"{API}/signals/{a}/decisions").json()["items"][0]["actor"] == "local"
    bad = client.post(
        f"{API}/signals/{a}/review", json={"state": "rejected", "reason_code": "other"}
    )
    assert bad.status_code == 422


# ------------------------------------------------------------------------------ expansion


def build_deployer_web(seed: Seed) -> tuple[str, str, str]:
    """Two deployers funded by the same wallet: the funder is linked to both seeds."""
    d1, d2, funder = addr(301), addr(302), addr(303)
    seed.relationship("deployed_by", addr(311), d1, [seed.deploy(d1, addr(311))])
    seed.relationship("deployed_by", addr(312), d2, [seed.deploy(d2, addr(312))])
    seed.relationship("funded_by", d1, funder, [seed.transfer(funder, d1)])
    seed.relationship("funded_by", d2, funder, [seed.transfer(funder, d2)])
    return d1, d2, funder


def test_expansion_finds_a_shared_upstream_and_explains_it(seed: Seed, client: TestClient) -> None:
    d1, d2, funder = build_deployer_web(seed)
    r = client.post(f"{API}/discovery/expand/ethereum", json={"policy": "deployer_network"})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["policy"]["id"] == "deployer_network"
    assert run["seeds_used"] == 2
    assert run["source"] == "local database only"
    sigs = client.get(f"{API}/signals", params={"detector": "expansion_deployer_network"}).json()
    subjects = {i["subject"]["ref"]: i for i in sigs["items"]}
    assert funder in subjects
    detail = client.get(f"{API}/signals/{subjects[funder]['id']}").json()
    exp = detail["explanation"]
    assert exp["evidence_class"] == "hypothesis"
    assert sorted(exp["seeds_linked"]) == sorted([d1, d2])
    assert "not proof" in exp["interpretation"]
    assert detail["confidence"] <= 0.4


def test_expansion_respects_depth_cooldown_and_entity_budget(
    seed: Seed, repo: Repository, settings: Settings, client: TestClient
) -> None:
    from bip.discovery.expansion import ExpansionEngine
    from bip.discovery.service import DiscoveryService

    build_deployer_web(seed)
    first = client.post(
        f"{API}/discovery/expand/ethereum", json={"policy": "deployer_network"}
    ).json()
    again = client.post(
        f"{API}/discovery/expand/ethereum", json={"policy": "deployer_network"}
    ).json()
    # seeds are expanded every run (so seeds can meet within a run); the shared funder, whose
    # edges were read in the first run, is not read again but is still reached and kept
    assert again["seeds_used"] == first["seeds_used"] == 2
    assert again["not_expanded_cooldown"] >= 1
    assert again["relationship_rows_read"] < first["relationship_rows_read"]
    sigs = client.get(f"{API}/signals", params={"detector": "expansion_deployer_network"}).json()
    assert sigs["total"] == 1  # the same candidate, merged, not a duplicate
    with repo.transaction() as conn:
        conn.execute(text("DELETE FROM discovery_examined"))
    tight = settings.model_copy(
        update={
            "memory": settings.memory.model_copy(
                update={"expansion_max_entities": 1, "expansion_max_depth": 1}
            )
        }
    )
    engine = ExpansionEngine(repo, tight, DiscoveryService(repo, tight), ReviewService(repo))
    out = engine.run("deployer_network", CHAIN)
    assert "entities" in out["truncated"]
    assert out["entities_examined"] <= 1 + out["seeds_used"]


def test_unknown_policy_and_chain_are_refused(client: TestClient) -> None:
    assert (
        client.post(f"{API}/discovery/expand/ethereum", json={"policy": "everything"}).status_code
        == 422
    )
    assert (
        client.post(
            f"{API}/discovery/expand/dogecoin", json={"policy": "deployer_network"}
        ).status_code
        == 422
    )


def test_policies_and_budgets_are_listed(client: TestClient) -> None:
    body = client.get(f"{API}/discovery/expansion").json()
    assert {p["id"] for p in body["policies"]} == {"shared_neighbourhood", "deployer_network"}
    assert body["budgets"]["max_depth"] == 2
    assert body["scheduled"] is False


# ------------------------------------------------------------------------------------ API


def test_memory_api_profile_search_and_refresh(seed: Seed, client: TestClient) -> None:
    dep, con = addr(401), addr(402)
    seed.deploy(dep, con)
    seed.transfer(dep, addr(403))
    missing = client.get(f"{API}/entities/memory/ethereum/{con}").json()
    assert missing["in_memory"] is False
    r = client.post(f"{API}/entities/memory/refresh/ethereum")
    assert r.status_code == 201
    body = client.get(f"{API}/entities/memory/ethereum/{dep}").json()
    assert body["in_memory"] is True
    assert body["entity"]["kind"] == "wallet"
    assert [c["ref"] for c in body["counterparties"]] == [addr(403)]
    assert "not proof" in body["note"]
    seed.signal(dep)
    with_candidate = client.get(f"{API}/entities/memory/ethereum/{dep}").json()
    assert with_candidate["candidates"][0]["review_state"] == "new"
    assert with_candidate["candidates"][0]["detector_id"] == "large_transfer_mad"
    found = client.get(
        f"{API}/entities/memory/search", params={"chain": "ethereum", "kind": "contract"}
    ).json()
    assert [i["ref"] for i in found["items"]] == [con]


def test_viewers_cannot_refresh_memory_review_or_expand(tmp_path: Any) -> None:
    from tests.security.helpers import PASSWORD, Rig

    rig = Rig(tmp_path, mode="required")
    rig.user("vic", role="viewer")
    try:
        with TestClient(rig.app) as c:
            r = c.post(f"{API}/auth/login", json={"username": "vic", "password": PASSWORD})
            h = {"X-CSRF-Token": r.json()["csrf_token"]}
            for path, body in (
                ("/entities/memory/refresh/ethereum", None),
                ("/discovery/expand/ethereum", {"policy": "deployer_network"}),
                ("/signals/1/review", {"state": "accepted", "reason_code": "duplicate"}),
            ):
                assert c.post(f"{API}{path}", json=body, headers=h).status_code == 403, path
            assert c.get(f"{API}/entities/memory/search").status_code == 200
    finally:
        rig.dispose()


# --------------------------------------------------------------------- repair-round tests


def test_several_active_funders_are_separate_stable_facts(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    w, f1, f2 = addr(501), addr(502), addr(503)
    seed.relationship("funded_by", w, f1, [seed.transfer(f1, w)])
    seed.relationship("funded_by", w, f2, [seed.transfer(f2, w)])
    memory.refresh(CHAIN)
    memory.refresh(CHAIN)
    memory.refresh(CHAIN)
    keys = sorted(r["fact_key"] for r in facts(repo, w) if r["fact_key"].startswith("funded_by"))
    assert keys == [f"funded_by:{f1}", f"funded_by:{f2}"]  # no flip-flopping, no growth
    with repo.transaction() as conn:
        conn.execute(
            text("UPDATE entity_relationships SET status='invalidated' WHERE target_ref=:t"),
            {"t": f2},
        )
    memory.refresh(CHAIN)
    status = {
        r["fact_key"]: r["status"] for r in facts(repo, w) if r["fact_key"].startswith("funded_by")
    }
    assert status == {f"funded_by:{f1}": "current", f"funded_by:{f2}": "outdated"}


def test_a_funder_link_reclassified_as_hypothesis_supersedes_the_observed_fact(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    w, f1 = addr(511), addr(512)
    seed.relationship("funded_by", w, f1, [seed.transfer(f1, w)])
    memory.refresh(CHAIN)
    [first] = facts(repo, w, f"funded_by:{f1}")
    assert first["evidence_class"] == "observed"
    with repo.transaction() as conn:  # a rebuild re-labels the link from censored data
        conn.execute(
            text("UPDATE entity_relationships SET basis='hypothesis' WHERE source_ref=:w"), {"w": w}
        )
    memory.refresh(CHAIN)
    history = facts(repo, w, f"funded_by:{f1}")
    assert [(r["evidence_class"], r["status"]) for r in history] == [
        ("observed", "superseded"),
        ("hypothesis", "current"),
    ]  # history kept: the old class is superseded, never rewritten
    assert history[1]["supersedes_id"] == history[0]["id"]
    memory.refresh(CHAIN)
    assert len(facts(repo, w, f"funded_by:{f1}")) == 2  # stable on repeat


def test_a_deployment_upgraded_in_place_is_still_folded_in(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    dep, con = addr(511), addr(512)
    tx = seed.deploy(dep, con)
    with repo.transaction() as conn:  # first stored as a partial row without its contract
        conn.execute(
            text("UPDATE transactions SET created_contract=NULL WHERE tx_id=:t"), {"t": tx}
        )
    seed.transfer(addr(513), addr(514))  # later rows move every id cursor past it
    memory.refresh(CHAIN)
    with repo.transaction() as conn:  # the row is upgraded in place and keeps its id
        conn.execute(
            text("UPDATE transactions SET created_contract=:c WHERE tx_id=:t"), {"c": con, "t": tx}
        )
    memory.refresh(CHAIN)
    assert memory.entity(CHAIN, con)["kind"] == "contract"  # type: ignore[index]


def test_a_reorg_reverts_a_kind_upgrade_and_queues_counts_for_recompute(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    b = addr(521)
    seed.transfer(addr(520), b)
    memory.refresh(CHAIN)
    tx = seed.deploy(addr(522), b, at=T0 + timedelta(hours=1))
    memory.refresh(CHAIN)
    assert memory.entity(CHAIN, b)["kind"] == "contract"  # type: ignore[index]
    with repo.transaction() as conn:
        ObservationStore._invalidate_derived(conn, CHAIN, [tx], "SYNTHETIC reorg")  # pyright: ignore[reportPrivateUsage]
    assert memory.entity(CHAIN, b)["kind"] == "wallet"  # type: ignore[index]
    pending = json.loads(repo.get_meta(f"memory:pending:{CHAIN}") or "{}")
    assert addr(522) in pending  # the deployer's counts will be recomputed


def test_a_changed_contradiction_supersedes_the_previous_one(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    w = addr(531)
    seed.label(w, "exchange", "SYNTHETIC A")
    seed.label(w, "bridge", "SYNTHETIC B")
    memory.refresh(CHAIN)
    seed.label(w, "treasury", "SYNTHETIC C")
    memory.refresh(CHAIN)
    rows = facts(repo, w, "label_conflict")
    assert [r["status"] for r in rows] == ["superseded", "current"]
    assert json.loads(rows[1]["value_json"])["categories"] == ["bridge", "exchange", "treasury"]


def test_the_same_value_from_another_source_is_a_confirmation(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    dep, con = addr(541), addr(542)
    seed.deploy(dep, con)
    memory.refresh(CHAIN)
    seed.deploy(dep, con, at=T0 + timedelta(hours=2))  # a second observation of the same fact
    out = memory.refresh(CHAIN)
    assert out["facts_confirmed"] >= 1
    rows = facts(repo, con, "deployed_by")
    assert len(rows) == 1
    assert rows[0]["confirmations"] == 2


def test_aggregates_beyond_the_cap_are_carried_to_the_next_run(
    seed: Seed, repo: Repository, settings: Settings
) -> None:
    small = MemoryService(repo, settings.memory.model_copy(update={"max_entities_per_run": 10}))
    for k in range(8):
        seed.transfer(addr(600 + k), addr(700 + k))  # 16 wallets touched
    first = small.refresh(CHAIN)
    assert first["aggregates_pending"] == 6
    second = small.refresh(CHAIN)
    assert second["aggregates_pending"] == 0
    assert all(facts(repo, addr(700 + k), "activity") for k in range(8))


def test_a_concurrent_refresh_is_refused(client: TestClient) -> None:
    from typing import cast

    svc = cast(MemoryService, client.app.state.bip.memory)  # type: ignore[attr-defined]
    lock = svc._lock  # pyright: ignore[reportPrivateUsage]
    assert lock.acquire(blocking=False)
    try:
        assert client.post(f"{API}/entities/memory/refresh/ethereum").status_code == 409
    finally:
        lock.release()


def test_search_is_an_exact_case_sensitive_prefix(seed: Seed, memory: MemoryService) -> None:
    seed.transfer(addr(800), addr(801))
    memory.refresh(CHAIN)
    assert [
        i["ref"] for i in memory.search(chain=CHAIN, kind=None, q=addr(800)[:-1] + "0", limit=10)
    ] == [addr(800)]  # ...320, not ...321
    assert memory.search(chain=CHAIN, kind=None, q="%%", limit=10) == []
    assert memory.search(chain=CHAIN, kind=None, q="0X" + addr(800)[2:], limit=10) == []


def test_a_seed_only_counts_within_the_depth_limit(
    seed: Seed, repo: Repository, settings: Settings
) -> None:
    """Seed A is adjacent to seed B. B's other neighbour N is 2 steps from A: with depth 1 it
    must not be reported as linked to two seeds."""
    from bip.discovery.expansion import ExpansionEngine
    from bip.discovery.service import DiscoveryService

    a, b, n = addr(901), addr(902), addr(903)
    seed.relationship("deployed_by", addr(911), a, [seed.deploy(a, addr(911))])
    seed.relationship("deployed_by", addr(912), b, [seed.deploy(b, addr(912))])
    seed.relationship("funded_by", b, a, [seed.transfer(a, b)])
    seed.relationship("funded_by", n, b, [seed.transfer(b, n)])
    one = settings.model_copy(
        update={"memory": settings.memory.model_copy(update={"expansion_max_depth": 1})}
    )
    engine = ExpansionEngine(repo, one, DiscoveryService(repo, one), ReviewService(repo))
    engine.run("deployer_network", CHAIN)
    with repo.transaction() as conn:
        subjects = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT subject_ref FROM signals WHERE detector_id='expansion_deployer_network'"
                )
            )
        }
    assert n not in subjects


def test_required_mode_records_the_real_reviewer_and_needs_csrf(tmp_path: Any) -> None:
    from tests.security.helpers import PASSWORD, Rig

    rig = Rig(tmp_path, mode="required")
    rig.user("ana", role="analyst")
    try:
        sid = Seed(rig.repo).signal(addr(950))
        with TestClient(rig.app) as c:
            r = c.post(f"{API}/auth/login", json={"username": "ana", "password": PASSWORD})
            h = {"X-CSRF-Token": r.json()["csrf_token"]}
            body = {"state": "accepted", "reason_code": "evidence_sufficient"}
            assert c.post(f"{API}/signals/{sid}/review", json=body).status_code == 403  # no CSRF
            ok = c.post(f"{API}/signals/{sid}/review", json=body, headers=h)
            assert ok.status_code == 200, ok.text
            assert ok.json()["history"][-1]["actor"] == "ana"
            assert c.post(f"{API}/entities/memory/refresh/ethereum", headers=h).status_code == 201
            assert (
                c.post(
                    f"{API}/discovery/expand/ethereum",
                    json={"policy": "shared_neighbourhood"},
                    headers=h,
                ).status_code
                == 201
            )
    finally:
        rig.dispose()


def test_a_contract_first_seen_in_its_own_deployment_is_withdrawn_by_a_reorg(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    con = addr(1001)
    tx = seed.deploy(addr(1000), con)
    memory.refresh(CHAIN)
    assert memory.entity(CHAIN, con)["kind"] == "contract"  # type: ignore[index]
    with repo.transaction() as conn:
        ObservationStore._invalidate_derived(conn, CHAIN, [tx], "SYNTHETIC reorg")  # pyright: ignore[reportPrivateUsage]
    assert memory.entity(CHAIN, con)["kind"] == "wallet"  # type: ignore[index]


def test_requeued_entities_get_aggregates_for_their_stored_kind(
    seed: Seed, memory: MemoryService, repo: Repository
) -> None:
    token = addr(1101)
    seed.transfer(addr(1102), addr(1103), token=token)
    memory.refresh(CHAIN)
    repo.set_meta(f"memory:pending:{CHAIN}", json.dumps({token: "wallet"}))  # queued as wallet
    memory.refresh(CHAIN)
    keys = {r["fact_key"] for r in facts(repo, token)}
    assert "token_activity" in keys
    assert "activity" not in keys


def test_unchanged_deployments_do_not_keep_the_aggregate_queue_full(
    seed: Seed, repo: Repository, settings: Settings
) -> None:
    small = MemoryService(repo, settings.memory.model_copy(update={"max_entities_per_run": 10}))
    for k in range(15):
        seed.deploy(addr(1200 + k), addr(1300 + k))
    small.refresh(CHAIN)
    small.refresh(CHAIN)
    assert small.refresh(CHAIN)["aggregates_pending"] == 0
    assert small.refresh(CHAIN)["aggregates_pending"] == 0  # re-reads queue nothing new


def test_seeds_meeting_late_are_still_passed_on_within_the_depth(
    seed: Seed, repo: Repository, settings: Settings
) -> None:
    """S1-X, S2-Y, X-Y and X-Z: at depth 3, Z is 2 steps from S1 and 3 from S2 (via Y and X)."""
    from bip.discovery.expansion import ExpansionEngine
    from bip.discovery.service import DiscoveryService

    s1, s2, x, y, z = (addr(1400 + k) for k in range(5))
    seed.relationship("deployed_by", addr(1411), s1, [seed.deploy(s1, addr(1411))])
    seed.relationship("deployed_by", addr(1412), s2, [seed.deploy(s2, addr(1412))])
    seed.relationship("funded_by", x, s1, [seed.transfer(s1, x)])
    seed.relationship("funded_by", y, s2, [seed.transfer(s2, y)])
    seed.relationship("funded_by", y, x, [seed.transfer(x, y)])
    seed.relationship("funded_by", z, x, [seed.transfer(x, z)])
    three = settings.model_copy(
        update={"memory": settings.memory.model_copy(update={"expansion_max_depth": 3})}
    )
    engine = ExpansionEngine(repo, three, DiscoveryService(repo, three), ReviewService(repo))
    engine.run("deployer_network", CHAIN)
    with repo.transaction() as conn:
        subjects = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT subject_ref FROM signals WHERE detector_id='expansion_deployer_network'"
                )
            )
        }
    assert {x, y, z} <= subjects
