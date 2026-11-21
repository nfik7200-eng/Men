# Free Provider Expansion Plan (2026-11-21)

Goal: register the free RPC and indexed-data providers from the owner's 2026-11-21 provider
review that are not yet in `bip/providers/registry.toml`, give the owner a key-onboarding
checklist, and record the Dune/Bitquery position. No accounts are created, no keys are
configured, no paid plan is used.

Spec: `CLAUDE.md` §2 (principles 1–4), §4, §5.6; `docs/PROVIDER_REGISTRY.md`;
`docs/DATA_ARCHITECTURE.md` (Provider Intelligence Router). Roadmap stage: Stage 2/4 provider
registry maintenance (same kind of work as commit 3eebc7e).

Facts source: `FACTS` = `/tmp/claude-0/-root-Men/102e68c5-6861-4ece-a9e5-79ea48cce461/scratchpad/provider-facts.md`
(researched 2026-11-21 from official pages, each fact with its source URL).

## Global Constraints

- Every documented fact in a registry entry must come from FACTS with its source URL in
  `sources`. A fact FACTS marks "unknown" or only search-derived is omitted or noted, never
  guessed. `documentation_basis = "official_docs"` only when every fact used was read from an
  official page; otherwise `"search_derived"`.
- `documentation_checked = "2026-11-21"` on every new entry.
- Ids are lowercase `<operator>-<chain>` for RPC (`validationcloud-eth`, `validationcloud-sol`,
  `ankr-sol`, `dwellir-eth`, `dwellir-sol`, `blockpi-eth`, `nodereal-eth`, `infstones-eth`) and
  `<operator>` for indexers (`chainbase`, `routescan`). Skip a provider (and say so in the
  report) if FACTS shows it has no free plan or does not serve that chain.
- RPC entry with a documented fixed hostname: `kind = "rpc"`, protocol `evm-jsonrpc` or
  `solana-jsonrpc`, `access = "credentials"`, `endpoint_env = "BIP_PROVIDER_<ID_UPPER>_URL"`,
  `allowed_hosts` = exact documented host(s), `methods` = the same method list the existing
  `alchemy-eth` / `alchemy-sol` entries use, `client_rps` ≤ 4.0 and never above half the
  documented free limit, own `infrastructure` group = operator slug.
- RPC entry whose hostname is per-project or undocumented: follow the `chainstack` pattern
  (`protocol = "none"`, `methods = []`, note "Catalogue entry only; host unknown until the owner
  creates an endpoint").
- Indexer entries: `kind = "indexer_api"`, `protocol = "none"`, `methods = []`,
  `endpoint_env = "BIP_PROVIDER_<ID_UPPER>_KEY"`, note "Catalogue entry only; no adapter".
- Restrictive terms (non-commercial, no bulk/scraping, attribution) go in `notes`.
- Every new `endpoint_env` gets a commented placeholder line in `.env.example` (no real keys).
- No changes to router, prober or loader code. No live network calls in tests.
- Checks: `uv run ruff check bip tests`, `uv run pyright bip/providers`,
  `uv run pytest tests/stage2 -q` must pass; record real output.
- Commit messages imperative, explain why, no model identifiers.

## Task 1: Register the missing free RPC providers

Files: `bip/providers/registry.toml`, `.env.example`, new test
`tests/stage2/test_free_provider_expansion.py`.

1. Write the test first: load `default_registry()`; for each RPC id added, assert it exists,
   `documentation_checked == "2026-11-21"`, `sources` non-empty, methods ⊆ `READ_ONLY_METHODS`,
   and for adapter entries that `endpoint_env` starts with `BIP_PROVIDER_` and `allowed_hosts`
   is non-empty; assert every adapter entry's `infrastructure` differs from every other
   operator's (independence). Assert `resolve_endpoint` reports "not_configured" when the env
   var is unset (use `monkeypatch.delenv`).
2. Add the entries under the matching chain sections of `registry.toml` using FACTS values.
3. Add `.env.example` placeholders with the documented URL shape from FACTS.
4. Run the checks; commit.

## Task 2: Register the indexed-data providers and the Infura Solana finding

Files: `bip/providers/registry.toml`, `.env.example`,
`tests/stage2/test_free_provider_expansion.py` (extend).

1. Extend the test: `chainbase` and `routescan` exist, are `indexer_api`, have no adapter
   (`has_adapter is False`), have sources and the check date.
2. Add both entries (Routescan: record keyless vs keyed limits and that it is EVM-only, per
   FACTS). If FACTS shows Infura's free plan serves Solana mainnet with a fixed host, add
   `infura-sol` as an RPC entry under Task 1 rules; otherwise add one sentence to the
   `infura-eth` notes recording the finding.
3. Run the checks; commit.

## Task 3: Owner onboarding checklist, ADR and state docs

Files: new `docs/PROVIDER_ONBOARDING.md`, `docs/PROVIDER_REGISTRY.md`, `DECISIONS.md`,
`CURRENT_STATE.md`, `IMPLEMENTATION_LOG.md`.

1. `docs/PROVIDER_ONBOARDING.md`: a table, one row per free provider in the registry that needs
   a key (new and existing), in the recommended priority order — Validation Cloud, Ankr,
   Alchemy, Helius, Chainbase, Routescan, The Graph, Birdeye, then the rest — with: signup page
   URL (from FACTS / registry `docs_url`), free allowance, card required?, the exact env var,
   and the URL shape. State plainly: the owner creates accounts and keys personally; BIP never
   automates signups (CLAUDE.md §2.4: no CAPTCHA circumvention, ToS acceptance is the owner's).
   Include how to verify a key afterwards with the capability prober (cite the existing make
   target or CLI from `docs/PROVIDER_CAPABILITY_METHODOLOGY.md`; read it, do not invent).
2. `docs/PROVIDER_REGISTRY.md`: a "Catalogue additions (2026-11-21, second batch)" section in the
   style of the existing one.
3. `DECISIONS.md`: next free ADR number, status `Proposed`: "Widen the free RPC pool and keep
   Dune and Bitquery catalogue-only". Context: owner review proposed Dune for SQL validation and
   Bitquery behind an adapter during its trial. Decision: no Dune/Bitquery adapter until the owner
   approves a paid plan or trial, because Dune's free plan has no API and Bitquery offers only a
   7-day trial (registry, checked 2026-11-21), and their low-tier licences restrict commercial
   use. Hybrid architecture (own ingestion, indexed providers as enrichment/backup) already
   matches `docs/DATA_ARCHITECTURE.md`.
4. Update `CURRENT_STATE.md` and append to `IMPLEMENTATION_LOG.md` with the real check output.
5. Run the checks; commit.
