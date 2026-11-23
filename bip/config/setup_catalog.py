"""Settings an administrator may change from the Setup page (ADR-056).

Only these dotted keys are ever read from the setup store; anything else in the file is ignored
and reported. Network binding, authentication, storage paths and limits stay environment-only:
changing them from a browser could lock the operator out or expose the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["bool", "int", "text", "choice", "list"]


@dataclass(frozen=True, slots=True)
class EditableSetting:
    key: str
    """Dotted path: ``section.field``, or ``field`` for a top-level setting."""
    group: str
    label: str
    kind: Kind
    help: str
    choices: tuple[str, ...] = ()
    contacts_providers: bool = False
    """True when enabling it makes BIP spend provider quota on its own schedule."""

    @property
    def env_name(self) -> str:
        return "BIP_" + self.key.upper().replace(".", "__")


_JOBS = "Background jobs"
_ALERTS = "Alerts"
_AI = "AI research"
_DATA = "Data"
_WEB = "Web feeds"
_SOURCES = "Data sources"

EDITABLE: tuple[EditableSetting, ...] = (
    EditableSetting(
        "data_origin",
        _DATA,
        "What this database holds",
        "choice",
        "Applied only to a database without a marker. Review decisions become ML ground truth "
        "only on live_network data.",
        choices=("live_network", "offline_fixture"),
    ),
    EditableSetting(
        "alerts.telegram_enabled",
        _ALERTS,
        "Send alerts to Telegram",
        "bool",
        "Needs the bot token (below) and a chat id.",
    ),
    EditableSetting(
        "alerts.telegram_chat_id",
        _ALERTS,
        "Telegram chat id",
        "text",
        "A numeric chat id (groups start with -100) or an @channel name.",
    ),
    EditableSetting(
        "alerts.max_attempts", _ALERTS, "Delivery attempts", "int", "Attempts before giving up."
    ),
    EditableSetting(
        "ai.provider",
        _AI,
        "AI provider",
        "choice",
        "none keeps research deterministic. ollama talks to a local Ollama only.",
        choices=("none", "ollama", "openai_compatible"),
    ),
    EditableSetting(
        "ai.ollama_url",
        _AI,
        "Ollama URL",
        "text",
        "Must be a loopback address, for example http://127.0.0.1:11434.",
    ),
    EditableSetting(
        "ai.ollama_model",
        _AI,
        "Ollama model",
        "text",
        "An installed model tag; BIP never pulls models.",
    ),
    EditableSetting(
        "ai.api_base_url", _AI, "OpenAI-compatible base URL", "text", "An https endpoint."
    ),
    EditableSetting("ai.api_model", _AI, "OpenAI-compatible model", "text", "Model name."),
    EditableSetting(
        "workers.enabled",
        _JOBS,
        "Run the background scheduler",
        "bool",
        "Master switch. Local-only jobs (discovery over stored data, maintenance) start with it.",
    ),
    EditableSetting(
        "workers.discovery_interval_minutes",
        _JOBS,
        "Discovery interval (minutes)",
        "int",
        "How often discovery runs over stored data.",
    ),
    EditableSetting("workers.backup_enabled", _JOBS, "Scheduled backups", "bool", "Local only."),
    EditableSetting(
        "workers.memory_enabled", _JOBS, "Refresh intelligence memory", "bool", "Local only."
    ),
    EditableSetting(
        "workers.expansion_enabled", _JOBS, "Relationship expansion", "bool", "Local only."
    ),
    EditableSetting(
        "workers.collector_enabled",
        _JOBS,
        "Ethereum block collector",
        "bool",
        "Reads finalized blocks for new contracts, pools and large transfers.",
        contacts_providers=True,
    ),
    EditableSetting(
        "workers.solana_collector_enabled",
        _JOBS,
        "Solana slot collector",
        "bool",
        "Reads finalized slots for new mints, programs and pools.",
        contacts_providers=True,
    ),
    EditableSetting(
        "workers.watchlist_refresh_enabled",
        _JOBS,
        "Refresh watchlist wallets",
        "bool",
        "Re-ingests watched wallets on a schedule.",
        contacts_providers=True,
    ),
    EditableSetting(
        "workers.ethereum_backfill_enabled",
        _JOBS,
        "Ethereum history backfill",
        "bool",
        "Walks back through older blocks within budget.",
        contacts_providers=True,
    ),
    EditableSetting(
        "workers.solana_backfill_enabled",
        _JOBS,
        "Solana history backfill",
        "bool",
        "Walks back through older slots within budget.",
        contacts_providers=True,
    ),
    EditableSetting(
        "workers.gap_recovery_enabled",
        _JOBS,
        "Gap recovery",
        "bool",
        "Re-reads recorded coverage gaps.",
        contacts_providers=True,
    ),
    EditableSetting(
        "workers.prices_enabled",
        _JOBS,
        "Refresh prices (CoinGecko)",
        "bool",
        "Native ETH/SOL and the tokens listed under Data sources, on a schedule.",
        contacts_providers=True,
    ),
    EditableSetting(
        "workers.dune_enabled",
        _JOBS,
        "Refresh Dune query results",
        "bool",
        "Fetches the latest results of your saved Dune queries (needs a Dune plan).",
        contacts_providers=True,
    ),
    EditableSetting(
        "sources.coingecko_plan",
        _SOURCES,
        "CoinGecko plan",
        "choice",
        "keyless works without a key at low limits; demo sends your free Demo key; pro uses the "
        "paid Pro host and key.",
        choices=("keyless", "demo", "pro"),
    ),
    EditableSetting(
        "sources.price_tokens",
        _SOURCES,
        "Tokens to price",
        "list",
        "One per line as <coingecko platform>:<contract address>, for example "
        "ethereum:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.",
    ),
    EditableSetting(
        "sources.dune_query_ids",
        _SOURCES,
        "Dune query ids",
        "list",
        "Saved query ids (numbers) whose latest results BIP fetches.",
    ),
    EditableSetting(
        "sources.daily_request_cap",
        _SOURCES,
        "Daily request cap per integration",
        "int",
        "BIP stops calling an integration for the rest of the UTC day at this count.",
    ),
    EditableSetting(
        "workers.lake_export_enabled",
        _JOBS,
        "Export to the Parquet lake",
        "bool",
        "Copies newly stored transactions and transfers into Silver Parquet files. Local only.",
    ),
    EditableSetting(
        "workers.web_feeds_enabled",
        _JOBS,
        "Fetch web feeds",
        "bool",
        "Fetches the feeds listed under Web feeds on a schedule (robots.txt honoured).",
        contacts_providers=True,
    ),
    EditableSetting(
        "web.feeds",
        _WEB,
        "Feeds to follow",
        "list",
        "https RSS or Atom feed URLs, one per line: project blogs, governance forums, GitHub "
        "releases (https://github.com/<owner>/<repo>/releases.atom).",
    ),
    EditableSetting(
        "web.contact",
        _WEB,
        "Contact in the User-Agent",
        "text",
        "An e-mail or URL so site operators can reach you about BIP's requests.",
    ),
    EditableSetting(
        "ml.continuous_training_enabled",
        _JOBS,
        "Continuous model retraining",
        "bool",
        "Retrains challengers behind the promotion gate. Local only.",
    ),
    EditableSetting(
        "collector.token_allowlist",
        _DATA,
        "ERC-20 tokens to store transfers for",
        "list",
        "Contract addresses, one per line.",
    ),
    EditableSetting(
        "solana_collector.spl_mint_allowlist",
        _DATA,
        "SPL mints to store transfers for",
        "list",
        "Mint addresses, one per line.",
    ),
)

BY_KEY = {s.key: s for s in EDITABLE}
