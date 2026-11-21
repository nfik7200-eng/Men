"""Curation feedback: provenance snapshot, training-label signal, active-learning queue."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, select

from bip.db import schema
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.ml.labels import LabelStore

API = "/api/v1"
ADDR = "0x" + "ab" * 20
AS_OF = "2026-01-01T00:00:00+00:00"
J = dict[str, Any]


def role(label: str, conf: float) -> J:
    return {
        "label": label,
        "status": "low_confidence",
        "confidence": conf,
        "source": "wallet-classifier-ethereum@1.0.0",
        "model_status": "experimental_synthetic",
        "calibrated": True,
        "explanation": "x",
        "evidence": [],
        "supporting_features": [],
        "caveat": None,
    }


def seed(engine: Engine, cid: str, address: str = ADDR, roles: list[J] | None = None) -> None:
    payload: J = {
        "classification_id": cid,
        "wallet_address": address,
        "chain": "ethereum",
        "as_of": AS_OF,
        "feature_digest": "d" * 64,
        "taxonomy_version": "1.0.0",
        "model": None,
        "data_origin": "stored_observations",
        "account_type": {**role("externally_owned_account", 0.9), "confidence": None},
        "behavioural_roles": roles
        if roles is not None
        else [role("high_frequency_activity", 0.93), role("automated_bot", 0.51)],
        "contradictions": [],
        "supporting_evidence_refs": ["tx1"],
    }
    with engine.begin() as conn:
        conn.execute(
            insert(schema.wallet_classifications).values(
                classification_key=cid,
                chain="ethereum",
                address=address,
                classified_at=utcnow(),
                as_of=utcnow(),
                feature_version="1.0.0",
                feature_digest="d" * 64,
                models_json="[]",
                status="classified",
                data_origin="stored_observations",
                payload_json=json.dumps(payload),
            )
        )


def fb(**kw: object) -> J:
    return {
        "target_type": "classification_label",
        "target_key": "c1",
        "label": "automated_bot",
        "decision": "accepted",
        "reviewer": "ana",
        **kw,
    }


def test_accept_and_reject_write_provenance_and_training_labels(
    client: TestClient, engine: Engine
) -> None:
    seed(engine, "c1")
    r = client.post(f"{API}/curation/feedback", json=fb())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["model_ref"] == "wallet-classifier-ethereum@1.0.0"
    assert body["snapshot"]["prediction"]["confidence"] == 0.51
    assert body["snapshot"]["supporting_evidence_refs"] == ["tx1"]
    assert body["training_label_id"] is not None
    rej = client.post(
        f"{API}/curation/feedback", json=fb(label="high_frequency_activity", decision="rejected")
    )
    assert rej.status_code == 201
    labels, _ = LabelStore(Repository(engine)).list(
        chain="ethereum", data_kind="real", limit=10, offset=0
    )
    by = {x["label"]: x for x in labels}
    assert by["automated_bot"]["value"] is True
    assert by["high_frequency_activity"]["value"] is False
    assert by["automated_bot"]["status"] == "analyst_reviewed"
    assert by["automated_bot"]["source"] == "analyst_curation:ana"
    assert by["automated_bot"]["source_ref"] == "c1"


def test_needs_evidence_writes_no_label_and_keeps_item_queued(
    client: TestClient, engine: Engine
) -> None:
    seed(engine, "c1")
    r = client.post(f"{API}/curation/feedback", json=fb(decision="needs_evidence"))
    assert r.json()["training_label_id"] is None
    with engine.connect() as conn:
        assert conn.execute(select(schema.wallet_labels.c.id)).first() is None
    q = client.get(f"{API}/curation/queue").json()
    assert {i["label"] for i in q["items"]} == {"high_frequency_activity", "automated_bot"}


def test_queue_orders_by_uncertainty_and_drops_reviewed(client: TestClient, engine: Engine) -> None:
    seed(engine, "c1")
    q = client.get(f"{API}/curation/queue").json()
    assert [i["label"] for i in q["items"]] == ["automated_bot", "high_frequency_activity"]
    assert q["items"][0]["uncertainty"] > q["items"][1]["uncertainty"]
    client.post(f"{API}/curation/feedback", json=fb())
    q = client.get(f"{API}/curation/queue").json()
    assert [i["label"] for i in q["items"]] == ["high_frequency_activity"]
    assert client.get(f"{API}/curation/queue?chain=solana").json()["items"] == []


def test_queue_uses_latest_classification_per_wallet(client: TestClient, engine: Engine) -> None:
    seed(engine, "old", roles=[role("automated_bot", 0.5)])
    seed(engine, "new", roles=[role("high_frequency_activity", 0.6)])
    items = client.get(f"{API}/curation/queue").json()["items"]
    assert [i["classification_id"] for i in items] == ["new"]


def test_validation_and_not_found(client: TestClient, engine: Engine) -> None:
    seed(engine, "c1")
    url = f"{API}/curation/feedback"
    assert client.post(url, json=fb(target_key="nope")).status_code == 404
    assert client.post(url, json=fb(label="long_term_holder")).status_code == 422
    assert client.post(url, json=fb(label=None)).status_code == 422
    assert client.post(url, json=fb(decision="maybe")).status_code == 422
    assert client.post(url, json=fb(extra="x")).status_code == 422
    assert (
        client.post(url, json=fb(target_type="pattern_match", target_key="999")).status_code == 404
    )
    assert client.post(url, json=fb(target_type="pattern_match", target_key="x")).status_code == 422


def test_pattern_match_feedback_is_signal_only(client: TestClient, engine: Engine) -> None:
    case = client.post(f"{API}/cases", json={"title": "C", "case_type": "rugpull"}).json()["id"]
    ev = client.post(
        f"{API}/cases/{case}/evidence",
        json={
            "kind": "hypothesis",
            "subject_type": "address",
            "subject_ref": ADDR,
            "description": "rule matched",
            "payload": {"name": "fan_out_burst"},
            "source_kind": "local_analysis",
            "source_ref": "analysis_run:1",
            "collected_at": AS_OF,
        },
    )
    assert ev.status_code == 201, ev.text
    eid = str(ev.json()["id"])
    r = client.post(
        f"{API}/curation/feedback",
        json={
            "target_type": "pattern_match",
            "target_key": eid,
            "decision": "rejected",
            "reviewer": "ana",
            "note": "benign",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["label"] == "fan_out_burst"
    assert r.json()["training_label_id"] is None
    assert r.json()["snapshot"]["source_ref"] == "analysis_run:1"
    with engine.connect() as conn:
        assert conn.execute(select(schema.wallet_labels.c.id)).first() is None


def test_stats_and_list(client: TestClient, engine: Engine) -> None:
    seed(engine, "c1")
    client.post(f"{API}/curation/feedback", json=fb())
    client.post(f"{API}/curation/feedback", json=fb(reviewer="bo", decision="rejected"))
    client.post(f"{API}/curation/feedback", json=fb(reviewer="cy", decision="needs_evidence"))
    s = client.get(f"{API}/curation/stats").json()
    assert s["total"] == 3
    assert s["by_decision"] == {"accepted": 1, "rejected": 1, "needs_evidence": 1}
    assert s["per_label"][0]["agreement_rate"] == 0.5
    lst = client.get(f"{API}/curation/feedback?limit=2").json()
    assert lst["total"] == 3
    assert len(lst["items"]) == 2
    assert lst["items"][0]["reviewer"] == "cy"  # newest first
