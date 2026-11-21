"""Registry entries added by the free-provider expansion (checked 2026-11-21)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from bip.providers.registry import (
    READ_ONLY_METHODS,
    ProviderSpec,
    default_registry,
    host_allowed,
    resolve_endpoint,
)

ADAPTER_IDS = (
    "validationcloud-eth",
    "validationcloud-sol",
    "ankr-sol",
    "dwellir-eth",
    "blockpi-eth",
    "nodereal-eth",
)
CATALOGUE_IDS = ("infstones-eth",)
NEW_IDS = ADAPTER_IDS + CATALOGUE_IDS
SEARCH_DERIVED = set(NEW_IDS)


def _spec(provider_id: str) -> ProviderSpec:
    return default_registry().get(provider_id)


@pytest.mark.parametrize("provider_id", NEW_IDS)
def test_new_entry_is_documented_and_read_only(provider_id: str) -> None:
    spec = _spec(provider_id)
    assert spec.documentation_checked == "2026-11-21"
    assert spec.sources
    assert set(spec.methods) <= READ_ONLY_METHODS[spec.protocol]
    expected = "search_derived" if provider_id in SEARCH_DERIVED else "official_docs"
    assert spec.documentation_basis == expected
    assert spec.kind == "rpc"


@pytest.mark.parametrize("provider_id", ADAPTER_IDS)
def test_adapter_entry_shape(provider_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec(provider_id)
    assert spec.has_adapter
    assert spec.endpoint_env is not None
    assert spec.endpoint_env.startswith("BIP_PROVIDER_")
    assert spec.allowed_hosts
    assert spec.methods
    assert spec.infrastructure
    assert spec.client_rps is not None
    assert spec.client_rps <= 4.0
    monkeypatch.delenv(spec.endpoint_env, raising=False)
    assert resolve_endpoint(spec).configuration == "not_configured"


@pytest.mark.parametrize("provider_id", CATALOGUE_IDS)
def test_catalogue_entry_has_no_adapter(provider_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec(provider_id)
    assert spec.protocol == "none"
    assert spec.methods == ()
    assert spec.endpoint_env is not None
    monkeypatch.delenv(spec.endpoint_env, raising=False)
    assert resolve_endpoint(spec).configuration == "not_configured"


def test_dwellir_free_plan_excludes_get_logs() -> None:
    assert "eth_getLogs" not in _spec("dwellir-eth").methods


def test_skipped_providers_are_absent() -> None:
    ids = {s.id for s in default_registry().all()}
    assert "dwellir-sol" not in ids
    assert "infura-sol" not in ids


def test_new_entries_do_not_share_infrastructure_with_other_operators() -> None:
    reg = default_registry()
    for pid in ADAPTER_IDS:
        spec = reg.get(pid)
        for other in reg.all():
            if other.id == pid or other.infrastructure is None:
                continue
            if other.operator != spec.operator:
                assert other.infrastructure != spec.infrastructure, (pid, other.id)


def test_ankr_sol_shares_group_only_with_ankr() -> None:
    assert _spec("ankr-sol").infrastructure == _spec("ankr-eth").infrastructure


INDEXER_IDS = ("chainbase", "routescan")


@pytest.mark.parametrize("provider_id", INDEXER_IDS)
def test_indexer_entry_is_catalogue_only(provider_id: str) -> None:
    spec = _spec(provider_id)
    assert spec.kind == "indexer_api"
    assert spec.has_adapter is False
    assert spec.methods == ()
    assert spec.sources
    assert spec.documentation_checked == "2026-11-21"
    assert spec.documentation_basis == "search_derived"


def test_indexer_chain_coverage_is_conservative() -> None:
    assert _spec("chainbase").chains == ("ethereum",)
    assert _spec("routescan").chains == ("ethereum",)
    assert "unverified" in (_spec("chainbase").notes or "")


def test_routescan_records_keyless_and_keyed_limits() -> None:
    spec = _spec("routescan")
    assert spec.endpoint_env == "BIP_PROVIDER_ROUTESCAN_KEY"
    limit = spec.rate_limit or ""
    assert "2 requests/s" in limit
    assert "5 requests/s" in limit
    assert "Powered by Routescan.io" in (spec.notes or "")


def test_infura_solana_finding_is_recorded_on_infura_eth() -> None:
    spec = _spec("infura-eth")
    assert "select customers" in (spec.notes or "")
    assert "https://docs.infura.io/get-started/endpoints" in spec.sources


def test_env_example_placeholder_hosts_are_allowed() -> None:
    env_text = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    placeholders = dict(re.findall(r"^# (BIP_PROVIDER_\w+)=(https?://\S+)", env_text, re.M))
    for pid in ADAPTER_IDS:
        spec = _spec(pid)
        assert spec.endpoint_env is not None
        url = placeholders.get(spec.endpoint_env)
        assert url is not None, spec.endpoint_env
        host = urlsplit(url).hostname
        assert host is not None
        assert host_allowed(host, spec.allowed_hosts), (pid, host)
