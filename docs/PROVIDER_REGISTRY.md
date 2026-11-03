# Provider Registry — Candidate Inventory (Deliverable C)

Status: **Research snapshot, 2026-11-03.**

## Stage 2 status (2026-11-03)

The machine-readable registry now exists: `bip/providers/registry.toml`, loaded by `bip/providers/registry.py`. This table records what is *implemented* and what was *actually tested*.

| Provider id | Chain | Adapter | Credentials | Endpoint in registry | Real request tested | Notes |
|---|---|---|---|---|---|---|
| `publicnode-eth` | Ethereum | Yes | No | `https://ethereum-rpc.publicnode.com` | **Failed: host blocked by the sandbox network policy** (no answer obtained) | Hostname from a third-party monitor plus the ethereum.org listing |
| `solana-public` | Solana | Yes | No | `https://api.mainnet.solana.com` (current official docs) | **Failed: host blocked by the sandbox network policy** | Official limit 100 req/10 s per IP |
| `publicnode-sol` | Solana | Yes | No | None: sources disagree on the hostname | No | Set `BIP_PROVIDER_PUBLICNODE_SOL_URL` after confirming it |
| `ankr-eth` | Ethereum | Yes | Yes (treated as) | Via env | No | Bare keyless URL unconfirmed |
| `alchemy-eth`, `alchemy-sol` | Ethereum, Solana | Yes | Yes | Via env | No | Free account needed |
| `infura-eth` | Ethereum | Yes | Yes | Via env | No | Free account needed |
| `quicknode-eth`, `quicknode-sol` | Ethereum, Solana | Yes | Yes | Via env | No | Free tier is a trial |
| `helius-sol` | Solana | Yes | Yes | Via env | No | Free account needed |
| `etherscan`, `bigquery`, `old-faithful`, `thegraph`, `defillama`, `github` | various | **No** | varies | n/a | n/a | Registered only |

**Methods exposed through adapters.**
- Ethereum: `eth_chainId`, `eth_blockNumber`, `eth_getBlockByNumber`, `eth_getBalance`, `eth_getTransactionByHash`, `eth_getTransactionReceipt`, `eth_getLogs`, `eth_getTransactionCount`.
- Solana: `getHealth`, `getGenesisHash`, `getSlot`, `getBlock`, `getBalance`, `getSignaturesForAddress`, `getTransaction`.

**Not yet confirmed for any provider:**
- archive depth;
- `eth_getLogs` range limits (the code adapts when a provider reports one);
- Solana history depth.

**Endpoint hostnames, rechecked by search on 2026-11-03:**
- PublicNode is listed on ethereum.org. The `ethereum-rpc.publicnode.com` hostname was confirmed only by a third-party monitor (main.net), which observed `eth_chainId` returning 0x1.
- Solana's current docs use `api.mainnet.solana.com`; older docs use `api.mainnet-beta.solana.com`.
- These URL formats come from the providers' docs as quoted in search: Infura `https://mainnet.infura.io/v3/<KEY>`, Helius `https://mainnet.helius-rpc.com/?api-key=<KEY>`.
- These come from third-party sources: Alchemy `https://eth-mainnet.g.alchemy.com/v2/<KEY>` and `https://solana-mainnet.g.alchemy.com/v2/<KEY>`, and Ankr `https://rpc.ankr.com/eth/<KEY>`.

## 0. How to read this document

**Verification method used on 2026-11-03.** This session's network policy blocked direct fetches of provider websites (DNS resolution failed for `alchemy.com`, `helius.dev`, `ankr.com`, `drpc.org`, `infura.io`). Figures below come from web-search results that quoted official documentation pages. Each figure is graded:

| Grade | Meaning |
|---|---|
| **V-search** | Quoted from an official provider page via search on 2026-11-03; not fetched directly |
| **Conflict** | Official sources disagree with each other; the more recent official figure is listed, with the alternative noted |
| **3rd-party** | Only third-party sources found; do not rely on it |
| **Unknown** | Not determined; must be probed (roadmap Stage 4) |

**Rule:** No figure here is entered in the machine-readable registry (`config/providers/*.yaml`, Stage 2) as `verified` until someone re-checks the official page directly and records `verified_at` + `source_url`. The router treats limits as *ceilings to stay well under*, and learns real limits from 429s and headers.

Free tiers change often. Several figures dropped sharply between 2024 and 2026 (Alchemy 300M→30M CU/mo, Infura 6M→3M credits/day, QuickNode 50M→10M credits). Plan for further reductions.

## 1. RPC / node-access providers

| Provider | Networks relevant to BIP | Free quota | Rate limit (free) | Archive / history | WebSocket | Auth | Notes / restrictions | Grade | Sources |
|---|---|---|---|---|---|---|---|---|---|
| **Alchemy** | ETH, Base, Arbitrum, BNB(?), Solana | 30M CU/month (account-wide) | Conflict: 25 RPS / 500 CU/s on pricing page; 300–330 CUPS in support/docs | Archive on ETH: Unknown for free tier (historically included); verify | Yes (Unknown for free tier limits) | API key | Older docs still say 300M CU; treat as outdated. CU cost varies by method (`eth_getLogs` is expensive) | Conflict | [pricing](https://www.alchemy.com/pricing), [free tier details](https://www.alchemy.com/support/free-tier-details), [pricing plans](https://docs.alchemy.com/reference/pricing-plans) |
| **Infura (MetaMask)** | ETH, Base, Arbitrum, others | 3,000,000 credits/day | 500 credits/s | Archive: Unknown on free tier | Yes | API key | Older page said 6M/day, 2,000 credits/s — outdated | Conflict | [docs pricing](https://docs.infura.io/get-started/pricing/), [metamask docs](https://docs.metamask.io/services/get-started/pricing/) |
| **dRPC** | ETH, Solana, Base, Arbitrum, BNB, many | 210M CU / 30 days, public nodes only | ~120,000 CU/min per IP (≈100 `eth_call`/s); some networks custom | Unknown (public-node pool; varies by node) | Unknown | Optional key (≤5 keys) | **trace, debug, filter methods disabled on free tier** | V-search | [pricing/requests](https://drpc.org/docs/pricing/requests), [rate limiting](https://drpc.org/docs/howitworks/ratelimiting), [2025 change](https://blog.drpc.org/upcoming-changes-to-drpcs-free-plan-effective-june-1-2025/) |
| **Ankr** | 40+ chains incl. ETH, Solana, BNB, Base, Arbitrum | Public: no monthly figure; Freemium: 200M API credits/month | ≈1,800 requests/min across endpoints (guaranteed; more possible under low load) | Unknown | Unknown | Public: none; Freemium: account | Advanced API (token/NFT indexer) availability on free: Unknown | V-search | [service plans](https://www.ankr.com/docs/rpc-service/service-plans) |
| **QuickNode** | ETH, Solana, many | 10M API credits | 15 RPS | Unknown | Yes | Account | Described as a **"Free trial — 1 month free"**; may not be permanently free. Not a baseline provider | V-search | [pricing](https://www.quicknode.com/pricing) |
| **Chainstack** | ETH, Solana, Base, BNB, Arbitrum | 3M request units/month | 25 RPS | Archive requests cost 2 RU (included in quota) | Yes | Account | Archive on free tier makes this valuable for historical state checks | V-search | [pricing](https://chainstack.com/pricing/) |
| **Helius** | Solana only | 1M credits/month | 10 RPC req/s; 1 `sendTransaction`/s | Solana historical `getTransaction` / `getSignaturesForAddress`: Unknown depth on free tier | Yes (standard WS) | API key | Effective 2026-04-07 per billing docs. DAS / enhanced APIs consume credits | V-search | [billing plans](https://helius.dev/docs/billing/plans), [rate limits](https://docs.helius.xyz/docs/billing/rate-limits.md), [credits](https://docs.helius.xyz/docs/billing/credits.md) |
| **PublicNode (Allnodes)** | ETH, Solana, Base, BNB, Arbitrum | No published quota | No single published limit; per-IP, varies by chain/transport | Unknown; likely non-archive for most chains | Yes (per-chain WS URLs) | None | Only 3rd-party descriptions found | 3rd-party | [publicnode.com](https://www.publicnode.com/) (not fetched) |
| **Solana Foundation public RPC** (`api.mainnet-beta.solana.com`) | Solana | None | Reported ~100 req / 10 s per IP; heavier methods stricter | Limited history | Yes | None | Officially not for production use. Only 3rd-party figures found | 3rd-party | [Solana clusters docs](https://solana.com/docs/references/clusters) (not fetched) |
| **Triton / ERPC Old Faithful-backed RPC** | Solana history | Unknown | Unknown | Full history (via Old Faithful) | — | Unknown | ERPC announced historical RPC from Old Faithful (2025-12) | 3rd-party | [ERPC note](https://slv.dev/en/news/2025/12/29/erpc-solana-rpc-historical-data-upgrade) |

**Important Ethereum caveat — EIP-4444 partial history expiry.** Since July 2025 all execution clients support dropping pre-Merge block bodies and receipts (blocks < 15,537,394). Providers may stop serving `eth_getBlockByNumber` bodies / receipts for pre-Merge blocks. The EF advises historical indexers to use external history sources. BIP must therefore probe each provider's pre-Merge coverage, and must not assume it. Sources: [EF blog 2025-07-08](https://blog.ethereum.org/2025/07/08/partial-history-exp), [ethresear.ch era1 torrent (~427 GB, blocks 0–15,537,393)](https://ethresear.ch/t/torrents-and-eip-4444/19788).

## 2. Bulk / historical datasets

| Source | Chains | What it has | Freshness | Free access | Limits | Grade | Sources |
|---|---|---|---|---|---|---|---|
| **BigQuery public `bigquery-public-data.crypto_ethereum`** (community) | ETH | blocks, transactions, logs, token_transfers, traces, contracts, tokens, balances (current only) | Historically daily-ish; verify | BigQuery sandbox: **1 TiB query/month, 10 GiB storage, tables expire in 60 days**, no credit card | Full-table scans are multi-TB → a single careless query can exhaust the month. Exports to GCS need billing; GCS egress is paid | V-search | [sandbox](https://cloud.google.com/bigquery/docs/sandbox), [pricing](https://cloud.google.com/bigquery/pricing) |
| **BigQuery `goog_blockchain_ethereum_mainnet_us`** (Google-managed) | ETH | Native data model, curated event tables; Google says storage is not charged, only queries | Managed; verify | Same query quota | Same | V-search | [Blockchain Analytics overview](https://docs.cloud.google.com/blockchain-analytics/docs/overview) |
| **BigQuery `crypto_solana_mainnet_us`** | Solana | Transactions (nested token transfers), blocks, instructions, accounts (verify tables) | **Unreliable:** community report of no updates after 2025-03-31; a later report shows several-day lag (Nov 2025) | Same quota | Monthly → daily partitions migration reported; must check partitioning before querying | Conflict | [Google dev forum thread](https://discuss.google.dev/t/public-solana-bigquery-dataset-crypto-solana-mainnet-us-stopped-updating-on-march-31-2025/185629), [Token Terminal note](https://tokenterminal.beehiiv.com/p/token-terminal-x-google) |
| **Old Faithful** (Triton, Solana Foundation funded) | Solana | Complete ledger from genesis: every block and transaction, one CAR file per epoch | Ongoing per epoch | Free CDN (`files.old-faithful.net`) and Filecoin | **Size ≈ hundreds of TB** (Triton: "over 400 TB" of block/tx data). Per-epoch CARs are still very large | V-search | [docs](https://docs.old-faithful.net/), [Triton walkthrough](https://blog.triton.one/self-hosting-solana-history-step-by-step-walkthrough-for-backfill/), [Triton archive docs](https://docs.triton.one/chains/solana/old-faithful-historical-archive-1) |
| **Ethereum era1 files** | ETH pre-Merge | Block bodies + receipts, blocks 0–15,537,393 | Static | Torrent / mirrors | ~427 GB (as proposed) | 3rd-party/V-search | [ethresear.ch](https://ethresear.ch/t/torrents-and-eip-4444/19788) |
| **cryo** (Paradigm, OSS tool, not a dataset) | EVM | Extracts blocks/txs/logs/traces etc. to Parquet via JSON-RPC, chunked (default 1,000 blocks/file) | n/a | OSS | Consumes RPC quota; `--dry` estimates work | V-search | [cryo repo](https://github.com/paradigmxyz/cryo) |
| **Dune** | Many | Decoded/curated tables | Fresh | Free: 2,500 credits/month; **official FAQ: free tier is manual editor only — no API/scheduled execution** | Not usable as an automated source on free tier | Conflict | [pricing FAQ](https://docs.dune.com/learning/how-tos/pricing-faqs) |

## 3. Indexers and data APIs

| Source | Use in BIP | Free tier | Grade | Sources |
|---|---|---|---|---|
| **Etherscan API V2** (multichain, one key) | Contract creation (deployer + creation tx), verified source/ABI, labels not available via API | 3 calls/s, 100,000 calls/day, **selected chains only** (older page: 5/s) | Conflict | [rate limits](https://docs.etherscan.io/resources/rate-limits), [pricing](https://etherscan.io/apis) |
| **The Graph (Subgraph Studio / network)** | Pre-indexed DEX/lending subgraphs (e.g. Uniswap) | 100,000 queries/month free; then $2/100k (requires card) | V-search | [pricing](https://thegraph.com/pricing), [billing docs](https://thegraph.com/docs/en/billing/) |
| **DefiLlama** | TVL, fees/revenue, protocol metadata, prices | Free public endpoints, no key; free rate limit **not officially stated**; premium endpoints paid | Conflict | [pro-api](https://defillama.com/pro-api) |
| **Helius DAS / enhanced** | Solana token metadata, parsed transactions | Inside Helius 1M credits/month | V-search | see Helius above |
| **Solscan / Birdeye / CoinGecko** | Solana token data, prices | Unknown — research in Stage 4 | Unknown | — |

## 4. Web intelligence sources

| Source | Access method | Limit | Grade | Sources |
|---|---|---|---|---|
| **GitHub REST** | Official API, PAT | 5,000 req/h authenticated; 60 req/h unauthenticated (per IP); secondary: 900 points/min REST | V-search | [REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) |
| **GitHub GraphQL** | Official API | Separate primary limit (3rd-party: 5,000 points/h); secondary 2,000 points/min | Partly V-search | same |
| Governance forums (Discourse) | Discourse JSON API / RSS (`/latest.json`, `.rss`) | Per-site | Unknown | — |
| Snapshot governance | Public GraphQL hub | Unknown | Unknown | — |
| Exchange announcements | Official RSS/API only; else robots-compliant fetch | Per-site | Unknown | — |
| Project websites/docs | robots-compliant fetch, conditional GET (ETag/Last-Modified) | Per-domain budget (default ≤ 1 req / 10 s) | Policy | — |

## 5. Recommended baseline provider set (Proposed, ADR-007)

**Ethereum live + recent:** PublicNode, dRPC, Ankr public, Infura, Alchemy, Chainstack (archive spot-checks). Etherscan V2 for contract-creation metadata.
**Ethereum deep history:** BigQuery (targeted, partition-pruned queries only) + RPC `eth_getLogs` ranges for scoped contracts; era1 only if separate storage is approved.
**Solana live:** Helius (primary WS), PublicNode, dRPC, Solana public RPC, Alchemy Solana.
**Solana history:** RPC `getSignaturesForAddress` + `getTransaction` for scoped programs/accounts; Old Faithful per-epoch, scoped extraction only when separate storage is approved; BigQuery Solana only after verifying freshness.

Accounts needing sign-up (free, no card): Alchemy, Infura, Chainstack, Helius, Etherscan, GitHub PAT, Google Cloud (sandbox). **Creating these accounts is an owner action.**

## 6. Machine-readable registry schema (to implement in Stage 2)

```yaml
id: drpc-eth
provider: drpc
chain: ethereum
network: mainnet
transport: [https, wss]
endpoint_env: BIP_DRPC_ETH_URL        # URL comes from env, never committed
auth: optional_key
quota:
  - {kind: compute_units, window: 30d, limit: 210_000_000, scope: account, grade: V-search}
  - {kind: compute_units, window: 60s, limit: 120_000, scope: ip, grade: V-search}
method_costs: {}                      # per-method cost in provider units; filled only from the provider's official cost table
default_method_cost: null             # null = unknown -> router uses conservative assumption and learns from usage headers
capabilities:
  methods_supported: [eth_blockNumber, eth_getBlockByNumber, eth_getTransactionReceipt, eth_getLogs]
  methods_blocked: [trace_*, debug_*, eth_newFilter]
  max_logs_block_range: unknown        # learned by probe
  history_floor_block: unknown         # learned by probe (EIP-4444!)
  archive_state: unknown
terms:
  commercial_use: unknown
  source_url: https://drpc.org/docs/pricing/requests
  verified_at: null                    # set only after direct verification
```
