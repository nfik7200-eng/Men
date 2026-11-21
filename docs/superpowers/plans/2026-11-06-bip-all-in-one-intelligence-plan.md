## Implementation Tasks

### Task 1: Provider Registry & Non-RPC Data Sources Extension

**Files:**
- Modify: `bip/providers/registry.toml`
- Modify: `bip/providers/registry.py`
- Modify: `bip/router/router.py`
- Test: `tests/stage2/test_registry_and_rpc.py`

**Interfaces:**
- Consumes: `bip/providers/registry.toml`
- Produces: Expanded provider inventory supporting public RPCs, block explorers, and free non-RPC aggregators (DefiLlama, GitHub, DEX routers) with proper capability metadata.

- [ ] **Step 1: Write failing test for new non-RPC providers and public RPC entries**

```python
def test_expanded_providers_registration():
    from bip.providers.registry import load_registry

    registry = load_registry()
    assert "publicnode-eth" in registry
    assert "defillama" in registry
    assert "github" in registry
```

- [ ] **Step 2: Run test to verify failure or success against current registry**

Run: `pytest tests/stage2/test_registry_and_rpc.py -v`

- [ ] **Step 3: Update `bip/providers/registry.toml` and parser to include robust non-RPC endpoints and public RPC fallback channels.**

- [ ] **Step 4: Run tests and commit**

```bash
git add bip/providers/registry.toml bip/providers/registry.py tests/stage2/test_registry_and_rpc.py
git commit -m "feat: expand provider registry with public RPC and non-RPC sources"
```

---

### Task 2: Advanced Strategy Extraction & Wallet Classification Engine (Copy Trader, Detective, Investor, Meme Trader, Dev Tracker)

**Files:**
- Create: `bip/strategies/__init__.py`
- Create: `bip/strategies/classifier.py`
- Create: `bip/strategies/copy_trader.py`
- Create: `bip/strategies/meme_trader.py`
- Create: `bip/strategies/dev_tracker.py`
- Test: `tests/stage5/test_advanced_strategies.py`

**Interfaces:**
- Consumes: Silver transactions, token transfers, contract creations, GitHub metadata.
- Produces: `classify_wallet_profile()`, `evaluate_copy_trade_signals()`, `detect_meme_momentum()`, `track_developer_activity()`.

- [ ] **Step 1: Write failing test for advanced strategy classification and meme/copy trading signals**

```python
def test_wallet_classification_and_copy_trading():
    from bip.strategies.classifier import classify_wallet_profile
    from bip.strategies.copy_trader import evaluate_copy_trade_signals

    # Mock data test
    profile = classify_wallet_profile("0x123", transactions=[], transfers=[])
    assert profile["classification"] in ["unknown", "meme_trader", "investor", "dev", "copy_target"]
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/stage5/test_advanced_strategies.py -v`

- [ ] **Step 3: Implement strategy modules in `bip/strategies/`**

- [ ] **Step 4: Run tests and verify passing**

- [ ] **Step 5: Commit**

```bash
git add bip/strategies/ tests/stage5/test_advanced_strategies.py
git commit -m "feat: add advanced wallet classification, copy trading, meme trader, and dev tracker modules"
```

---

### Task 3: Backend API Integration for Multi-Strategy Intelligence

**Files:**
- Modify: `bip/core/api.py`
- Test: `tests/stage5/test_pipeline.py`

**Interfaces:**
- Consumes: `bip/strategies/*`
- Produces: New API endpoints `/strategies/copy-trader`, `/strategies/meme-momentum`, `/strategies/dev-tracker`, `/wallet/{address}/intelligence`.

- [ ] **Step 1: Add API route tests for strategy endpoints in `tests/stage5/test_pipeline.py`**

- [ ] **Step 2: Implement endpoints in `bip/core/api.py`**

- [ ] **Step 3: Run API tests and verify passing**

- [ ] **Step 4: Commit**

```bash
git add bip/core/api.py tests/stage5/test_pipeline.py
git commit -m "feat: expose strategy intelligence and copy trading endpoints in API"
```

---

### Task 4: Frontend Dashboard Integration for Multi-Strategy & Trackers

**Files:**
- Modify: frontend files / dashboard views (per `docs/FRONTEND_ARCHITECTURE.md`)
- Test: Vitest frontend test suite

**Interfaces:**
- Consumes: Backend strategy API endpoints
- Produces: Dashboard tabs and widgets for Copy Trading, Meme Opportunities, Developer Activity, and Detective Case Files.

- [ ] **Step 1: Implement frontend components / views for strategy monitoring**

- [ ] **Step 2: Run frontend tests**

- [ ] **Step 3: Commit**

```bash
git add frontend/
git commit -m "feat: integrate strategy and tracker views into frontend dashboard"
```

---
