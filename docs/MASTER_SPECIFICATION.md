# BIP Master Specification (Deliverable A)

Version 0.1 (2026-11-03). Status: **Draft, awaiting owner approval.**

## 1. Product definition

BIP (Blockchain Intelligence Platform) is a multi-chain intelligence system. It:
1. **acquires** blockchain and public ecosystem data from many independent free sources;
2. **preserves** it with full provenance in a columnar lakehouse;
3. **normalizes** it into chain-agnostic schemas;
4. **derives** analytical features, relationships and signals;
5. **investigates** important signals with selective AI agents;
6. **reports** findings that are traceable to evidence and labelled with confidence.

### 1.1 What BIP is not
- Not a block explorer (it does not aim to serve arbitrary lookups for every address).
- Not a full replica of every chain (a zero-cost replica is not feasible; see `DATA_ARCHITECTURE.md` §9).
- Not a trading bot. It produces intelligence; any execution system would be separate.
- Not an identity de-anonymization tool. It reports address behavior, not real-world identities, unless independently verifiable public evidence exists (e.g. an ENS name the project itself publishes, a verified deployer statement on the project's official site).

## 2. Users

| User | Needs |
|---|---|
| Owner / primary analyst (initial and main user) | Alerts on emerging projects, whale moves and risks; investigation reports; watchlists |
| Research agents (internal) | Typed, read-only access to evidence and features |
| Future team analysts | Shared watchlists, report review, annotation |
| Future API consumers | Programmatic access to Gold/Intelligence datasets |

Single-tenant first. Multi-user auth is a later stage.

## 3. Objectives

| ID | Objective | Measure |
|---|---|---|
| O1 | Reliable, resumable acquisition from multiple free providers | No committed-range loss after kill -9; provider failure does not stop ingestion |
| O2 | Honest coverage | Every dataset has a coverage map; gaps are explicit, never zero-filled |
| O3 | Evidence-traceable intelligence | 100% of findings link to evidence records with raw-data references |
| O4 | Early detection | New deployments / liquidity events for watched scopes alerted within minutes of finality |
| O5 | Cost discipline | $0 recurring until owner approves a paid item |
| O6 | Extensibility | Adding a chain = new chain adapter + registry entries, no intelligence-layer change |

## 4. Intelligence capabilities

Each capability lists its data dependencies so the roadmap can sequence it.

### A. Wallet intelligence
Profiles, activity history, counterparties, token transfers, balance changes, accumulation/distribution, estimated PnL (only where price + cost-basis data is reliable; otherwise `unknown`), watchlists, classification (e.g. `cex_hot_wallet`, `contract_deployer`, `mev_bot`, `market_maker_like`) with evidence and confidence, relationship discovery.
*Depends on:* Silver transactions, token transfers, swaps; price data; labels.

### B. Developer intelligence
Deployer identification, deployment histories, funding wallets of deployers, GitHub repository analysis (public API only), repo↔contract linking (verified source matches, addresses in repos/docs), developer↔project graphs, activity acceleration.
*Depends on:* contract creations (incl. internal creations via traces where obtainable), explorer verified-source data, GitHub API.

### C. Whale and capital-flow intelligence
Large transfers, CEX inflows/outflows, stablecoin flows, funding paths, rotations, liquidity moves, coordinated-activity detection, repeated funding patterns.
Must classify movements as `transfer`, `swap_buy`, `swap_sell`, `internal_move` (same-entity cluster), `bridge`, `cex_deposit`, `cex_withdrawal`, `unknown`.
*Depends on:* transfers, swaps, labels, entity clustering.

### D. Early-project and opportunity intelligence
New deployments, new tokens, early liquidity, holder growth anomalies, influential-wallet early entries, developer acceleration, funding/governance/listing announcements, ecosystem growth.
Scoring is evidence-based and decomposed: each opportunity score lists contributing signals, their values, their evidence, and labels (`verified` / `signal` / `hypothesis`).

### E. DeFi and token intelligence
DEX activity, liquidity, volume, supply and holder concentration, lending/borrowing, TVL, fees/revenue (from DefiLlama-type indexers where available), contract permissions (owner, minter, pauser, blacklist), mint/admin capabilities, proxy/upgradeability (EIP-1967/UUPS/Beacon slots; Solana upgrade authority, mint/freeze authority), risk indicators.

### F. Relationship and graph intelligence
Typed edges between wallets, developers, contracts, tokens, protocols, exchanges, funding sources, projects. Each edge: `type`, `first_seen`, `last_seen`, `evidence_count`, `supporting_refs` (tx hashes/signatures), `source`, `confidence`, `method` (rule id / model version).

### G. Autonomous AI research (future)
Agents monitor intelligence events, select evidence, investigate, trace relationships, generate and verify hypotheses, write reports, alert. Pluggable LLM adapters (Ollama local; others via adapters). AI never in the ingestion path; never with unrestricted DB/shell access.

## 5. Evidence and claim model

| Label | Definition | Example |
|---|---|---|
| **Verified observation** | Directly present in canonical chain data or an authoritative public source, re-checkable | "Address X deployed contract Y in tx Z at block N" |
| **Statistical signal** | Computed from data with a stated method and threshold | "Holder count grew 6σ above the 30-day baseline" |
| **Hypothesis** | Inference not yet verified | "X and W are likely controlled by the same operator (shared funder, timing correlation)" |

A finding is promoted only by new evidence, never by repetition or by an LLM asserting it.

## 6. Non-functional requirements

| Area | Requirement |
|---|---|
| Cost | $0/month baseline; every paid item needs an approved ADR |
| Reliability | Idempotent, resumable ingestion; checkpoints transactional with data commits |
| Correctness | Finality-aware; reorg handling; cross-provider verification on sampled data |
| Provenance | Every record carries chain, position, source, method, timestamps, schema version, raw ref |
| Scalability | Billions of rows held in partitioned Parquet, not in one RDBMS table |
| Extensibility | Chain adapters + provider registry; no hardcoded two-chain assumptions |
| Footprint | Deployable on a modest single host; storage root relocatable to separate disk/object store |
| Security | No secrets in code; SSRF-safe configurable URLs; least-privilege DB roles; audited agent tools |
| Compliance | Respect ToS/robots.txt/quotas; no circumvention of access controls |
| Observability | Metrics for provider health, quota, ingestion lag, coverage, disk usage |

## 7. Scope phasing

- **Phase 1 (foundation):** registry, router, Ethereum + Solana live and scoped historical ingestion, Bronze/Silver, coverage tracking.
- **Phase 2 (intelligence core):** Gold features, labels, entity clustering, graph edges, alerting, watchlists.
- **Phase 3 (research):** AI agents, reports, web intelligence (GitHub, governance, announcements).
- **Phase 4 (expansion):** Base, Arbitrum, BNB; dashboard; multi-user.

## 8. Open questions awaiting owner input

See `DECISIONS.md` → items with status `Proposed` and the "Awaiting approval" list in `CURRENT_STATE.md`.
