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
