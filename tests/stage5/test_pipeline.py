"""End-to-end offline pipeline (fixtures only; never live blockchain validation):

fake RPC → ingestion → features → model trained on the labelled SYNTHETIC set → classification
persisted → investigation retrieves classification + evidence → report keeps predictions,
observations, interpretations and hypotheses apart.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from bip.db import schema
from tests.stage3.builders import a
from tests.stage5.test_research import API, TERMINAL, World


def wait(c: TestClient, key: str) -> dict[str, Any]:
    for _ in range(400):
        g = c.get(f"{API}/research/runs/{key}").json()
        if g["state"] in TERMINAL:
            return g
        time.sleep(0.02)
    raise AssertionError("timeout")


def test_ml_to_ai_pipeline_on_fixture_data(tmp_path: Path) -> None:
    w = World(tmp_path)
    try:
        with TestClient(w.app) as c:
            sol = w.env.sol.wallet
            assert c.post(f"{API}/ingestion/wallet/solana/{sol}?limit=20").status_code == 201
            trained = c.post(
                f"{API}/ml/training-runs", json={"chain": "solana", "source": "synthetic"}
            ).json()
            assert trained["status"] == "succeeded"
            cls = c.post(f"{API}/ml/classify/solana/{sol}").json()
            assert cls["coverage"]["transactions_observed"] >= 2
            assert cls["model"]["name"] == "wallet-classifier-solana"
            with w.env.engine.connect() as conn:
                assert (
                    conn.execute(
                        select(func.count()).select_from(schema.wallet_classifications)
                    ).scalar_one()
                    == 1
                )
            r = c.post(
                f"{API}/research/runs",
                json={
                    "chain": "solana",
                    "address": sol,
                    "question": "Classify this wallet and review the supporting evidence.",
                },
            )
            g = wait(c, r.json()["run_id"])
            assert g["state"] == "completed_with_limitations"
            assert [t["tool"] for t in g["tool_calls"]][:1] == ["wallet_classification"]
            bundle = w.llm.calls[0]["bundle"]
            assert any(cl["kind"] == "deterministic_rule" for cl in bundle["claims"])
            report = c.get(f"{API}/research/runs/{g['run_id']}/report").json()["report"]
            assert report["classification"]["account_type"]["label"] == "wallet_account"
            assert report["classification"]["model"]["status"] == "experimental_synthetic"
            statuses = {(x["kind"], x["status"]) for v in report["sections"].values() for x in v}
            assert ("blockchain_observation", "unverified") in statuses
            assert not any(s == "verified" for _, s in statuses)
            assert report["legend"]["model_prediction"].endswith("never a verified fact)")
            assert any("synthetic" in x.lower() for x in report["limitations"])
    finally:
        w.env.engine.dispose()


def test_busy_wallet_gets_multiple_roles_and_predictions_stay_inferred(tmp_path: Path) -> None:
    from tests.stage3.test_findings_opportunities import Seeder

    w = World(tmp_path)
    try:
        with TestClient(w.app) as c:
            c.post(f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"})
            bot = a(8080)
            start = datetime.now(UTC) - timedelta(days=2)
            with w.env.engine.begin() as conn:
                s = Seeder(conn)
                for k in range(80):
                    s.tx(start + timedelta(seconds=600 * k), bot, a(9))
            cls = c.post(f"{API}/ml/classify/ethereum/{bot}").json()
            roles = cls["behavioural_roles"]
            model_roles = [r for r in roles if r["source"].startswith("wallet-classifier")]
            rule_roles = [r for r in roles if r["status"] == "rule_based"]
            assert len(roles) >= 2  # multi-label: several roles at once
            assert {r["label"] for r in rule_roles} >= {"high_frequency_activity"}
            # the synthetic-trained model either abstains (score < 0.5, listed in not_assigned)
            # or reports at most low confidence; it never claims more
            assert all(r["status"] == "low_confidence" for r in model_roles)
            assert all(r["model_status"] == "experimental_synthetic" for r in model_roles)
            assert model_roles or cls["not_assigned"]
            r = c.post(
                f"{API}/research/runs",
                json={
                    "chain": "ethereum",
                    "address": bot,
                    "question": "Is this an automated bot?",
                    "focus": ["classification", "behaviour", "anomalies"],
                },
            )
            g = wait(c, r.json()["run_id"])
            inferred = [
                x for x in g["claims"] if x["kind"] in {"model_prediction", "deterministic_rule"}
            ]
            assert len(inferred) >= 3
            assert all(x["status"] == "inferred" for x in inferred)
            assert all(
                "not a verified fact" in x["statement"]
                for x in inferred
                if x["kind"] == "model_prediction"
            )
            md = c.get(f"{API}/research/runs/{g['run_id']}/report").json()["markdown"]
            assert "## Classification" in md
            assert "synthetic" in md.lower()
            _ = json
    finally:
        w.env.engine.dispose()
