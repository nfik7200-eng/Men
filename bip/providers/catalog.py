"""Static catalog of candidate data providers, taken from ``docs/PROVIDER_REGISTRY.md``.

This is descriptive metadata only. **No provider adapter exists yet**, so nothing here has ever
connected to a provider, and :func:`provider_status` reports every provider as unavailable.
Configuration is detected by the *presence* of an environment variable; its value is never read,
returned or logged. The machine-readable registry with verified limits is Stage 2 work.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

SourceKind = Literal["rpc", "dataset", "indexer_api", "web"]
ResearchGrade = Literal["V-search", "Conflict", "3rd-party"]
ConfigurationStatus = Literal["not_configured", "configured", "not_required"]


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    id: str
    name: str
    kind: SourceKind
    chains: tuple[str, ...]
    credential_env: str | None
    """Environment variable that will hold the endpoint URL or API key. ``None``: none needed."""
    research_grade: ResearchGrade
    docs_url: str


CATALOG: tuple[ProviderInfo, ...] = (
    ProviderInfo(
        "alchemy",
        "Alchemy",
        "rpc",
        ("ethereum", "solana"),
        "BIP_PROVIDER_ALCHEMY_KEY",
        "Conflict",
        "https://www.alchemy.com/pricing",
    ),
    ProviderInfo(
        "infura",
        "Infura",
        "rpc",
        ("ethereum",),
        "BIP_PROVIDER_INFURA_KEY",
        "Conflict",
        "https://docs.infura.io/get-started/pricing/",
    ),
    ProviderInfo(
        "drpc",
        "dRPC",
        "rpc",
        ("ethereum", "solana"),
        "BIP_PROVIDER_DRPC_KEY",
        "V-search",
        "https://drpc.org/docs/pricing/requests",
    ),
    ProviderInfo(
        "ankr",
        "Ankr (public)",
        "rpc",
        ("ethereum", "solana"),
        None,
        "V-search",
        "https://www.ankr.com/docs/rpc-service/service-plans",
    ),
    ProviderInfo(
        "chainstack",
        "Chainstack",
        "rpc",
        ("ethereum", "solana"),
        "BIP_PROVIDER_CHAINSTACK_URL",
        "V-search",
        "https://chainstack.com/pricing/",
    ),
    ProviderInfo(
        "helius",
        "Helius",
        "rpc",
        ("solana",),
        "BIP_PROVIDER_HELIUS_KEY",
        "V-search",
        "https://helius.dev/docs/billing/plans",
    ),
    ProviderInfo(
        "publicnode",
        "PublicNode",
        "rpc",
        ("ethereum", "solana"),
        None,
        "3rd-party",
        "https://www.publicnode.com/",
    ),
    ProviderInfo(
        "solana-public",
        "Solana public RPC",
        "rpc",
        ("solana",),
        None,
        "3rd-party",
        "https://solana.com/docs/references/clusters",
    ),
    ProviderInfo(
        "etherscan",
        "Etherscan API V2",
        "indexer_api",
        ("ethereum",),
        "BIP_PROVIDER_ETHERSCAN_KEY",
        "Conflict",
        "https://docs.etherscan.io/resources/rate-limits",
    ),
    ProviderInfo(
        "bigquery",
        "Google BigQuery public datasets",
        "dataset",
        ("ethereum", "solana"),
        "BIP_PROVIDER_GCP_PROJECT",
        "V-search",
        "https://cloud.google.com/bigquery/docs/sandbox",
    ),
    ProviderInfo(
        "old-faithful",
        "Old Faithful archive",
        "dataset",
        ("solana",),
        None,
        "V-search",
        "https://docs.old-faithful.net/",
    ),
    ProviderInfo(
        "thegraph",
        "The Graph",
        "indexer_api",
        ("ethereum",),
        "BIP_PROVIDER_THEGRAPH_KEY",
        "V-search",
        "https://thegraph.com/pricing",
    ),
    ProviderInfo(
        "defillama",
        "DefiLlama",
        "indexer_api",
        ("ethereum", "solana"),
        None,
        "Conflict",
        "https://defillama.com/pro-api",
    ),
    ProviderInfo(
        "github",
        "GitHub API",
        "web",
        (),
        "BIP_PROVIDER_GITHUB_TOKEN",
        "V-search",
        "https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api",
    ),
)


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    info: ProviderInfo
    configuration_status: ConfigurationStatus
    adapter_implemented: bool
    connection_status: Literal["unavailable"]
    status_reason: str
    last_check_at: datetime | None
    last_success_at: datetime | None


def provider_status(
    checks: Mapping[str, tuple[datetime | None, datetime | None, str | None]],
    environ: Mapping[str, str] | None = None,
) -> list[ProviderStatus]:
    """Status of every catalogued provider. Reads only whether variables are set."""
    env = os.environ if environ is None else environ
    out: list[ProviderStatus] = []
    for info in CATALOG:
        if info.credential_env is None:
            config: ConfigurationStatus = "not_required"
        elif env.get(info.credential_env, ""):
            config = "configured"
        else:
            config = "not_configured"
        last_check, last_success, _ = checks.get(info.id, (None, None, None))
        out.append(
            ProviderStatus(
                info=info,
                configuration_status=config,
                adapter_implemented=False,
                connection_status="unavailable",
                status_reason="No adapter implemented yet (roadmap Stages 2-4); never contacted.",
                last_check_at=last_check,
                last_success_at=last_success,
            )
        )
    return out
