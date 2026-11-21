# Provider onboarding checklist (owner)

Status: 2026-11-21. Every id, environment variable and allowance below is taken from `bip/providers/registry.toml` (figures are the registry's `rate_limit` text, checked 2026-11-21 unless marked otherwise). Where the registry says a figure is unverified, this file says so too.

## Ground rules

- **You create every account and key personally.** BIP and Claude never automate signups: accepting a provider's terms of service and passing its CAPTCHA or email checks are your actions (CLAUDE.md section 2.4: no circumvention of auth, CAPTCHAs, paywalls or access controls). Do not create several accounts at one provider to get more quota: Dwellir's terms, for one, prohibit it.
- **Free only.** Skip any step that asks for a card or a paid plan. A "card required" of "not stated" means the registry does not record it: if the signup asks for a card, stop (CLAUDE.md section 2.1).
- **Keys go in `.env` only.** Copy `.env.example` to `.env` (git-ignored) and uncomment the line. A credentialed endpoint is a full URL that embeds the key, so the whole URL is a secret. Never commit it, paste it in an issue, or log it. Settings are not read from `.env` implicitly: run commands with `uv run --env-file .env ...`.
- **Allowances change.** Read the provider's current page when you sign up. A different number is a reason to update the registry, not to trust this file.

## Order of signup

Validation Cloud, Ankr, Alchemy, Helius, Chainbase, Routescan, The Graph, Birdeye, then the rest. The first four give the router independent Ethereum and Solana fallbacks; the next four are catalogue or enrichment sources (no adapter yet).

## Checklist

"Adapter" = the router can call it today. "Catalogue" = recorded only; configuring a key has no effect yet. The docs link is the registry `docs_url`; find the signup or dashboard link from there.

| # | Provider (registry id) | Docs | Free allowance | Card | Env var in `.env` | Value shape | Adapter |
|---|---|---|---|---|---|---|---|
| 1 | Validation Cloud (`validationcloud-eth`, `validationcloud-sol`) | https://docs.validationcloud.io/nodeapi | 50M compute units/month, 120 requests/s shared across the team | No | `BIP_PROVIDER_VALIDATIONCLOUD_ETH_URL`, `BIP_PROVIDER_VALIDATIONCLOUD_SOL_URL` | `https://mainnet.ethereum.validationcloud.io/v1/<KEY>`, `https://mainnet.solana.validationcloud.io/v1/<KEY>` | Yes |
| 2 | Ankr (`ankr-eth`, `ankr-sol`) | https://www.ankr.com/docs/rpc-service/ | About 1,800 requests/minute (Public and Freemium); `eth_getLogs` max 1,000 blocks on Public | Not stated | `BIP_PROVIDER_ANKR_ETH_URL`, `BIP_PROVIDER_ANKR_SOL_URL` | `https://rpc.ankr.com/eth/<KEY>`, `https://rpc.ankr.com/solana/<KEY>` | Yes. Solana may need a paid plan (docs conflict): verify with a probe and skip if it fails |
| 3 | Alchemy (`alchemy-eth`, `alchemy-sol`) | https://www.alchemy.com/docs | 30M CU/month, 300 CU/s (about 15 requests/s); `eth_getLogs` limited to 10 blocks on Free | Not stated | `BIP_PROVIDER_ALCHEMY_ETH_URL`, `BIP_PROVIDER_ALCHEMY_SOL_URL` | `https://eth-mainnet.g.alchemy.com/v2/<KEY>`, `https://solana-mainnet.g.alchemy.com/v2/<KEY>` | Yes (one account serves both) |
| 4 | Helius (`helius-sol`) | https://www.helius.dev/docs/rpc/quickstart | 1M credits/month, 10 RPC requests/s, 2/s for DAS and Enhanced APIs | Not stated | `BIP_PROVIDER_HELIUS_SOL_URL` | `https://mainnet.helius-rpc.com/?api-key=<KEY>` | Yes |
| 5 | Chainbase (`chainbase`) | https://docs.chainbase.com | 200,000 credits/month, Web3 API 2 req/s (lower of two conflicting figures; credit overview says 3 credits/s), SQL API 100 credits/query | No | `BIP_PROVIDER_CHAINBASE_KEY` | Bare key, sent as `X-API-KEY` to `api.chainbase.com` | Catalogue |
| 6 | Routescan (`routescan`) | https://docs.routescan.io | Keyless 2 requests/s and 10,000 calls/day; free key 5 requests/s and 100,000 calls/day. Attribution required unless private use | No | `BIP_PROVIDER_ROUTESCAN_KEY` (optional) | Bare key, sent as `apikey` header to `api.routescan.io` | Catalogue |
| 7 | The Graph (`thegraph`) | https://thegraph.com/docs/en/ | 100,000 queries/month | Not stated | `BIP_PROVIDER_THEGRAPH_KEY` | Bare key | Catalogue |
| 8 | Birdeye (`birdeye`) | https://docs.birdeye.so/ | A free Standard package exists; limits unverified (not shown on the pricing page) | Not stated | `BIP_PROVIDER_BIRDEYE_KEY` | Bare key | Catalogue |
| 9 | Dwellir (`dwellir-eth`) | https://www.dwellir.com/docs | 20 responses/s, 100,000 responses/day; `eth_getLogs` not available on Free | Unknown | `BIP_PROVIDER_DWELLIR_ETH_URL` | `https://api-ethereum-mainnet.n.dwellir.com/<KEY>` | Yes |
| 10 | BlockPI (`blockpi-eth`) | https://docs.blockpi.io/ | 50M request units, 20 requests/s, granted monthly | No (per site FAQ) | `BIP_PROVIDER_BLOCKPI_ETH_URL` | `https://ethereum.blockpi.network/v1/rpc/<KEY>` | Yes |
| 11 | NodeReal MegaNode (`nodereal-eth`) | https://docs.nodereal.io | 10M CU/month, 150 CU/s per key, 3 keys | No | `BIP_PROVIDER_NODEREAL_ETH_URL` | `https://eth-mainnet.nodereal.io/v1/<KEY>` | Yes |
| 12 | Infura (`infura-eth`) | https://docs.infura.io/api/networks/ethereum | 3,000,000 credits/day, 500 credits/s | Not stated | `BIP_PROVIDER_INFURA_ETH_URL` | `https://mainnet.infura.io/v3/<KEY>` | Yes. Solana is limited to select customers: not registered |
| 13 | Etherscan (`etherscan`) | https://docs.etherscan.io/ | 3 calls/s, 100,000 calls/day; personal and research use only | Not stated | `BIP_PROVIDER_ETHERSCAN_KEY` | Bare key | Catalogue |
| 14 | Blockscout PRO (`blockscout`) | https://docs.blockscout.com/devs/pro-api | 100K credits/day, 5 requests/s | Not stated | `BIP_PROVIDER_BLOCKSCOUT_KEY` | Bare key | Catalogue |
| 15 | QuickNode (`quicknode-eth`, `quicknode-sol`) | https://www.quicknode.com/docs/ethereum | A 1-month trial (10M credits, 15 RPS), not permanently free | Not stated | `BIP_PROVIDER_QUICKNODE_ETH_URL`, `BIP_PROVIDER_QUICKNODE_SOL_URL` | `https://<ENDPOINT>.quiknode.pro/<TOKEN>/` | Yes. Trial only: optional |
| 16 | Chainstack (`chainstack`) | https://docs.chainstack.com/ | 3M request units/month, 25 RPS, 1 project | Not stated | `BIP_PROVIDER_CHAINSTACK_URL` | Per-project URL | Catalogue |
| 17 | dRPC (`drpc`) | https://drpc.org/docs | 50M CU per 30 days, 25 RPS, public nodes only | Not stated | `BIP_PROVIDER_DRPC_URL` | Per-key URL | Catalogue |
| 18 | GetBlock (`getblock`) | https://docs.getblock.io/ | 50,000 CU/day, 20 RPS, 2 tokens | Not stated | `BIP_PROVIDER_GETBLOCK_URL` | `https://go.getblock.io/<TOKEN>/` | Catalogue |
| 19 | InfStones (`infstones-eth`) | https://docs.infstones.com | Fast API Free: 30M request cost, 15 requests/s (page may be stale) | Unknown | `BIP_PROVIDER_INFSTONES_ETH_URL` | Unknown: endpoint format unverified | Catalogue |
| 20 | Moralis (`moralis`) | https://docs.moralis.com/ | 40,000 CU/day, 40 RPS | Not stated | `BIP_PROVIDER_MORALIS_KEY` | Bare key | Catalogue |
| 21 | CoinGecko (`coingecko`) | https://docs.coingecko.com/ | Demo: 10,000 calls/month, 100 calls/min, attribution required | Not stated | `BIP_PROVIDER_COINGECKO_KEY` | Bare key | Catalogue |
| 22 | Solscan (`solscan`) | https://docs.solscan.io/ | A free Level 1 key exists; limits unverified | Not stated | `BIP_PROVIDER_SOLSCAN_KEY` | Bare key | Catalogue |
| 23 | GitHub (`github`) | https://docs.github.com/en/rest | 5,000 requests/h with a token (60/h without) | No | `BIP_PROVIDER_GITHUB_TOKEN` | Personal access token | Catalogue |
| 24 | Google BigQuery sandbox (`bigquery`) | https://cloud.google.com/bigquery/docs/sandbox | 1 TiB of queries/month; sandbox needs no billing account | No (sandbox) | `BIP_PROVIDER_GCP_PROJECT` | Project id | Catalogue |

Keyless and needing nothing from you: `publicnode-eth`, `solana-public`, `defillama`, `old-faithful`. `publicnode-sol` is keyless but has no built-in host: set `BIP_PROVIDER_PUBLICNODE_SOL_URL` only after confirming the hostname on publicnode.com, and note its terms forbid bulk extraction.

### Not recommended: no free API

| Provider (registry id) | Why | Where decided |
|---|---|---|
| Dune (`dune`) | The free plan is view-only: no API access. The API needs a 7-day trial or a paid plan from $65/month. | ADR-052 |
| Bitquery (`bitquery`) | No ongoing free tier: a 7-day trial, real-time data only; paid plans from $29/month billed annually. | ADR-052 |

Do not configure either. They stay catalogue-only until you approve a paid plan or trial.

## After you add a key: verify it

1. Put the URL or key in `.env`, then run the budgeted capability probe on a machine with internet access:

   ```
   BIP_LIVE_RPC=1 uv run --env-file .env python -m bip.tools.probe
   ```

   `make probe` runs the same tool without `--env-file`, so it sees only variables already exported in your shell (export them first, or use the command above). The probe is real network and opt-in, at most 8 calls per provider, 48 per run and 90 seconds (`docs/PROVIDER_CAPABILITY_METHODOLOGY.md`). A provider whose variable is unset is reported as "requires configuration", not as down.
2. Read the result per provider. `unsupported` or a documented-versus-observed difference is a fact to record. Probe results are never written back into the registry: update the registry by hand, with source and date, if a documented figure was wrong.
3. If a provider fails (for example Ankr Solana on the free plan), leave it unconfigured. Missing is not zero: a failed provider is simply not used.
4. Never share the probe output unredacted if it echoes a URL: the URL contains your key.
