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


def _breaker(tmp_path: Path, enabled: bool = True):
    """A router whose wall clock the test controls, for the persisted-state circuit breaker."""
    from datetime import UTC, datetime, timedelta

    settings = load_settings(
        data_root=tmp_path / "cb",
        env="test",
        rpc={"circuit_failure_threshold": 3, "circuit_open_s": 60},
        router={"circuit_breakers_enabled": enabled},
    )
    engine = create_db_engine(settings)
    store = ProviderStateStore(Repository(engine))
    wall = {"now": datetime(2026, 11, 13, 12, 0, tzinfo=UTC)}
    node = FakeEthNode()
    router = ProviderRouter(
        default_registry(),
        settings,
        FakeTransport({ETH_HOST: node.handle}),
        store,
        environ={},
        clock=Clock().now,
        sleep=lambda _s: None,
        rng=lambda: 0.5,
        wall_clock=lambda: wall["now"],
    )
    return router, store, engine, wall, timedelta


def test_circuit_opens_after_repeated_failures_and_half_opens_later(tmp_path: Path) -> None:
    from bip.db.types import utcnow

    router, store, engine, wall, td = _breaker(tmp_path)
    wall["now"] = utcnow()
    for _ in range(3):
        store.record_call("publicnode-eth", ok=False, latency_ms=None, outcome="timeout")
    with pytest.raises(NoEligibleProviderError) as info:
        router.call("ethereum", "eth_blockNumber", [])
    assert "circuit open" in info.value.reasons["publicnode-eth"]
    # the state lives in the database, so a fresh router (a restart) sees the same open circuit
    _, reasons = router.eligible("ethereum", "eth_blockNumber")
    assert "circuit open" in reasons["publicnode-eth"]
    wall["now"] = wall["now"] + td(seconds=61)  # half-open: one call goes through
    assert router.call("ethereum", "eth_blockNumber", []).result is not None
    assert store.states()["publicnode-eth"].consecutive_failures == 0  # closed again
    engine.dispose()


def test_circuit_stays_closed_below_threshold_or_when_disabled(tmp_path: Path) -> None:
    from bip.db.types import utcnow

    router, store, engine, wall, _ = _breaker(tmp_path / "a")
    wall["now"] = utcnow()
    for _ in range(2):
        store.record_call("publicnode-eth", ok=False, latency_ms=None, outcome="timeout")
    assert router.call("ethereum", "eth_blockNumber", []).result is not None
    engine.dispose()
    router, store, engine, wall, _ = _breaker(tmp_path / "b", enabled=False)
    wall["now"] = utcnow()
    for _ in range(10):
        store.record_call("publicnode-eth", ok=False, latency_ms=None, outcome="timeout")
    assert router.call("ethereum", "eth_blockNumber", []).result is not None
    engine.dispose()


def test_open_circuit_survives_a_restart_and_a_failed_trial_reopens_it(tmp_path: Path) -> None:
    from bip.db.types import utcnow

    _router, store, engine, wall, td = _breaker(tmp_path)
    wall["now"] = utcnow()
    for _ in range(3):
        store.record_call("publicnode-eth", ok=False, latency_ms=None, outcome="timeout")
    settings = load_settings(
        data_root=tmp_path / "cb",
        env="test",
        rpc={"circuit_failure_threshold": 3, "circuit_open_s": 60},
    )
    restarted = ProviderRouter(
        default_registry(),
        settings,
        FakeTransport({}),
        ProviderStateStore(Repository(engine)),
        environ={},
        clock=Clock().now,
        sleep=lambda _s: None,
        rng=lambda: 0.5,
        wall_clock=lambda: wall["now"],
    )
    _, reasons = restarted.eligible("ethereum", "eth_blockNumber")
    assert "circuit open" in reasons["publicnode-eth"]
    # half-open: the trial call fails (no node answers), which reopens the circuit
    wall["now"] = utcnow() + td(seconds=61)
    with pytest.raises((RoutingFailedError, NoEligibleProviderError, TransportError)):
        restarted.call("ethereum", "eth_blockNumber", [])
    wall["now"] = utcnow() + td(seconds=1)
    _, reasons = restarted.eligible("ethereum", "eth_blockNumber")
    assert "circuit open" in reasons["publicnode-eth"]
    engine.dispose()


def test_half_open_lets_exactly_one_trial_call_through(tmp_path: Path) -> None:
    from bip.db.types import utcnow

    router, store, engine, wall, td = _breaker(tmp_path)
    for _ in range(3):
        store.record_call("publicnode-eth", ok=False, latency_ms=None, outcome="timeout")
    wall["now"] = utcnow() + td(seconds=61)
    for _ in range(2):  # listing (as the corroborator does) never uses up the trial call
        listed, _ = router.eligible("ethereum", "eth_blockNumber")
        assert [v.spec.id for v in listed] == ["publicnode-eth"]
    # simulate a trial call in flight from another caller: the claim is held
    assert router._claim_half_open(  # pyright: ignore[reportPrivateUsage]
        "publicnode-eth", store.states()["publicnode-eth"], 1000.0
    )
    listed, reasons = router.eligible("ethereum", "eth_blockNumber")
    assert listed == []
    assert "trial call is already in flight" in reasons["publicnode-eth"]
    with pytest.raises(NoEligibleProviderError):
        router.call("ethereum", "eth_blockNumber", [])
    engine.dispose()


def test_a_pinned_call_claims_only_its_own_providers_half_open_trial(tmp_path: Path) -> None:
    """The corroborator lists providers, then makes one pinned call each: calling one provider
    must not use up another provider's half-open trial call."""
    from datetime import timedelta

    from bip.db.types import utcnow

    settings = load_settings(
        data_root=tmp_path / "pin",
        env="test",
        rpc={"circuit_failure_threshold": 3, "circuit_open_s": 60},
    )
    engine = create_db_engine(settings)
    store = ProviderStateStore(Repository(engine))
    node = FakeEthNode()
    wall = {"now": utcnow()}
    router = ProviderRouter(
        default_registry(),
        settings,
        FakeTransport({ETH_HOST: node.handle, "mainnet.infura.io": node.handle}),
        store,
        environ={"BIP_PROVIDER_INFURA_ETH_URL": INFURA},
        clock=Clock().now,
        sleep=lambda _s: None,
        rng=lambda: 0.5,
        wall_clock=lambda: wall["now"],
    )
    for pid in ("publicnode-eth", "infura-eth"):
        for _ in range(3):
            store.record_call(pid, ok=False, latency_ms=None, outcome="timeout")
    wall["now"] = utcnow() + timedelta(seconds=61)  # both half-open
    listed, _ = router.eligible("ethereum", "eth_blockNumber")
    assert {v.spec.id for v in listed} == {"publicnode-eth", "infura-eth"}
    router.call("ethereum", "eth_blockNumber", [], provider_id="publicnode-eth")
    # infura's trial is still available for its own pinned call
    assert (
        router.call("ethereum", "eth_blockNumber", [], provider_id="infura-eth").provider_id
        == "infura-eth"
    )
    engine.dispose()


def test_daily_usage_is_counted_and_a_configured_budget_stops_routing(tmp_path: Path) -> None:
    settings = load_settings(
        data_root=tmp_path / "budget",
        env="test",
        rpc={"daily_request_budgets": {"publicnode-eth": 3}},
    )
    engine = create_db_engine(settings)
    store = ProviderStateStore(Repository(engine))
    node = FakeEthNode()
    router = ProviderRouter(
        default_registry(),
        settings,
        FakeTransport({ETH_HOST: node.handle}),
        store,
        environ={},
        clock=Clock().now,
        sleep=lambda _s: None,
        rng=lambda: 0.5,
    )
    router.call("ethereum", "eth_blockNumber", [])  # identity check + call = 2 requests
    assert store.usage_today()["publicnode-eth"] == 2
    router.call("ethereum", "eth_blockNumber", [])
    assert store.usage_today()["publicnode-eth"] == 3
    with pytest.raises(NoEligibleProviderError) as info:
        router.call("ethereum", "eth_blockNumber", [])
    assert "daily request budget reached" in info.value.reasons["publicnode-eth"]
    assert node.calls.count("eth_blockNumber") == 2  # the capped call was never sent
    engine.dispose()


def test_failover_to_a_secondary_and_back_when_the_primary_recovers(tmp_path: Path) -> None:
    """Primary fails, the secondary serves; when the primary answers again it is used again."""
    primary, secondary = FakeEthNode(), FakeEthNode()
    t = FakeTransport({ETH_HOST: primary.handle, "mainnet.infura.io": secondary.handle})
    router, store, _, engine = make(tmp_path, t, {"BIP_PROVIDER_INFURA_ETH_URL": INFURA})
    router.call("ethereum", "eth_blockNumber", [])  # both healthy: identity checks happen
    for _ in range(3):
        t.script(ETH_HOST, Scripted(status=503, body=b"down"))
    served = {router.call("ethereum", "eth_blockNumber", []).provider_id for _ in range(2)}
    assert "infura-eth" in served
    assert store.states()["publicnode-eth"].consecutive_failures >= 1
    # the primary answers again; when the secondary then fails, the primary serves and its
    # failure count clears (a healthy secondary is preferred while the primary is degraded)
    t.scripts.pop(ETH_HOST, None)  # the primary is healthy again
    for _ in range(3):
        t.script("mainnet.infura.io", Scripted(status=503, body=b"down"))
    assert router.call("ethereum", "eth_blockNumber", []).provider_id == "publicnode-eth"
    assert store.states()["publicnode-eth"].consecutive_failures == 0
    engine.dispose()


def test_an_auth_failure_drops_that_provider_without_retrying_it(tmp_path: Path) -> None:
    primary, secondary = FakeEthNode(), FakeEthNode()
    t = FakeTransport({ETH_HOST: primary.handle, "mainnet.infura.io": secondary.handle})
    router, store, _, engine = make(tmp_path, t, {"BIP_PROVIDER_INFURA_ETH_URL": INFURA})
    router.call("ethereum", "eth_blockNumber", [])  # identity checks done
    before = [h for h, _m in t.requests].count(ETH_HOST)
    t.script(ETH_HOST, Scripted(status=401, body=b"unauthorized"))
    out = router.call("ethereum", "eth_blockNumber", [])
    assert out.provider_id == "infura-eth"
    assert [h for h, _m in t.requests].count(ETH_HOST) - before == 1  # tried once, not retried
    assert store.states()["publicnode-eth"].last_outcome == "http_401"
    engine.dispose()


def test_a_probe_respects_the_daily_budget(tmp_path: Path) -> None:
    settings = load_settings(
        data_root=tmp_path / "pb", env="test", rpc={"daily_request_budgets": {"publicnode-eth": 1}}
    )
    engine = create_db_engine(settings)
    store = ProviderStateStore(Repository(engine))
    node = FakeEthNode()
    router = ProviderRouter(
        default_registry(),
        settings,
        FakeTransport({ETH_HOST: node.handle}),
        store,
        environ={},
        clock=Clock().now,
        sleep=lambda _s: None,
        rng=lambda: 0.5,
    )
    spec = default_registry().get("publicnode-eth")
    first = router.probe(
        spec, "eth_blockNumber", [], timeout_s=1, max_bytes=10_000, min_interval_s=0
    )
    second = router.probe(
        spec, "eth_blockNumber", [], timeout_s=1, max_bytes=10_000, min_interval_s=0
    )
    assert first.refused is None
    assert second.refused == "daily request budget reached"
    engine.dispose()
