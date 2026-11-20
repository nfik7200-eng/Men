"""Tests for backend API integration of multi-strategy intelligence endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pathlib import Path

from tests.stage5.test_research import API, World


def test_strategy_api_endpoints(tmp_path: Path) -> None:
    w = World(tmp_path)
    try:
        with TestClient(w.app) as c:
            sol = w.env.sol.wallet
            # Ingest wallet data first
            assert c.post(f"{API}/ingestion/wallet/solana/{sol}?limit=20").status_code == 201

            # 1. Classification endpoint
            res = c.get(f"{API}/strategies/classification/solana/{sol}")
            assert res.status_code == 200
            data = res.json()
            assert "primary_strategy" in data
            assert "strategies" in data

            # 2. Copy trader endpoint
            res = c.get(f"{API}/strategies/copy-trader/solana/{sol}")
            assert res.status_code == 200
            data = res.json()
            assert "score" in data
            assert "recommendation" in data

            # 3. Meme momentum endpoint
            res = c.get(f"{API}/strategies/meme-momentum/solana/{sol}")
            assert res.status_code == 200
            data = res.json()
            assert "momentum_score" in data
            assert "tokens_involved" in data

            # 4. Dev tracker endpoint
            res = c.get(f"{API}/strategies/dev-tracker/solana/{sol}")
            assert res.status_code == 200
            data = res.json()
            assert "deployments_count" in data
            assert "deployer_address" in data
    finally:
        w.env.engine.dispose()
