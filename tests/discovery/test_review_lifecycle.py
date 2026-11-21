"""Stage 4 review lifecycle and ground truth (schema v19). SYNTHETIC rows only."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert, text

from bip.config import load_settings
from bip.db import schema
from bip.db.engine import create_db_engine
from bip.db.errors import InvalidRecordError
from bip.db.repository import Repository
from bip.discovery.review import (
    DEFINITIONS_VERSION,
    STATES,
    TRANSITIONS,
    ReviewService,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


@pytest.fixture
def repo(engine: Engine) -> Repository:
    r = Repository(engine)
    r.set_meta("data_origin", "live_network")  # ground truth needs an explicit real origin
    return r


@pytest.fixture
def review(repo: Repository) -> ReviewService:
    return ReviewService(repo)


def signal(repo: Repository, n: int, chain: str = "ethereum", subject: str | None = None) -> int:
    with repo.transaction() as conn:
        pk = conn.execute(
            insert(schema.signals).values(
                signal_key=f"k{n}",
                detector_id="large_transfer_mad",
                detector_version="1",
                family="large_transfers",
                chain=chain,
                subject_kind="wallet",
                subject_ref=subject or f"0x{n:040x}",
                title="SYNTHETIC",
                severity="low",
                score=10.0,
                confidence=0.1,
                status="open",
                explanation_json="{}",
                evidence_json="[]",
                coverage_json="{}",
                occurrences=1,
                observed_at=T0,
                first_seen_at=T0,
                last_seen_at=T0,
            )
        ).inserted_primary_key
    assert pk is not None
    return int(pk[0])


# ------------------------------------------------------------------ states and transitions


def test_the_lifecycle_has_the_stage4_states_and_never_writes_accepted() -> None:
    for state in ("new", "investigating", "confirmed", "rejected", "duplicate"):
        assert state in STATES
    assert "insufficient_evidence" in STATES
    assert "accepted" not in STATES
    # finished states only reopen, and only to investigating
    for done in ("confirmed", "rejected", "duplicate", "insufficient_evidence", "archived"):
        assert TRANSITIONS[done] == ("investigating",)


def test_decisions_follow_the_transition_table_and_reasons_fit_the_state(
    repo: Repository, review: ReviewService
) -> None:
    s = signal(repo, 1)
    review.decide(s, "investigating", "needs_more_data", None, "alice")
    with pytest.raises(InvalidRecordError, match="reason"):
        review.decide(s, "confirmed", "false_positive", None, "alice")  # contradicts the state
    out = review.decide(s, "confirmed", "evidence_sufficient", "two sources agree", "alice")
    assert out["review_state"] == "confirmed"
    with pytest.raises(InvalidRecordError, match="cannot move"):
        review.decide(s, "monitoring", "promoted_to_watchlist", None, "alice")
    with pytest.raises(InvalidRecordError, match="reopen"):
        review.decide(s, "investigating", "needs_more_data", None, "alice")  # reopen needs reason
    review.decide(s, "investigating", "reopened", "new evidence arrived", "alice")
    states = [h["to_state"] for h in review.history(s)]
    assert states == ["investigating", "confirmed", "investigating"]  # never erased
    assert all(h["definitions_version"] == DEFINITIONS_VERSION for h in review.history(s))


def test_the_old_name_accepted_is_an_alias_for_confirmed(
    repo: Repository, review: ReviewService
) -> None:
    s = signal(repo, 2)
    assert review.decide(s, "accepted", "evidence_sufficient", None, "alice")["review_state"] == (
        "confirmed"
    )
    assert review.history(s)[-1]["to_state"] == "confirmed"


def test_a_duplicate_names_the_candidate_it_duplicates(
    repo: Repository, review: ReviewService
) -> None:
    a, b = signal(repo, 3), signal(repo, 4)
    with pytest.raises(InvalidRecordError, match="duplicate_of"):
        review.decide(b, "duplicate", "duplicate", None, "alice")
    with pytest.raises(InvalidRecordError, match="itself"):
        review.decide(b, "duplicate", "duplicate", None, "alice", duplicate_of=b)
    with pytest.raises(InvalidRecordError, match="not found"):
        review.decide(b, "duplicate", "duplicate", None, "alice", duplicate_of=99_999)
    review.decide(b, "duplicate", "duplicate", None, "alice", duplicate_of=a)
    assert review.history(b)[-1]["duplicate_of"] == a
    with pytest.raises(InvalidRecordError, match="duplicate_of"):  # only on a duplicate
        review.decide(a, "rejected", "false_positive", None, "alice", duplicate_of=b)


def test_a_correction_and_a_wallet_label_are_kept_on_the_decision(
    repo: Repository, review: ReviewService
) -> None:
    s = signal(repo, 5)
    review.decide(
        s,
        "confirmed",
        "evidence_sufficient",
        None,
        "alice",
        correction="the subject is the router, not the pool",
        wallet_label="exchange_hot_wallet",
    )
    last = review.history(s)[-1]
    assert last["correction"] == "the subject is the router, not the pool"
    assert last["wallet_label"] == "exchange_hot_wallet"
    with pytest.raises(InvalidRecordError, match="wallet_label"):
        review.decide(
            signal(repo, 6), "confirmed", "evidence_sufficient", None, "a", wallet_label="nope"
        )
    with pytest.raises(InvalidRecordError, match="wallet_label"):  # a label needs a confirmation
        review.decide(
            signal(repo, 7),
            "rejected",
            "false_positive",
            None,
            "a",
            wallet_label="exchange_hot_wallet",
        )


def test_promote_and_dismiss_moves_that_the_table_refuses_are_reported_not_swallowed(
    repo: Repository, review: ReviewService
) -> None:
    s = signal(repo, 8)
    review.decide(s, "confirmed", "evidence_sufficient", None, "alice")
    with pytest.raises(InvalidRecordError, match="cannot move"):
        review.record(s, "monitoring", "promoted_to_watchlist", None, "alice")
    review.record(s, "confirmed", "evidence_sufficient", None, "alice")  # a repeat is a no-op


# ------------------------------------------------------------------ ground truth


def test_one_human_decision_is_ground_truth_and_machines_never_are(
    repo: Repository, review: ReviewService
) -> None:
    confirmed, rejected, machine, pending = (signal(repo, n) for n in (10, 11, 12, 13))
    review.decide(confirmed, "confirmed", "evidence_sufficient", None, "alice")
    review.decide(rejected, "rejected", "false_positive", None, "alice")
    review.decide(machine, "rejected", "false_positive", None, "system:auto")
    review.decide(pending, "investigating", "needs_more_data", None, "alice")
    truth = review.ground_truth(confirmed)
    assert (truth["label"], truth["quality"], truth["eligible"]) == (1, "human_review", True)
    assert review.ground_truth(rejected)["label"] == 0
    assert review.ground_truth(machine)["eligible"] is False  # not a person
    assert review.ground_truth(pending)["label"] is None
    for i, kind in enumerate(("ai:model", "signal:7")):
        s = signal(repo, 20 + i)
        review.decide(s, "confirmed", "evidence_sufficient", None, kind)
        assert review.ground_truth(s)["eligible"] is False


def test_reviewer_disagreement_is_disputed_until_resolved(
    repo: Repository, review: ReviewService
) -> None:
    s = signal(repo, 30)
    review.decide(s, "confirmed", "evidence_sufficient", None, "alice")
    review.decide(s, "rejected", "false_positive", "looks like payroll", "bob")  # a second opinion
    truth = review.ground_truth(s)
    assert (truth["label"], truth["quality"], truth["eligible"]) == (None, "disputed", False)
    assert set(truth["reviewers"]) == {"alice", "bob"}
    with pytest.raises(InvalidRecordError, match="cannot move"):  # the same person: reopen first
        review.decide(s, "confirmed", "evidence_sufficient", None, "bob")
    review.decide(s, "confirmed", "dispute_resolved", "payroll ruled out", "carol")
    truth = review.ground_truth(s)
    assert (truth["label"], truth["quality"], truth["eligible"]) == (1, "dispute_resolved", True)


def test_agreement_between_reviewers_is_consensus(repo: Repository, review: ReviewService) -> None:
    s = signal(repo, 31)
    review.decide(s, "rejected", "false_positive", None, "alice")
    review.decide(s, "investigating", "reopened", "second look", "bob")
    review.decide(s, "rejected", "out_of_scope", None, "bob")
    # a reopen starts a new review round: only decisions after it count
    assert review.ground_truth(s)["quality"] == "human_review"


def test_pre_v19_accepted_rows_count_as_confirmed(repo: Repository, review: ReviewService) -> None:
    s = signal(repo, 32)
    with repo.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO signal_decisions (signal_id, from_state, to_state, reason_code,"
                " actor, decided_at) VALUES (:s, 'new', 'accepted', 'evidence_sufficient',"
                " 'alice', :d)"
            ),
            {"s": s, "d": "2026-11-01 00:00:00.000000"},
        )
    assert review.state(s) == "confirmed"  # read as the new name
    assert review.ground_truth(s)["label"] == 1


def test_data_of_unknown_or_synthetic_origin_is_never_ground_truth(
    repo: Repository, review: ReviewService
) -> None:
    s = signal(repo, 40)
    review.decide(s, "confirmed", "evidence_sufficient", None, "alice")
    assert review.ground_truth(s)["eligible"] is True
    for origin in ("offline_fixture", "synthetic", ""):
        repo.set_meta("data_origin", origin)
        truth = review.ground_truth(s)
        assert truth["eligible"] is False
        assert truth["excluded_because"]


def test_the_ground_truth_set_is_bounded_and_per_chain(
    repo: Repository, review: ReviewService
) -> None:
    for n in range(6):
        s = signal(repo, 50 + n, chain="ethereum" if n % 2 else "solana")
        review.decide(
            s,
            "confirmed" if n < 3 else "rejected",
            "evidence_sufficient" if n < 3 else "false_positive",
            None,
            "alice",
        )
    data = review.ground_truth_set(limit=4)
    assert len(data["items"]) == 4
    counts = review.ground_truth_set()["counts"]
    assert counts["ethereum"] == {"positive": 1, "negative": 2}
    assert counts["solana"] == {"positive": 2, "negative": 1}


def test_a_confirmed_wallet_label_feeds_the_wallet_classifier_labels(
    repo: Repository, review: ReviewService
) -> None:
    from bip.ml.labels import LabelStore

    s = signal(repo, 60)
    review.decide(
        s, "confirmed", "evidence_sufficient", None, "alice", wallet_label="exchange_hot_wallet"
    )
    items, total = LabelStore(repo).list(chain="ethereum", data_kind=None, limit=10, offset=0)
    assert total == 1
    [row] = items
    assert (row["label"], row["status"], row["source"]) == (
        "exchange_hot_wallet",
        "analyst_reviewed",
        "signal_review",
    )
    assert row["source_ref"] == f"signal:{s}"
    assert row["data_kind"] == "real"  # the database is marked live_network
    # a label that does not apply to the chain is refused before anything is written
    sol = signal(repo, 61, chain="solana", subject="So1ana" + "1" * 38)
    with pytest.raises(InvalidRecordError, match="does not apply"):
        review.decide(
            sol, "confirmed", "evidence_sufficient", None, "a", wallet_label="smart_contract"
        )
    assert review.state(sol) == "new"


def test_labels_from_unmarked_data_are_stored_as_synthetic(
    repo: Repository, review: ReviewService
) -> None:
    from bip.ml.labels import LabelStore

    repo.set_meta("data_origin", "offline_fixture")
    s = signal(repo, 62)
    review.decide(
        s, "confirmed", "evidence_sufficient", None, "alice", wallet_label="exchange_hot_wallet"
    )
    items, _ = LabelStore(repo).list(chain="ethereum", data_kind=None, limit=10, offset=0)
    assert items[0]["data_kind"] == "synthetic"  # never real training data


def test_dispute_resolved_needs_an_actual_disagreement(
    repo: Repository, review: ReviewService
) -> None:
    s = signal(repo, 70)
    review.decide(s, "confirmed", "evidence_sufficient", None, "alice")
    # one reviewer cannot flip their own outcome by calling it a resolution
    with pytest.raises(InvalidRecordError, match="disagreement"):
        review.decide(s, "rejected", "dispute_resolved", "changed my mind", "alice")
    with pytest.raises(InvalidRecordError, match="disagreement"):
        review.decide(s, "rejected", "dispute_resolved", None, "bob")
    # nor give themselves a "second opinion"
    with pytest.raises(InvalidRecordError, match="cannot move"):
        review.decide(s, "rejected", "false_positive", None, "alice")
    assert review.ground_truth(s)["quality"] == "human_review"
    # a real disagreement, then a resolution
    review.decide(s, "rejected", "false_positive", None, "bob")
    review.decide(s, "rejected", "dispute_resolved", "payroll pattern confirmed", "carol")
    truth = review.ground_truth(s)
    assert (truth["label"], truth["quality"]) == (0, "dispute_resolved")
    # once resolved there is no live disagreement left to resolve
    with pytest.raises(InvalidRecordError, match="disagreement"):
        review.decide(s, "confirmed", "dispute_resolved", None, "dave")


def test_the_ground_truth_set_costs_a_constant_number_of_queries(
    engine: Engine, repo: Repository, review: ReviewService
) -> None:
    from sqlalchemy import event

    for n in range(40):
        review.decide(signal(repo, 100 + n), "rejected", "false_positive", None, "alice")
    statements: list[str] = []

    def count(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", count)
    try:
        data = review.ground_truth_set(limit=5)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert data["counts"]["ethereum"] == {"positive": 0, "negative": 40}
    assert len(data["items"]) == 5
    decision_reads = [q for q in statements if "signal_decisions" in q]
    assert len(decision_reads) <= 2, f"{len(decision_reads)} queries for 40 signals"


def test_a_fed_wallet_label_is_withdrawn_when_the_candidate_stops_being_confirmed(
    repo: Repository, review: ReviewService
) -> None:
    from bip.ml.labels import LabelStore

    s = signal(repo, 80)
    review.decide(
        s, "confirmed", "evidence_sufficient", None, "alice", wallet_label="exchange_hot_wallet"
    )
    store = LabelStore(repo)
    assert len(store.training_rows("ethereum", "real")) == 1
    review.decide(s, "investigating", "reopened", "new evidence", "bob")
    assert store.training_rows("ethereum", "real") == []  # no longer backed by a confirmation
    items, _ = store.list(chain="ethereum", data_kind=None, limit=10, offset=0)
    assert items[0]["latest_review"] == "rejected"  # kept, with the withdrawal on record


def test_a_withdrawn_wallet_label_is_reinstated_when_the_candidate_is_confirmed_again(
    repo: Repository, review: ReviewService
) -> None:
    from bip.ml.labels import LabelStore

    s = signal(repo, 90)
    review.decide(
        s, "confirmed", "evidence_sufficient", None, "alice", wallet_label="exchange_hot_wallet"
    )
    review.decide(s, "investigating", "reopened", "check again", "bob")
    store = LabelStore(repo)
    assert store.training_rows("ethereum", "real") == []
    review.decide(s, "confirmed", "evidence_sufficient", "still holds", "bob")
    assert len(store.training_rows("ethereum", "real")) == 1  # back, with the history kept


def test_a_machine_actor_cannot_give_a_second_opinion(
    repo: Repository, review: ReviewService
) -> None:
    s = signal(repo, 91)
    review.decide(s, "confirmed", "evidence_sufficient", None, "alice")
    with pytest.raises(InvalidRecordError, match="cannot move"):
        review.decide(s, "rejected", "dismissed", None, "system:auto")
