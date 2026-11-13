"""Evidence-backed relationships: what is built, what is refused, how reorgs and caps behave."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from bip.config import load_settings
from bip.db.engine import create_db_engine
from bip.db.errors import InvalidRecordError
from bip.db.observations import ObservationStore
from bip.db.repository import Repository
from bip.entities.relationships import MIN_REPEATED_TRANSFERS, RelationshipService
from tests.discovery.seed import E18, ETH, NOW, SOL, Seed, eth_addr, eth_tx


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


def make(engine: Engine, **cfg: Any) -> RelationshipService:
    settings = load_settings(data_root=Path("/unused"), env="test", discovery=cfg)
    return RelationshipService(Repository(engine), settings, clock=lambda: NOW)


def rel(svc: RelationshipService, ref: str, **kw: Any) -> list[dict[str, Any]]:
    return svc.for_entity(ETH, ref, limit=200, **kw)["items"]


def fund(seed: Seed, tx: int, source: int, target: int, hours: float, amount: int = 2) -> None:
    when = NOW - timedelta(hours=hours)
    seed.tx(eth_tx(tx), eth_addr(source), when, to=eth_addr(target))
    seed.transfer(eth_tx(tx), eth_addr(source), eth_addr(target), amount * E18, when)


# ------------------------------------------------------------------ deployed_by


def test_deployed_by_links_a_contract_to_the_sender_of_its_creation(engine: Engine) -> None:
    seed = Seed(engine)
    seed.tx(eth_tx(1), eth_addr(0xD1), NOW - timedelta(hours=5), created=eth_addr(0xC1))
    seed.tx(
        eth_tx(2),
        eth_addr(0xD2),
        NOW - timedelta(hours=4),
        created=eth_addr(0xC2),
        parse_status="partial",
    )
    svc = make(engine)
    svc.rebuild(ETH)
    one = rel(svc, eth_addr(0xC1))[0]
    assert one["kind"] == "deployed_by"
    assert one["source"] == {"kind": "contract", "ref": eth_addr(0xC1)}
    assert one["target"] == {"kind": "wallet", "ref": eth_addr(0xD1)}
    assert one["basis"] == "observed"
    assert one["confidence"] == 0.9
    assert one["evidence"] == [{"kind": "transaction", "chain": ETH, "ref": eth_tx(1)}]
    assert "not who controls" in one["explanation"]["limitations"]
    assert rel(svc, eth_addr(0xC2))[0]["confidence"] == 0.6  # incomplete receipt: lower
    assert [r["kind"] for r in rel(svc, eth_addr(0xD1), direction="in")] == ["deployed_by"]


# ---------------------------------------------------------------- funded_by and siblings


def test_funded_by_uses_the_first_stored_inbound_transfer(engine: Engine) -> None:
    seed = Seed(engine)
    fund(seed, 10, 0xA1, 0xB1, hours=30)  # first funding
    fund(seed, 11, 0xA2, 0xB1, hours=10)  # a later transfer from someone else
    svc = make(engine)
    svc.rebuild(ETH)
    funded = rel(svc, eth_addr(0xB1), kind="funded_by", direction="out")
    assert len(funded) == 1
    assert funded[0]["target"]["ref"] == eth_addr(0xA1)
    assert funded[0]["confidence"] == 0.5
    assert "first inbound native transfer" in funded[0]["explanation"]["note"]
    # the collector keeps only native transfers at or above its threshold, so an earlier, smaller
    # funder is invisible: the funder link is an interpretation of censored data, not an observation
    assert funded[0]["basis"] == "hypothesis"
    assert "10 ETH" in funded[0]["explanation"]["note"]
    assert "smaller" in funded[0]["explanation"]["note"]
    assert "does not show common ownership" in funded[0]["explanation"]["limitations"]


def test_shared_funder_is_a_labelled_hypothesis_not_a_stored_link(engine: Engine) -> None:
    seed = Seed(engine)
    for i, w in enumerate((0xB1, 0xB2, 0xB3)):
        fund(seed, 20 + i, 0xA1, w, hours=10 - i)
    svc = make(engine)
    svc.rebuild(ETH)
    derived = svc.for_entity(ETH, eth_addr(0xB1))["derived"]
    assert derived["basis"] == "hypothesis"
    assert derived["confidence"] <= 0.2
    assert set(derived["shares_first_funder_with"]) == {eth_addr(0xB2), eth_addr(0xB3)}
    assert "not evidence of common ownership" in derived["note"]
    with engine.connect() as conn:  # no wallet-to-wallet "same owner" row exists
        kinds = set(conn.execute(text("SELECT DISTINCT kind FROM entity_relationships")).scalars())
        bases = set(conn.execute(text("SELECT DISTINCT basis FROM entity_relationships")).scalars())
    assert kinds <= {"funded_by", "deployed_by", "transferred_to", "interacted_with"}
    assert bases == {"hypothesis"}  # funder links are hypotheses; nothing automatic is 'verified'


def test_the_schema_refuses_a_verified_basis(engine: Engine) -> None:
    with pytest.raises(Exception, match=r"relationship_basis|CHECK"), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO entity_relationships (chain, kind, source_kind, source_ref,"
                " target_kind,"
                " target_ref, basis, confidence, weight, status, first_seen_at, last_seen_at)"
                " VALUES ('ethereum','funded_by','wallet','a','wallet','b','verified',1,1,'active',"
                "'2026-01-01','2026-01-01')"
            )
        )


# ---------------------------------------------------------- transferred_to, interacted_with


def test_transferred_to_needs_repeated_transfers(engine: Engine) -> None:
    seed = Seed(engine)
    for i in range(MIN_REPEATED_TRANSFERS):
        fund(seed, 30 + i, 0xA1, 0xB1, hours=20 - i)
    fund(seed, 40, 0xA1, 0xB2, hours=5)  # only once
    svc = make(engine)
    svc.rebuild(ETH)
    sent = rel(svc, eth_addr(0xA1), kind="transferred_to", direction="out")
    assert [(r["target"]["ref"], r["weight"]) for r in sent] == [(eth_addr(0xB1), 3)]
    assert len(sent[0]["evidence"]) == 3


def test_solana_program_use_skips_well_known_programs(engine: Engine) -> None:
    seed = Seed(engine)
    wallet, program = "W1" * 16, "P1" * 16
    system = "11111111111111111111111111111111"
    for i in range(4):
        seed.tx(
            f"sig{i}" * 4,
            wallet,
            NOW - timedelta(hours=i + 1),
            chain=SOL,
            programs=[system, program],
        )
    svc = make(engine)
    svc.rebuild(SOL)
    found = svc.for_entity(SOL, wallet, kind="interacted_with")["items"]
    assert [(r["target"], r["weight"]) for r in found] == [({"kind": "program", "ref": program}, 4)]


# ------------------------------------------------------------------ idempotence, caps, reorgs


def test_rebuild_is_idempotent(engine: Engine) -> None:
    seed = Seed(engine)
    fund(seed, 10, 0xA1, 0xB1, hours=3)
    seed.tx(eth_tx(1), eth_addr(0xD1), NOW - timedelta(hours=2), created=eth_addr(0xC1))
    svc = make(engine)
    first = svc.rebuild(ETH)["chains"][ETH]
    second = svc.rebuild(ETH)["chains"][ETH]
    assert first["funded_by"]["created"] == 1
    assert second["funded_by"]["created"] == 0
    assert second["funded_by"]["updated"] == 1
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM entity_relationships")).scalar_one() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM relationship_evidence")).scalar_one() == 2


def test_orphaned_data_never_builds_relationships(engine: Engine) -> None:
    seed = Seed(engine)
    seed.tx(
        eth_tx(1),
        eth_addr(0xD1),
        NOW - timedelta(hours=2),
        created=eth_addr(0xC1),
        finality="orphaned",
    )
    when = NOW - timedelta(hours=3)
    seed.tx(eth_tx(2), eth_addr(0xA1), when, to=eth_addr(0xB1), finality="orphaned")
    seed.transfer(eth_tx(2), eth_addr(0xA1), eth_addr(0xB1), E18, when)
    svc = make(engine)
    svc.rebuild(ETH)
    assert svc.counts() == {}


def test_a_relationship_no_longer_supported_is_invalidated_after_a_complete_scan(
    engine: Engine,
) -> None:
    seed = Seed(engine)
    seed.tx(eth_tx(1), eth_addr(0xD1), NOW - timedelta(hours=2), created=eth_addr(0xC1))
    svc = make(engine)
    svc.rebuild(ETH)
    assert svc.counts()["deployed_by"] == {"active": 1}
    with engine.begin() as conn:
        conn.execute(text("UPDATE transactions SET finality = 'orphaned'"))
    summary = svc.rebuild(ETH)["chains"][ETH]["deployed_by"]
    assert summary["invalidated"] == 1
    assert svc.counts()["deployed_by"] == {"invalidated": 1}
    assert rel(svc, eth_addr(0xC1)) == []  # hidden by default
    assert len(rel(svc, eth_addr(0xC1), status="any")) == 1


def test_a_capped_scan_invalidates_nothing(engine: Engine) -> None:
    seed = Seed(engine)
    for i in range(120):  # 120 distinct recipients, oldest first
        fund(seed, 1000 + i, 0x100 + i, 0x500 + i, hours=300 - i)
    svc = make(engine)
    assert svc.rebuild(ETH)["chains"][ETH]["funded_by"]["created"] == 120
    capped = make(engine, max_rows_scanned=100)  # reads only the newest 100 rows
    summary = capped.rebuild(ETH)["chains"][ETH]["funded_by"]
    assert summary["row_cap_reached"] is True
    assert summary["found"] == 100
    assert summary["invalidated"] == 0  # the 20 unread relationships are not declared gone
    assert capped.counts()["funded_by"] == {"active": 120}


def test_a_capped_rebuild_reads_the_newest_rows_so_new_data_is_never_starved(
    engine: Engine,
) -> None:
    seed = Seed(engine)
    for i in range(120):  # oldest first; the store has more rows than the cap
        fund(seed, 1000 + i, 0x100 + i, 0x500 + i, hours=300 - i)
    capped = make(engine, max_rows_scanned=100)
    capped.rebuild(ETH)
    newest = rel(capped, eth_addr(0x500 + 119), kind="funded_by", direction="out")
    assert [r["target"]["ref"] for r in newest] == [eth_addr(0x100 + 119)]
    assert rel(capped, eth_addr(0x500), kind="funded_by", direction="out") == []  # outside window
    fund(seed, 2000, 0x9F0, 0x9F1, hours=1)  # data arriving later is picked up on the next run
    capped.rebuild(ETH)
    assert len(rel(capped, eth_addr(0x9F1), kind="funded_by", direction="out")) == 1


def test_a_capped_rebuild_never_makes_an_existing_relationship_worse(engine: Engine) -> None:
    seed = Seed(engine)
    for i in range(5):  # an old, repeated pair
        fund(seed, 10 + i, 0xA1, 0xB1, hours=300 - i)
    full = make(engine)
    full.rebuild(ETH)
    [before] = rel(full, eth_addr(0xA1), kind="transferred_to", direction="out")
    assert before["weight"] == 5
    for i in range(150):  # newer, unrelated rows push the old pair out of the capped window
        fund(seed, 100 + i, 0x200 + i, 0x400 + i, hours=200 - i)
    for i in range(3):  # and the pair repeats recently, inside the window
        fund(seed, 300 + i, 0xA1, 0xB1, hours=3 - i)
    capped = make(engine, max_rows_scanned=100)
    summary = capped.rebuild(ETH)["chains"][ETH]["transferred_to"]
    assert summary["row_cap_reached"] is True
    [after] = rel(capped, eth_addr(0xA1), kind="transferred_to", direction="out")
    assert after["weight"] == 5  # a partial window is a lower bound, never a reduction
    assert after["first_seen_at"] == before["first_seen_at"]
    assert after["last_seen_at"] > before["last_seen_at"]
    assert len(after["evidence"]) == 8  # earlier evidence kept, new evidence appended
    capped.rebuild(ETH)
    [again] = rel(capped, eth_addr(0xA1), kind="transferred_to", direction="out")
    assert len(again["evidence"]) == 8  # stable on repeat
    assert again["weight"] == 5


def test_a_capped_rebuild_does_not_invent_a_funder_when_the_first_one_is_out_of_window(
    engine: Engine,
) -> None:
    seed = Seed(engine)
    fund(seed, 10, 0xA1, 0xB1, hours=300)  # the real first stored funding of B1
    for i in range(150):
        fund(seed, 100 + i, 0x200 + i, 0x400 + i, hours=200 - i)
    fund(seed, 20, 0xA2, 0xB1, hours=1)  # a later transfer, inside the capped window
    capped = make(engine, max_rows_scanned=100)
    capped.rebuild(ETH)
    assert rel(capped, eth_addr(0xB1), kind="funded_by", direction="out") == []
    make(engine).rebuild(ETH)  # a complete scan still finds the real first funder
    funders = rel(capped, eth_addr(0xB1), kind="funded_by", direction="out")
    assert [r["target"]["ref"] for r in funders] == [eth_addr(0xA1)]


def test_orphaning_a_block_invalidates_relationships_resting_on_it(engine: Engine) -> None:
    seed = Seed(engine)
    seed.tx(
        eth_tx(1),
        eth_addr(0xD1),
        NOW - timedelta(hours=2),
        created=eth_addr(0xC1),
        block_hash="0x" + "aa" * 32,
    )
    svc = make(engine)
    svc.rebuild(ETH)
    with engine.connect() as conn:
        number = conn.execute(text("SELECT block_number FROM transactions")).scalar_one()
    effects = ObservationStore(Repository(engine)).mark_ethereum_block_orphaned(
        int(number), "0x" + "aa" * 32, "reorg"
    )
    assert effects.relationships_invalidated == 1
    assert svc.counts()["deployed_by"] == {"invalidated": 1}


def test_query_validation(engine: Engine) -> None:
    svc = make(engine)
    with pytest.raises(InvalidRecordError):
        svc.for_entity(ETH, "x", kind="same_owner")
    with pytest.raises(InvalidRecordError):
        svc.for_entity(ETH, "x", direction="sideways")
    empty = svc.for_entity(ETH, eth_addr(1))
    assert empty["total"] == 0
    assert empty["derived"] is None
    assert "not people or organisations" in empty["scope"]
