"""Continuous retraining status is visible: switch, gate parameters, last decision per chain."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from bip.db.repository import Repository
from bip.ml.continuous import RANKING, WALLET, decision_key, examples_key

API = "/api/v1"


def test_status_without_any_decision(client: TestClient) -> None:
    body = client.get(f"{API}/ml/continuous").json()
    assert body["enabled"] is False
    assert body["interval_hours"] == 6
    assert body["min_new_examples"] == 20
    assert body["evidence_class"] == "statistical_signal"
    kinds = {(d["kind"], d["chain"]) for d in body["items"]}
    assert kinds == {
        (WALLET, "ethereum"),
        (WALLET, "solana"),
        (RANKING, "ethereum"),
        (RANKING, "solana"),
    }
    assert all(
        d["last_decision"] is None and d["examples_at_last_decision"] is None for d in body["items"]
    )


def test_status_shows_the_last_decision(client: TestClient, repo: Repository) -> None:
    decision = {
        "kind": WALLET,
        "chain": "ethereum",
        "time": "2026-11-23T10:00:00+00:00",
        "decision": "rejected",
        "reason": "challenger macro PR-AUC below champion",
        "challenger": {"macro_pr_auc": 0.61, "brier": 0.2, "labels": ["exchange"], "n": 40},
        "champion": {"macro_pr_auc": 0.7, "brier": 0.18, "labels": ["exchange"], "n": 40},
    }
    repo.set_meta(decision_key("ethereum", WALLET), json.dumps(decision))
    repo.set_meta(examples_key("ethereum", WALLET), "57")
    body = client.get(f"{API}/ml/continuous").json()
    item = next(d for d in body["items"] if (d["kind"], d["chain"]) == (WALLET, "ethereum"))
    assert item["last_decision"]["decision"] == "rejected"
    assert item["last_decision"]["challenger"]["macro_pr_auc"] == 0.61
    assert item["examples_at_last_decision"] == 57


def test_a_corrupt_stored_decision_is_reported_not_a_crash(
    client: TestClient, repo: Repository
) -> None:
    repo.set_meta(decision_key("solana", RANKING), "{not json")
    item = next(
        d
        for d in client.get(f"{API}/ml/continuous").json()["items"]
        if (d["kind"], d["chain"]) == (RANKING, "solana")
    )
    assert item["last_decision"] is None
    assert item["error"] == "stored decision is unreadable"
