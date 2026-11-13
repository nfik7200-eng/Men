from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from bip.core.exceptions import UnsafeURLError
from bip.providers.registry import (
    READ_ONLY_METHODS,
    RegistryError,
    ResolvedEndpoint,
    default_registry,
    host_allowed,
    load_registry,
    resolve_endpoint,
)
from bip.providers.state import ProviderState
from bip.providers.status import compute_view
from bip.rpc.jsonrpc import (
    RpcError,
    RpcRequest,
    classify_error,
    classify_http_status,
    parse_response,
)
from bip.rpc.transport import HttpsTransport, TransportError

REQ = RpcRequest(7, "eth_chainId", [])


# ---------- registry ----------


def test_registry_loads_and_is_read_only() -> None:
    reg = default_registry()
    ids = {s.id for s in reg.all()}
    assert {"publicnode-eth", "solana-public", "helius-sol", "alchemy-eth"} <= ids
    for spec in reg.all():
        assert set(spec.methods) <= READ_ONLY_METHODS[spec.protocol]
        assert not any(
            m.lower().startswith(("eth_send", "eth_sign", "send", "requestairdrop"))
            for m in spec.methods
        )
    assert [s.id for s in reg.rpc_providers("solana")][:1] == ["solana-public"]
    assert reg.get("solana-public").archive is None  # unknown, not guessed
    assert reg.get("publicnode-eth").max_log_block_range is None


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "r.toml"
    p.write_text(body, encoding="utf-8")
    return p


BASE = """
[[provider]]
id = "x-eth"
infrastructure = "x-infra"
name = "X"
operator = "X"
kind = "rpc"
protocol = "evm-jsonrpc"
chains = ["ethereum"]
network = "mainnet"
access = "public"
endpoint_url = "https://rpc.example-x.com"
allowed_hosts = ["rpc.example-x.com"]
docs_url = "https://example-x.com/docs"
terms_url = "https://example-x.com/terms"
"""


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ('methods = ["eth_sendRawTransaction"]', "not permitted"),
        ('methods = ["getSlot"]', "not permitted"),
        ('endpoint_url = "http://rpc.example-x.com"', "scheme"),
        ('endpoint_url = "https://evil.example-y.com"', "allowed_hosts"),
        ('endpoint_url = "https://127.0.0.1"', "loopback|allowed"),
        ('chains = ["solana"]', "family"),
        ('endpoint_env = "OTHER_URL"', "BIP_PROVIDER_"),
    ],
)
def test_registry_rejects_unsafe_entries(tmp_path: Path, patch: str, match: str) -> None:
    lines = [ln for ln in BASE.splitlines() if not ln.startswith(patch.split(" = ")[0] + " =")]
    with pytest.raises(RegistryError, match=match):
        load_registry(_write(tmp_path, "\n".join([*lines, patch])))


def test_registry_rejects_duplicates_and_empty(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="duplicate"):
        load_registry(_write(tmp_path, BASE + BASE))
    with pytest.raises(RegistryError, match="no"):
        load_registry(_write(tmp_path, "title = 'x'"))


def test_host_allowlist_matching() -> None:
    assert host_allowed("abc.quiknode.pro", (".quiknode.pro",))
    assert not host_allowed("quiknode.pro", (".quiknode.pro",))
    assert not host_allowed("evilquiknode.pro", (".quiknode.pro",))
    assert host_allowed("mainnet.infura.io", ("mainnet.infura.io",))


def test_credentialed_endpoint_resolution_never_exposes_secret() -> None:
    spec = default_registry().get("helius-sol")
    assert resolve_endpoint(spec, {}).configuration == "not_configured"
    url = "https://mainnet.helius-rpc.com/?api-key=" + "k3y" * 8
    res = resolve_endpoint(spec, {"BIP_PROVIDER_HELIUS_SOL_URL": url})
    assert res.configuration == "configured"
    assert res.endpoint is not None
    assert "k3y" not in repr(res)
    assert "k3y" not in res.reason
    bad = resolve_endpoint(
        spec, {"BIP_PROVIDER_HELIUS_SOL_URL": "https://attacker.example.net/?api-key=x"}
    )
    assert bad.configuration == "invalid"
    assert bad.endpoint is None


def test_lifecycle_states() -> None:
    reg = default_registry()
    eth = reg.get("publicnode-eth")
    assert compute_view(eth, None, {}).lifecycle == "untested"
    assert compute_view(reg.get("alchemy-eth"), None, {}).lifecycle == "adapter_available"
    assert compute_view(reg.get("etherscan"), None, {}).lifecycle == "registered"
    assert (
        compute_view(reg.get("etherscan"), None, {"BIP_PROVIDER_ETHERSCAN_KEY": "x"}).lifecycle
        == "configured"
    )
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    ok = ProviderState("publicnode-eth", last_check_at=now, last_success_at=now)
    assert compute_view(eth, ok, {}).lifecycle == "healthy"
    flaky = ProviderState(
        "publicnode-eth",
        last_check_at=now,
        last_success_at=now,
        last_failure_at=now - timedelta(minutes=1),
    )
    assert compute_view(eth, flaky, {}).lifecycle == "degraded"
    down = ProviderState(
        "publicnode-eth", last_check_at=now, last_failure_at=now, consecutive_failures=1
    )
    assert compute_view(eth, down, {}).lifecycle == "unavailable"
    off = ProviderState("publicnode-eth", enabled_override=False)
    assert compute_view(eth, off, {}).lifecycle == "disabled"


# ---------- JSON-RPC ----------


@pytest.mark.parametrize(
    ("raw", "cls"),
    [
        (b"not json", "malformed"),
        (b"[]", "malformed"),
        (b'{"jsonrpc":"1.0","id":7,"result":1}', "malformed"),
        (b'{"jsonrpc":"2.0","id":8,"result":1}', "malformed"),
        (b'{"jsonrpc":"2.0","id":7}', "malformed"),
        (b'{"jsonrpc":"2.0","id":7,"result":1,"error":{}}', "malformed"),
        (
            b'{"jsonrpc":"2.0","id":7,"error":{"code":-32601,"message":"nope"}}',
            "method_unsupported",
        ),
        (
            b'{"jsonrpc":"2.0","id":7,"error":{"code":-32005,"message":"rate limit"}}',
            "rate_limited",
        ),
        (b'{"jsonrpc":"2.0","id":7,"error":{"code":-32602,"message":"bad"}}', "invalid_request"),
        (b'{"jsonrpc":"2.0","id":7,"error":{"code":-32007,"message":"skipped"}}', "not_available"),
        (b'{"jsonrpc":"2.0","id":7,"error":"oops"}', "malformed"),
    ],
)
def test_parse_response_validation(raw: bytes, cls: str) -> None:
    with pytest.raises(RpcError) as info:
        parse_response(raw, REQ)
    assert info.value.error_class == cls


def test_parse_response_ok_and_null() -> None:
    assert parse_response(b'{"jsonrpc":"2.0","id":7,"result":"0x1"}', REQ) == "0x1"
    assert parse_response(b'{"jsonrpc":"2.0","id":7,"result":null}', REQ) is None


def test_error_classification() -> None:
    assert (
        classify_error(-32005, "query exceeds max block range 1000", "eth_getLogs")
        == "range_too_large"
    )
    assert (
        classify_error(-32000, "query returned more than 10000 results", "eth_getLogs")
        == "range_too_large"
    )
    assert classify_error(None, "Too Many Requests", "getSlot") == "rate_limited"
    assert classify_http_status(429) == "rate_limited"
    assert classify_http_status(401) == "unauthorized"
    assert classify_http_status(503) == "transient"
    assert classify_http_status(200) is None


# ---------- transport safety (no network) ----------


def test_transport_refuses_private_resolution() -> None:
    t = HttpsTransport(resolver=lambda host, port: ["10.1.2.3"])
    ep = ResolvedEndpoint(
        SecretStr("https://rpc.example-x.com/v2/secretkey123"), "rpc.example-x.com", True
    )
    with pytest.raises(TransportError) as info:
        t.post_json(ep, b"{}", timeout_s=1, max_bytes=1000)
    assert info.value.kind == "unsafe_destination"
    assert "secretkey123" not in str(info.value)


def test_transport_proxy_mode_requires_proxy() -> None:
    t = HttpsTransport(use_env_proxy=True, environ={})
    ep = ResolvedEndpoint(SecretStr("https://rpc.example-x.com/"), "rpc.example-x.com", False)
    with pytest.raises(TransportError, match="HTTPS_PROXY"):
        t.post_json(ep, b"{}", timeout_s=1, max_bytes=1000)


def test_unsafe_url_error_is_raised_for_bad_registry_url() -> None:
    from bip.providers.registry import _check_url  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(UnsafeURLError):
        _check_url("https://169.254.169.254/", ("169.254.169.254",))


def test_transport_distinguishes_dns_failure_from_unsafe_address() -> None:
    def no_dns(host: str, port: int) -> list[str]:
        raise OSError("Name or service not known")

    ep = ResolvedEndpoint(SecretStr("https://rpc.example-x.com/"), "rpc.example-x.com", False)
    with pytest.raises(TransportError) as info:
        HttpsTransport(resolver=no_dns).post_json(ep, b"{}", timeout_s=1, max_bytes=10)
    assert info.value.kind == "connection"
    assert "DNS" in str(info.value)
    mixed = HttpsTransport(resolver=lambda h, p: ["93.184.216.34", "169.254.169.254"])
    with pytest.raises(TransportError) as info2:
        mixed.post_json(ep, b"{}", timeout_s=1, max_bytes=10)
    assert info2.value.kind == "unsafe_destination"


def test_minus_32001_means_cleaned_up_on_solana_but_stays_retryable_on_evm() -> None:
    from bip.rpc.jsonrpc import classify_error

    assert classify_error(-32001, "Block 5 cleaned up", "getBlock") == "not_available"
    assert classify_error(-32001, "resource not found", "eth_getBlockByNumber") == "transient"
