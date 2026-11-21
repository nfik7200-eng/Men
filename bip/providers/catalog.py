"""Compatibility view of the provider registry (BIP 0.1 API).

BIP 0.1 kept static provider metadata here. Stage 2 moved it to ``registry.toml`` loaded by
:mod:`bip.providers.registry`; this module now only derives the old shape from the registry so
existing imports keep working.
"""

from __future__ import annotations

from dataclasses import dataclass

from bip.providers.registry import default_registry


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    id: str
    name: str
    kind: str
    chains: tuple[str, ...]
    credential_env: str | None
    docs_url: str


CATALOG: tuple[ProviderInfo, ...] = tuple(
    ProviderInfo(s.id, s.name, s.kind, s.chains, s.endpoint_env, s.docs_url)
    for s in default_registry().all()
)
