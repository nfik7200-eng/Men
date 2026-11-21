"""Search, transaction/token/contract detail, notes and the case timeline."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from bip.config import load_settings
from bip.db.engine import create_db_engine
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.entities import views
from bip.entities.cases import CaseService
from bip.entities.relationships import RelationshipService
from tests.conftest import evm_provenance
from tests.discovery.seed import E18, ETH, NOW, SOL, Seed, eth_addr, eth_tx
from tests.stage2.fakes import key

TOKEN = eth_addr(0x7000)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


def repo(engine: Engine) -> Repository:
    return Repository(engine)


def token_transfer(
    seed: Seed, tx: int, sender: int, to: int, amount: int, hours: float, **kw: Any
) -> None:
    when = NOW - timedelta(hours=hours)
    seed.tx(eth_tx(tx), eth_addr(sender), when, to=eth_addr(to), **kw)
    seed.transfer(eth_tx(tx), eth_addr(sender), eth_addr(to), amount * E18, when, token=TOKEN)


# ------------------------------------------------------------------ search


def test_search_finds_each_kind_and_respects_the_chain_filter(engine: Engine) -> None:
    seed = Seed(engine)
    token_transfer(seed, 1, 0xA1, 0xA2, 5, 3)
    seed.tx(eth_tx(2), eth_addr(0xD1), NOW - timedelta(hours=2), created=eth_addr(0xC0DE))
    seed.tx("solsig" + "Z" * 20, "SolWallet" + "1" * 20, NOW, chain=SOL)
    r = repo(engine)
    r.observe_wallet(ETH, eth_addr(0xFACE), NOW, evm_provenance())

    def kinds(query: str, chain: str | None = None) -> set[str]:
        return {h["kind"] for h in views.search(r, query, chain, 20)["items"]}

    assert kinds(TOKEN) == {"token"}
    assert kinds(eth_addr(0xC0DE)) == {"contract"}
    assert kinds(eth_addr(0xFACE)) == {"wallet"}
    assert kinds(eth_tx(2)) == {"transaction"}
    assert kinds("solsig", SOL) == {"transaction"}
    assert kinds("solsig", ETH) == set()
    hit = views.search(r, eth_addr(0xC0DE), None, 20)["items"][0]
    assert hit == {"kind": "contract", "chain": ETH, "ref": eth_addr(0xC0DE)}


def test_search_needs_three_characters_and_treats_wildcards_literally(engine: Engine) -> None:
    r = repo(engine)
    with pytest.raises(InvalidRecordError, match="at least 3"):
        views.search(r, "ab", None, 10)
    Seed(engine).tx(eth_tx(1), eth_addr(1), NOW)
    assert views.search(r, "%%%", None, 10)["items"] == []
    assert views.search(r, "0x_", None, 10)["items"] == []
    assert "does not mean the entity does not exist" in views.search(r, "zzz", None, 10)["scope"]


# ------------------------------------------------------------------ transaction


def test_transaction_detail_joins_transfers_signals_and_relationships(engine: Engine) -> None:
    seed = Seed(engine)
    seed.tx(eth_tx(1), eth_addr(0xD1), NOW - timedelta(hours=2), created=eth_addr(0xC1))
    RelationshipService(repo(engine), load_settings(data_root=Path("/u"), env="test")).rebuild(ETH)
    detail = views.transaction_detail(repo(engine), ETH, eth_tx(1))
    assert detail["created_contract"] == eth_addr(0xC1)
    assert detail["finality"] == "finalized"
    assert detail["orphaned"] is False
    assert [r["kind"] for r in detail["relationships"]] == ["deployed_by"]
    assert detail["provenance"]["source_provider"] == "synthetic"
    assert "provenance_json" not in str(detail)


def test_orphaned_transaction_is_still_viewable_but_flagged(engine: Engine) -> None:
    Seed(engine).tx(eth_tx(1), eth_addr(1), NOW, finality="orphaned")
    detail = views.transaction_detail(repo(engine), ETH, eth_tx(1))
    assert (detail["orphaned"], detail["finality"]) == (True, "orphaned")


def test_unknown_transaction(engine: Engine) -> None:
    with pytest.raises(RecordNotFoundError, match="no stored ethereum transaction"):
        views.transaction_detail(repo(engine), ETH, eth_tx(99))


# ------------------------------------------------------------------ token and contract


def test_token_detail_counts_only_canonical_transfers_and_claims_no_holders(engine: Engine) -> None:
    seed = Seed(engine)
    token_transfer(seed, 1, 0xA1, 0xA2, 5, 10)
    token_transfer(seed, 2, 0xA1, 0xA3, 7, 9)
    token_transfer(seed, 3, 0xA4, 0xA2, 11, 8)
    token_transfer(seed, 4, 0xA9, 0xA9 + 1, 1000, 7, finality="orphaned")
    detail = views.token_detail(repo(engine), ETH, TOKEN)
    assert detail["transfers"] == 3
    assert detail["orphaned_transfers_excluded"] == 1
    assert detail["distinct_senders"] == 2
    assert detail["distinct_recipients"] == 2
    assert detail["volume_raw"] == str(23 * E18)
    assert (
        detail["top_counterparties"][0] == {"address": eth_addr(0xA1), "transfers": 2}
        or detail["top_counterparties"][0]["transfers"] == 2
    )
    assert "holders and their balances" in detail["coverage"]["not_known"]
    assert "holders" not in {k for k in detail if k not in ("coverage",)}


def test_token_detail_for_an_unseen_token(engine: Engine) -> None:
    with pytest.raises(RecordNotFoundError, match="has stored no transfers"):
        views.token_detail(repo(engine), ETH, eth_addr(0x5555))


def test_contract_detail_reports_creation_and_interactions(engine: Engine) -> None:
    seed = Seed(engine)
    contract = eth_addr(0xC1)
    seed.tx(eth_tx(1), eth_addr(0xD1), NOW - timedelta(hours=9), created=contract)
    for i in range(3):
        seed.tx(eth_tx(10 + i), eth_addr(0xE0 + i % 2), NOW - timedelta(hours=5 - i), to=contract)
    seed.tx(eth_tx(20), eth_addr(0xEF), NOW, to=contract, finality="orphaned")
    detail = views.contract_detail(repo(engine), ETH, contract)
    assert detail["created"]["deployer"] == eth_addr(0xD1)
    assert detail["interactions"]["transactions"] == 3  # the orphaned call is excluded
    assert detail["interactions"]["distinct_callers"] == 2
    assert "source code, verification status or ownership" in detail["coverage"]["not_known"]


def test_solana_program_detail_matches_the_program_list_exactly(engine: Engine) -> None:
    seed = Seed(engine)
    program = key(50)
    lookalike = program + "X"
    seed.tx("sigAAAA", key(51), NOW, chain=SOL, programs=[program])
    seed.tx("sigBBBB", key(52), NOW, chain=SOL, programs=[lookalike])
    detail = views.contract_detail(repo(engine), SOL, program)
    assert detail["kind"] == "program"
    assert detail["interactions"]["transactions"] == 1


def test_unknown_contract(engine: Engine) -> None:
    with pytest.raises(RecordNotFoundError, match="has stored nothing"):
        views.contract_detail(repo(engine), ETH, eth_addr(0x4242))


# ------------------------------------------------------------------ notes and timeline


def case(engine: Engine) -> int:
    return repo(engine).create_investigation("Funding review", None, None).id


def test_notes_and_hypotheses_are_stored_separately_and_never_evidenced(engine: Engine) -> None:
    cs = CaseService(repo(engine), clock=lambda: NOW)
    cid = case(engine)
    note = cs.add_note(cid, "note", "Checked the funder on an explorer.", "ana")
    hyp = cs.add_note(cid, "hypothesis", "These wallets may belong to one operator.", "ana")
    assert note["evidenced"] is False
    assert hyp["evidenced"] is False
    assert [n["kind"] for n in cs.notes(cid, kind="hypothesis", limit=10, offset=0)["items"]] == [
        "hypothesis"
    ]
    assert cs.notes(cid, kind=None, limit=10, offset=0)["total"] == 2


def test_note_validation_and_secret_redaction(engine: Engine) -> None:
    cs = CaseService(repo(engine), clock=lambda: NOW)
    cid = case(engine)
    with pytest.raises(InvalidRecordError):
        cs.add_note(cid, "verified", "x", "ana")
    with pytest.raises(InvalidRecordError):
        cs.add_note(cid, "note", "   ", "ana")
    with pytest.raises(InvalidRecordError):
        cs.add_note(cid, "note", "x" * 4001, "ana")
    with pytest.raises(RecordNotFoundError):
        cs.add_note(999, "note", "x", "ana")
    stored = cs.add_note(cid, "note", "key was https://u:hunter2pass@rpc.example/v2/abc", "ana")
    assert "hunter2pass" not in stored["body"]


def test_timeline_merges_everything_in_time_order_with_honest_labels(engine: Engine) -> None:
    cid = case(engine)  # opened at the real current time
    base = utcnow()
    clock_now = [base]
    cs = CaseService(repo(engine), clock=lambda: clock_now[0])
    clock_now[0] = base + timedelta(minutes=5)
    cs.add_note(cid, "hypothesis", "Maybe a single operator.", "ana")
    clock_now[0] = base + timedelta(minutes=10)
    cs.add_note(cid, "note", "Looked at the explorer.", "ana")
    timeline = cs.timeline(cid)
    types = [e["type"] for e in timeline["items"]]
    assert types[0] == "case_opened"
    assert types[1:] == ["hypothesis", "note"]
    assert [e["at"] for e in timeline["items"]] == sorted(e["at"] for e in timeline["items"])
    labels = {e["type"]: e["label"] for e in timeline["items"]}
    assert labels["hypothesis"] == "hypothesis"
    assert "without evidence" in timeline["labels"]["hypothesis"]
    with pytest.raises(RecordNotFoundError):
        cs.timeline(12345)
