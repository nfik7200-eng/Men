"""Registry entries added by the free-provider expansion (checked 2026-11-21)."""

from __future__ import annotations

import pytest

from bip.providers.registry import (
    READ_ONLY_METHODS,
    ProviderSpec,
    default_registry,
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
SEARCH_DERIVED = {"ankr-sol", "dwellir-eth", "blockpi-eth", "nodereal-eth", "infstones-eth"}


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
