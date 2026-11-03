from __future__ import annotations

from pathlib import Path

import pytest

from bip.config import load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.providers.registry import default_registry
from bip.providers.state import ProviderStateStore
from bip.providers.status import compute_view
from bip.router.router import (
    BudgetExhaustedError,
    CallBudget,
    NoEligibleProviderError,
    ProviderRouter,
    RoutingFailedError,
)
from bip.rpc.jsonrpc import RpcError
from bip.rpc.transport import TransportError
from tests.stage2.fakes import ETH_HOST, SOL_HOST, FakeEthNode, FakeSolNode, FakeTransport, Scripted

INFURA = "https://mainnet.infura.io/v3/" + "f" * 32


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def make(tmp_path: Path, transport: FakeTransport, environ: dict[str, str] | None = None):
    settings = load_settings(data_root=tmp_path / "d", env="test")
    engine = create_db_engine(settings)
    store = ProviderStateStore(Repository(engine))
    clock = Clock()
    router = ProviderRouter(
        default_registry(),
        settings,
        transport,
        store,
        environ=environ or {},
        clock=clock.now,
        sleep=clock.sleep,
        rng=lambda: 0.5,
    )
    return router, store, clock, engine


def test_identity_verified_once_then_reused(tmp_path: Path) -> None:
    node = FakeEthNode()
    router, store, _, engine = make(tmp_path, FakeTransport({ETH_HOST: node.handle}))
    assert router.call("ethereum", "eth_blockNumber", []).result == hex(node.head)
    router.call("ethereum", "eth_blockNumber", [])
    assert node.calls == ["eth_chainId", "eth_blockNumber", "eth_blockNumber"]
    view = compute_view(
        default_registry().get("publicnode-eth"), store.states()["publicnode-eth"], {}
    )
    assert view.lifecycle == "healthy"
    engine.dispose()


def test_network_mismatch_is_never_used(tmp_path: Path) -> None:
    wrong = FakeEthNode(chain_id="0x89")  # Polygon
    router, store, _, engine = make(tmp_path, FakeTransport({ETH_HOST: wrong.handle}))
    with pytest.raises((RoutingFailedError, NoEligibleProviderError)):
        router.call("ethereum", "eth_blockNumber", [])
    assert "eth_blockNumber" not in wrong.calls
    assert store.states()["publicnode-eth"].last_outcome == "network_mismatch"
    with pytest.raises(NoEligibleProviderError, match="identity mismatch"):
        router.call("ethereum", "eth_blockNumber", [])
    engine.dispose()


def test_solana_genesis_identity(tmp_path: Path) -> None:
    node = FakeSolNode()
    router, _, _, engine = make(tmp_path, FakeTransport({SOL_HOST: node.handle}))
    assert router.call("solana", "getSlot", [{"commitment": "finalized"}]).result == node.head_slot
    assert node.calls[0] == "getGenesisHash"
    devnet = FakeSolNode(genesis="EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG")
    router2, _, _, engine2 = make(tmp_path / "b", FakeTransport({SOL_HOST: devnet.handle}))
    with pytest.raises((RoutingFailedError, NoEligibleProviderError)):
        router2.call("solana", "getSlot", [])
    engine.dispose()
    engine2.dispose()


def test_no_eligible_provider_explains_why(tmp_path: Path) -> None:
    router, store, _, engine = make(tmp_path, FakeTransport({}))
    store.set_enabled("solana-public", False, "test")
    with pytest.raises(NoEligibleProviderError) as info:
        router.call("solana", "getSlot", [])
    assert info.value.reasons["solana-public"] == "disabled"
    assert "BIP_PROVIDER_HELIUS_SOL_URL" in info.value.reasons["helius-sol"]
    with pytest.raises(NoEligibleProviderError) as info2:
        router.call("ethereum", "eth_sendRawTransaction", [])
    assert all(
        "not supported" in r or "Set " in r or "adapter" in r for r in info2.value.reasons.values()
    )
    engine.dispose()


def test_rate_limit_fails_over_and_respects_retry_after(tmp_path: Path) -> None:
    node = FakeEthNode()
    t = FakeTransport({ETH_HOST: node.handle, "mainnet.infura.io": node.handle})
    router, store, clock, engine = make(tmp_path, t, {"BIP_PROVIDER_INFURA_ETH_URL": INFURA})
    router.call(
        "ethereum", "eth_blockNumber", [], provider_id="publicnode-eth"
    )  # make publicnode healthy first
    t.script(ETH_HOST, Scripted(status=429, headers={"retry-after": "30"}))
    out = router.call("ethereum", "eth_blockNumber", [])
    assert out.provider_id == "infura-eth"
    usable, reasons = router.eligible("ethereum", "eth_blockNumber")
    assert "rate-limited" in reasons["publicnode-eth"]
    assert [v.spec.id for v in usable] == ["infura-eth"]
    assert store.states()["publicnode-eth"].last_outcome == "http_429"
    assert (
        clock.sleeps == [] or max(clock.sleeps) < 1
    )  # no long inline wait while another provider exists
    engine.dispose()


def test_long_retry_after_with_single_provider_stops(tmp_path: Path) -> None:
    node = FakeEthNode()
    t = FakeTransport({ETH_HOST: node.handle})
    router, _, clock, engine = make(tmp_path, t)
    router.call("ethereum", "eth_chainId", [])
    t.script(ETH_HOST, Scripted(status=429, headers={"retry-after": "120"}))
    with pytest.raises(RoutingFailedError):
        router.call("ethereum", "eth_blockNumber", [])
    assert all(s < 120 for s in clock.sleeps)
    engine.dispose()


def test_transient_errors_retry_with_bounded_backoff(tmp_path: Path) -> None:
    node = FakeEthNode()
    t = FakeTransport({ETH_HOST: node.handle})
    router, store, clock, engine = make(tmp_path, t)
    router.call("ethereum", "eth_chainId", [])
    t.script(
        ETH_HOST, Scripted(status=503), Scripted(error=TransportError("timeout", ETH_HOST, "t"))
    )
    out = router.call("ethereum", "eth_blockNumber", [])
    assert out.result == hex(node.head)
    assert len([s for s in clock.sleeps if s > 0.2]) >= 2  # backoff happened (plus rps spacing)
    t.script(ETH_HOST, *[Scripted(status=500)] * 5)
    with pytest.raises(RoutingFailedError) as info:
        router.call("ethereum", "eth_blockNumber", [])
    assert len(info.value.attempts) == 3  # rpc.max_attempts
    assert store.states()["publicnode-eth"].consecutive_failures >= 3
    engine.dispose()


def test_malformed_responses_exhaust_attempts(tmp_path: Path) -> None:
    node = FakeEthNode()
    t = FakeTransport({ETH_HOST: node.handle})
    router, store, _, engine = make(tmp_path, t)
    router.call("ethereum", "eth_chainId", [])
    t.script(
        ETH_HOST,
        Scripted(body=b'{"jsonrpc":"2.0","id":999,"result":"0x1"}'),
        Scripted(body=b"<html>"),
        Scripted(body=b'{"jsonrpc":"2.0"}'),
    )
    with pytest.raises(RoutingFailedError, match="malformed"):
        router.call("ethereum", "eth_blockNumber", [])
    assert store.states()["publicnode-eth"].last_outcome == "rpc_malformed"
    engine.dispose()


def test_invalid_request_is_not_retried(tmp_path: Path) -> None:
    node = FakeEthNode()
    router, store, _, engine = make(tmp_path, FakeTransport({ETH_HOST: node.handle}))
    with pytest.raises(RpcError) as info:
        router.call("ethereum", "eth_getBalance", ["bad", "latest"])
    assert info.value.error_class == "invalid_request"
    assert node.calls.count("eth_getBalance") == 1
    assert (
        store.states()["publicnode-eth"].consecutive_failures == 0
    )  # our fault, not the provider's
    engine.dispose()


def test_unsupported_method_is_never_resent_to_that_provider(tmp_path: Path) -> None:
    node = FakeEthNode(unsupported={"eth_getTransactionCount"})
    router, _, _, engine = make(tmp_path, FakeTransport({ETH_HOST: node.handle}))
    with pytest.raises(RoutingFailedError):
        router.call("ethereum", "eth_getTransactionCount", [node.wallet, "latest"])
    with pytest.raises(NoEligibleProviderError, match="reported unsupported"):
        router.call("ethereum", "eth_getTransactionCount", [node.wallet, "latest"])
    assert node.calls.count("eth_getTransactionCount") == 1
    engine.dispose()


def test_budget_bounds_calls(tmp_path: Path) -> None:
    node = FakeEthNode()
    router, _, _, engine = make(tmp_path, FakeTransport({ETH_HOST: node.handle}))
    budget = CallBudget(max_calls=2, deadline=10**9)
    router.call("ethereum", "eth_blockNumber", [], budget=budget)  # identity + call = 2
    with pytest.raises(BudgetExhaustedError):
        router.call("ethereum", "eth_blockNumber", [], budget=budget)
    engine.dispose()


def test_rps_spacing(tmp_path: Path) -> None:
    node = FakeEthNode()
    router, _, clock, engine = make(tmp_path, FakeTransport({ETH_HOST: node.handle}))
    for _ in range(4):
        router.call("ethereum", "eth_blockNumber", [])
    spacing = [s for s in clock.sleeps if s > 0]
    assert spacing  # requests were spaced by client_rps (4/s -> 0.25 s)
    assert all(abs(s - 0.25) < 1e-6 for s in spacing)
    engine.dispose()
