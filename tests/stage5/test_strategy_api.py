"""Tests for backend API integration of multi-strategy intelligence endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.stage5.test_research import API, World


def test_strategy_api_endpoints(tmp_path: Path) -> None:
    w = World(tmp_path)
    try:
        with TestClient(w.app) as c:
            eth = w.env.eth.wallet
            # Ingest wallet data first
            assert c.post(f"{API}/ingestion/wallet/ethereum/{eth}?limit=20").status_code == 201

            # 1. Classification endpoint
            res = c.get(f"{API}/strategies/classification/ethereum/{eth}")
            assert res.status_code == 200
            data = res.json()
            assert "primary_strategy" in data
            assert "strategies" in data

            # 2. Copy trader endpoint
            res = c.get(f"{API}/strategies/copy-trader/ethereum/{eth}")
            assert res.status_code == 200
            data = res.json()
            assert "score" in data
            assert data["lead"] in {"no_activity", "low_activity", "research_lead"}

            # 3. Meme momentum endpoint
            res = c.get(f"{API}/strategies/meme-momentum/ethereum/{eth}")
            assert res.status_code == 200
            data = res.json()
            assert "momentum_score" in data
            assert "tokens_involved" in data

            # 4. Dev tracker endpoint
            res = c.get(f"{API}/strategies/dev-tracker/ethereum/{eth}")
            assert res.status_code == 200
            data = res.json()
            assert "deployments_count" in data
            assert "deployer_address" in data
    finally:
        w.env.engine.dispose()
