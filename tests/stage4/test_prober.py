"""Stage 4: provider capability prober, budgets, failure classification, persistence, API.

Every request goes to deterministic fake nodes (tests/stage2/fakes.py); failures are injected
with scripted transport responses. No test touches the network.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from bip.config import load_settings
from bip.config.settings import ProbeSettings
from bip.core.exceptions import ConfigurationError
from bip.db import schema
from bip.providers import capabilities as caps
from bip.providers.intelligence import compute_reliability, consistency_checks
from bip.providers.prober import ENVIRONMENT_RESTRICTION, Prober, ProviderResult
from bip.providers.registry import (
    ProviderSpec,
    Registry,
    default_registry,
    is_write_capable,
)
from bip.rpc.transport import TransportError
from tests.stage2.fakes import ETH_HOST, SOL_HOST, Scripted, h32
from tests.stage3.test_intel_api import INFURA_HOST, Env

API = "/api/v1"
J = dict[str, Any]


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path, second=False)  # keyless providers only
    yield e
    e.engine.dispose()


@pytest.fixture
def client(env: Env) -> Iterator[TestClient]:
    with TestClient(env.app) as c:
        yield c


def run(client: TestClient, **body: Any) -> J:
    r = client.post(f"{API}/providers/probe-runs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def obs(result: J, provider: str, capability: str) -> J:
    return next(
        o
        for o in result["observations"]
        if o["provider_id"] == provider and o["capability"] == capability
    )


def sent(env: Env, host: str) -> list[str]:
    return [m for h, m in env.transport.requests if h == host]


# ---------------------------------------------------------------- registry and allow-list


def test_every_rpc_provider_has_infrastructure_and_documentation_basis() -> None:
    for spec in default_registry().all():
        assert spec.documentation_basis in {"official_docs", "search_derived", "unverified"}
        if spec.has_adapter:
            assert spec.infrastructure, spec.id
            assert not any(is_write_capable(m) for m in spec.methods)


@pytest.mark.parametrize(
    "method",
    [
        "eth_sendRawTransaction",
        "eth_sendTransaction",
        "eth_sign",
        "personal_unlockAccount",
        "sendTransaction",
        "requestAirdrop",
        "admin_peers",
        "debug_traceTransaction",
        "eth_accounts",
    ],
)
def test_write_capable_methods_are_rejected(method: str) -> None:
    assert is_write_capable(method)
    base = default_registry().get("publicnode-eth").model_dump()
    with pytest.raises(ValueError, match="not permitted"):
        ProviderSpec.model_validate({**base, "methods": [*base["methods"], method]})


def test_router_probe_refuses_methods_outside_allow_list(env: Env, client: TestClient) -> None:
    spec = env.app.state.bip.registry.get("publicnode-eth")
    for method in ("eth_sendRawTransaction", "eth_getStorageAt"):
        r = env.app.state.bip.router.probe(
            spec, method, [], timeout_s=5, max_bytes=1024, min_interval_s=1.0
        )
        assert r.sent is False
        assert r.refused == "method not in allow-list"
    assert env.transport.requests == []


def test_probe_budget_ceilings_cannot_be_raised() -> None:
    for bad in (
        {"max_calls_per_provider": 9},
        {"max_calls_per_run": 49},
        {"max_run_seconds": 91},
        {"max_concurrent_providers": 3},
        {"request_timeout_s": 6},
        {"retries": 1},
        {"max_response_bytes": 2**20 + 1},
        {"min_interval_s": 0.5},
    ):
        with pytest.raises(ConfigurationError):
            load_settings(env="test", probe=bad)
    lower = load_settings(env="test", probe={"max_calls_per_provider": 2, "min_interval_s": 3})
    assert lower.probe.max_calls_per_provider == 2


# ---------------------------------------------------------------- preview and validation


def test_preview_sends_nothing_and_explains_ineligible_providers(
    env: Env, client: TestClient
) -> None:
    p = client.get(f"{API}/providers/probe/preview").json()
    assert {x["provider_id"] for x in p["providers"]} == {"publicnode-eth", "solana-public"}
    reasons = {x["provider_id"]: x["reason"] for x in p["ineligible"]}
    assert reasons["infura-eth"].startswith("requires configuration")
    assert p["budget"]["max_calls_per_provider"] == 8
    assert p["budget"]["retries"] == 0
    eth = next(x for x in p["providers"] if x["provider_id"] == "publicnode-eth")
    assert len(eth["planned"]) == 8
    assert eth["over_budget"] == ["block_number", "contract_code"]
    assert p["max_requests"] <= 48
    assert env.transport.requests == []  # preview never contacts a provider


def test_probe_request_validation(client: TestClient) -> None:
    assert (
        client.post(f"{API}/providers/probe-runs", json={"providers": ["nope-x"]}).status_code
        == 404
    )
    bad_cap = client.post(f"{API}/providers/probe-runs", json={"capabilities": ["mint_tokens"]})
    assert bad_cap.status_code == 422
    unconfigured = client.post(f"{API}/providers/probe-runs", json={"providers": ["infura-eth"]})
    assert unconfigured.status_code == 422
    assert "requires configuration" in unconfigured.text
    assert (
        client.post(f"{API}/providers/probe-runs", json={"max_calls_per_provider": 9}).status_code
        == 422
    )
    url = client.post(f"{API}/providers/probe-runs", json={"url": "https://evil.example"})
    assert url.status_code == 422  # arbitrary endpoints cannot even be expressed
    assert (
        client.post(f"{API}/providers/probe-runs", json={"providers": ["etherscan"]}).status_code
        == 422
    )


def test_disabled_providers_are_skipped(client: TestClient) -> None:
    client.patch(f"{API}/providers/publicnode-eth", json={"enabled": False})
    p = client.get(f"{API}/providers/probe/preview").json()
    assert "publicnode-eth" not in {x["provider_id"] for x in p["providers"]}
    assert {"provider_id": "publicnode-eth", "reason": "provider is disabled"} in p["ineligible"]


# ---------------------------------------------------------------- successful run


def test_bounded_run_records_capabilities_profile_and_reliability(
    env: Env, client: TestClient
) -> None:
    r = run(client, providers=["publicnode-eth", "solana-public"])
    assert r["status"] == "completed"
    assert r["requests_used"] == 16
    assert len(sent(env, ETH_HOST)) == 8
    assert obs(r, "publicnode-eth", "chain_id")["status"] == "supported"
    assert (
        obs(r, "publicnode-eth", "historical_block")["reference"]["distance_from_head"] == 100_000
    )
    assert obs(r, "publicnode-eth", "contract_code")["status"] == "not_tested"
    assert "request cap" in obs(r, "publicnode-eth", "contract_code")["explanation"]
    skipped = obs(r, "solana-public", "finalized_block")
    assert skipped["status"] == "inconclusive"  # skipped slot: never "unsupported"
    assert skipped["rpc_error_code"] == -32007
    assert obs(r, "solana-public", "native_transfer_data")["status"] == "supported"
    prof = client.get(f"{API}/providers/publicnode-eth/profile").json()
    assert prof["connectivity"] == "reachable"
    assert prof["historical_coverage"]["label"] == "historical_sample_observed"
    assert prof["historical_coverage"]["archive"] == "archive_capability_unverified"
    assert prof["reliability"]["state"] == "insufficient_data"  # 8 attempts, 1 run
    assert prof["reliability"]["latency"]["p95_ms"] is None
    assert prof["coverage"]["supported"] >= 8
    matrix = {c["id"]: c["observation"] for c in prof["capabilities"]}
    assert matrix["block_number"]["status"] == "not_tested"
    # selecting fewer capabilities tests the rest
    r2 = run(client, providers=["publicnode-eth"], capabilities=["block_number", "contract_code"])
    assert obs(r2, "publicnode-eth", "contract_code")["status"] == "supported"
    assert r2["requests_used"] == 3  # identity is always tested first
    hist = client.get(
        f"{API}/providers/publicnode-eth/capabilities/history?capability=contract_code"
    ).json()
    assert hist["total"] == 2
    assert (
        client.get(
            f"{API}/providers/publicnode-eth/capabilities/history?capability=xx_yy"
        ).status_code
        == 422
    )
    runs = client.get(f"{API}/providers/probe-runs").json()
    assert runs["total"] == 2
    assert client.get(f"{API}/providers/probe-runs/{r['run_id']}").json()["run_id"] == r["run_id"]


def test_fixture_database_runs_are_labelled_and_never_use_stored_ids(tmp_path: Path) -> None:
    e = Env(tmp_path, second=False, data_origin="offline_fixture")
    seen: list[tuple[str, list[object]]] = []
    inner = e.eth.handle

    def spy(method: str, params: list[object]) -> object:
        seen.append((method, params))
        return inner(method, params)

    e.transport.nodes[ETH_HOST] = spy
    try:
        with TestClient(e.app) as c:
            assert (
                c.post(f"{API}/ingestion/wallet/ethereum/{e.eth.wallet}?limit=5").status_code == 201
            )
            seen.clear()
            r = run(c, providers=["publicnode-eth"])
            assert r["data_origin"] == "offline_fixture"
            looked_up = [p[0] for m, p in seen if m == "eth_getTransactionByHash"]
            assert looked_up == [h32(e.eth.head)]  # from the live head block, not stored txs
            assert e.eth.txa not in looked_up
            assert e.eth.txb not in looked_up
    finally:
        e.engine.dispose()


# ---------------------------------------------------------------- failure classification


@pytest.mark.parametrize(
    ("error", "connectivity", "kind", "env_flag"),
    [
        (
            TransportError("connection", ETH_HOST, "DNS resolution failed"),
            "network_blocked",
            "dns_failure",
            True,
        ),
        (
            TransportError("proxy", ETH_HOST, "proxy refused the CONNECT tunnel"),
            "network_blocked",
            "proxy_refused",
            True,
        ),
        (
            TransportError("connection", ETH_HOST, "ConnectionResetError"),
            "network_blocked",
            "connection_failed",
            False,
        ),
        (TransportError("timeout", ETH_HOST, "request timed out"), "timeout", "timeout", False),
        (TransportError("tls", ETH_HOST, "TLS error"), "tls_error", "tls_error", False),
        (
            TransportError("redirect", ETH_HOST, "HTTP 302 redirect refused"),
            "provider_error",
            "redirect_refused",
            False,
        ),
        (
            TransportError("unsafe_destination", ETH_HOST, "private address"),
            "network_blocked",
            "unsafe_destination",
            False,
        ),
    ],
)
def test_transport_failures_are_classified_and_stop_the_provider(
    env: Env,
    client: TestClient,
    error: TransportError,
    connectivity: str,
    kind: str,
    env_flag: bool,
) -> None:
    env.transport.script(ETH_HOST, Scripted(error=error))
    r = run(client, providers=["publicnode-eth"])
    first = obs(r, "publicnode-eth", "chain_id")
    assert first["status"] == "inconclusive"  # never "unsupported"
    assert (first["connectivity"], first["failure_kind"]) == (connectivity, kind)
    assert first["environment_restricted"] is env_flag
    assert len(sent(env, ETH_HOST)) == 1  # no identical repeated failures
    assert obs(r, "publicnode-eth", "latest_block")["status"] == "not_tested"
    assert r["status"] == "partial"
    prof = client.get(f"{API}/providers/publicnode-eth/profile").json()
    assert prof["connectivity"] == connectivity
    if env_flag:
        assert first["explanation"].endswith(ENVIRONMENT_RESTRICTION)
        assert prof["environment_note"] == ENVIRONMENT_RESTRICTION
        assert prof["reliability"]["state"] == "unknown"  # never "unreliable"
        assert prof["reliability"]["success_rate"] is None
        assert "provider is down" not in json.dumps(prof).lower()


@pytest.mark.parametrize(
    ("status", "connectivity"),
    [
        (401, "unauthorized"),
        (403, "unauthorized"),
        (429, "rate_limited"),
        (500, "provider_error"),
        (503, "provider_error"),
        (400, "provider_error"),
    ],
)
def test_http_failures(env: Env, client: TestClient, status: int, connectivity: str) -> None:
    headers = {"retry-after": "30"} if status == 429 else {}
    env.transport.script(
        ETH_HOST, Scripted(status=status, body=b"secret body text", headers=headers)
    )
    r = run(client, providers=["publicnode-eth"])
    o = obs(r, "publicnode-eth", "chain_id")
    assert (o["status"], o["connectivity"], o["http_status"]) == (
        "inconclusive",
        connectivity,
        status,
    )
    assert "secret body text" not in json.dumps(r)  # bodies are never stored
    if status in {401, 403, 429}:
        assert len(sent(env, ETH_HOST)) == 1
    if status == 429:
        assert "Retry-After: 30s" in o["explanation"]
        lifecycle = client.get(f"{API}/providers/publicnode-eth").json()["lifecycle"]
        assert lifecycle != "unavailable"  # a rate limit is not an outage
    if status == 403:
        assert "does not show that the provider is down" in o["explanation"]


def test_jsonrpc_errors_and_invalid_responses(env: Env, client: TestClient) -> None:
    # The router numbers requests; scripted replies must echo the id it will use.
    router = env.app.state.bip.router
    nxt = router._next_id + 1  # pyright: ignore[reportPrivateUsage]
    env.transport.script(
        ETH_HOST,
        Scripted(body=json.dumps({"jsonrpc": "2.0", "id": nxt, "result": "0x1"}).encode()),
        Scripted(
            body=json.dumps(
                {"jsonrpc": "2.0", "id": nxt + 1, "error": {"code": -32601, "message": "not found"}}
            ).encode()
        ),
        Scripted(body=b"{not json"),
    )
    r = run(
        client,
        providers=["publicnode-eth"],
        capabilities=["chain_id", "latest_block", "finalized_block", "historical_block"],
    )
    assert obs(r, "publicnode-eth", "latest_block")["status"] == "unsupported"
    bad_json = obs(r, "publicnode-eth", "finalized_block")
    assert (bad_json["status"], bad_json["connectivity"]) == ("inconclusive", "invalid_response")
    # the head is unknown after "latest" was unsupported: no historical sample is invented
    assert obs(r, "publicnode-eth", "historical_block")["status"] == "not_tested"


def test_malformed_result_is_not_supported(env: Env, client: TestClient) -> None:
    env.eth.blocks[env.eth.head] = {"number": hex(env.eth.head)}  # no hash
    r = run(client, providers=["publicnode-eth"], capabilities=["latest_block"])
    o = obs(r, "publicnode-eth", "latest_block")
    assert (o["status"], o["connectivity"], o["validation"]) == (
        "inconclusive",
        "invalid_response",
        "failed",
    )


def test_wrong_network_stops_probing(env: Env, client: TestClient) -> None:
    env.eth.chain_id = "0x2105"
    r = run(client, providers=["publicnode-eth"])
    o = obs(r, "publicnode-eth", "chain_id")
    assert (o["connectivity"], o["status"]) == ("wrong_network", "inconclusive")
    assert len(sent(env, ETH_HOST)) == 1
    assert (
        client.get(f"{API}/providers/publicnode-eth/profile").json()["connectivity"]
        == "wrong_network"
    )


def test_historical_limits_and_missing_samples(env: Env, client: TestClient) -> None:
    env.eth.history_floor = env.eth.head - 10  # pruned node: historical block returns null
    env.eth.blocks[env.eth.head]["transactions"] = []  # nothing to look up
    r = run(client, providers=["publicnode-eth"])
    h = obs(r, "publicnode-eth", "historical_block")
    assert (h["status"], h["failure_kind"]) == ("inconclusive", "null_result")
    tx = obs(r, "publicnode-eth", "transaction_lookup")
    assert tx["status"] == "not_tested"
    assert "no safe sample" in tx["explanation"]
    prof = client.get(f"{API}/providers/publicnode-eth/profile").json()
    assert prof["historical_coverage"]["label"] == "historical_access_failed"


def test_oversized_response_is_inconclusive(env: Env, client: TestClient) -> None:
    env.transport.script(ETH_HOST, Scripted(error=TransportError("too_large", ETH_HOST, "big")))
    r = run(client, providers=["publicnode-eth"], capabilities=["chain_id"])
    o = obs(r, "publicnode-eth", "chain_id")
    assert (o["status"], o["failure_kind"]) == ("inconclusive", "response_too_large")


# ---------------------------------------------------------------- budgets


class _Recorder:
    """Transport wrapper that records limits and concurrency."""

    def __init__(self, inner: Any, delay: float = 0.0) -> None:
        self.inner = inner
        self.delay = delay
        self.calls: list[tuple[str, float, int]] = []
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def post_json(self, endpoint: Any, body: bytes, *, timeout_s: float, max_bytes: int) -> Any:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append((endpoint.host, timeout_s, max_bytes))
        try:
            if self.delay:
                time.sleep(self.delay)
            return self.inner.post_json(endpoint, body, timeout_s=timeout_s, max_bytes=max_bytes)
        finally:
            with self.lock:
                self.active -= 1


def _prober(env: Env, **cfg: Any) -> tuple[Prober, _Recorder]:
    ctx = env.app.state.bip
    rec = _Recorder(env.transport, delay=cfg.pop("delay", 0.0))
    ctx.router._transport = rec  # pyright: ignore[reportPrivateUsage]
    return Prober(
        ctx.registry,
        ctx.router,
        ProbeSettings(**cfg),
        clock=cfg.pop("clock", None) or time.monotonic,
    ), rec


def test_per_provider_and_global_caps_timeout_and_size(tmp_path: Path) -> None:
    e = Env(tmp_path)  # 4 eligible providers
    try:
        with TestClient(e.app):
            prober, rec = _prober(e, max_calls_per_provider=3, max_calls_per_run=7)
            results = prober.run(prober.plan())
            assert len(rec.calls) == 7  # global cap
            assert all(r.requests_used <= 3 for r in results)
            assert {(t, b) for _, t, b in rec.calls} == {(5.0, 1024 * 1024)}
            notes = [
                o.explanation for r in results for o in r.observations if o.status == "not_tested"
            ]
            assert any("run request budget of 7 reached" in n for n in notes)
    finally:
        e.engine.dispose()


def test_concurrency_is_limited_to_two_providers(tmp_path: Path) -> None:
    e = Env(tmp_path)
    try:
        with TestClient(e.app):
            prober, rec = _prober(e, max_calls_per_provider=2, delay=0.05)
            prober.run(prober.plan())
            assert rec.peak <= 2
            assert len(rec.calls) == 8
    finally:
        e.engine.dispose()


def test_run_deadline_stops_requests(tmp_path: Path) -> None:
    e = Env(tmp_path, second=False)
    ticks = iter(range(0, 10_000, 30))
    try:
        with TestClient(e.app):
            ctx = e.app.state.bip
            prober = Prober(
                ctx.registry,
                ctx.router,
                ProbeSettings(max_run_seconds=60),
                clock=lambda: float(next(ticks)),
            )
            results = prober.run(prober.plan(["publicnode-eth"]))
            assert results[0].requests_used <= 2
            assert results[0].stop_reason == "run time budget reached"
    finally:
        e.engine.dispose()


def test_requests_are_spaced_and_never_retried(tmp_path: Path) -> None:
    sleeps: list[float] = []
    e = Env(tmp_path, second=False)
    # A fake clock that only advances when the router sleeps: with a real clock the gap between
    # recorded waits is interval minus the wall time spent between requests, which fails on slow
    # CI runners even though the slots are correctly spaced.
    now = [1000.0]

    def fake_sleep(wait: float) -> None:
        sleeps.append(wait)
        now[0] += wait

    try:
        with TestClient(e.app):
            ctx = e.app.state.bip
            ctx.router._clock = lambda: now[0]  # pyright: ignore[reportPrivateUsage]
            ctx.router._sleep = fake_sleep  # pyright: ignore[reportPrivateUsage]
            e.transport.script(ETH_HOST, *[Scripted(status=500)] * 1)
            prober = Prober(ctx.registry, ctx.router, ProbeSettings())
            results = prober.run(prober.plan(["publicnode-eth"]))
            calls = [m for h, m in e.transport.requests if h == ETH_HOST]
            assert calls.count("eth_chainId") == 1  # the 500 was not retried
            assert len(calls) == results[0].requests_used
            # time moves only by sleeping, so every wait is the full spacing to the next slot:
            # consecutive requests to one provider are at least min_interval_s (1 s) apart
            assert sleeps
            assert all(w >= 0.99 for w in sleeps), sleeps
    finally:
        e.engine.dispose()


def test_only_one_probe_run_at_a_time(env: Env, client: TestClient) -> None:
    lock = env.app.state.bip.probe_lock
    assert lock.acquire(blocking=False)
    try:
        r = client.post(f"{API}/providers/probe-runs", json={})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "probe_busy"
    finally:
        lock.release()


def test_nothing_to_probe(env: Env, client: TestClient) -> None:
    for p in ("publicnode-eth", "solana-public", "publicnode-sol"):
        client.patch(f"{API}/providers/{p}", json={"enabled": False})
    r = client.post(f"{API}/providers/probe-runs", json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "nothing_to_probe"


# ---------------------------------------------------------------- consistency and reliability


def test_consistency_compares_like_for_like(tmp_path: Path) -> None:
    e = Env(tmp_path)  # publicnode-eth (allnodes) + infura-eth (consensys)
    try:
        with TestClient(e.app) as c:
            r = run(c, providers=["publicnode-eth", "infura-eth"])
            by = {x["comparison"]: x for x in r["consistency_checks"]}
            assert by["chain_identity"]["outcome"] == "consistent"
            assert by["finalized_block_hash"]["outcome"] == "consistent"
            assert by["finalized_block_hash"]["independent"] is True
            # different sync points: never a conflict
            e.eth2.head += 7
            e.eth2.__post_init__()
            r2 = run(c, providers=["publicnode-eth", "infura-eth"])
            fin = next(
                x for x in r2["consistency_checks"] if x["comparison"] == "finalized_block_hash"
            )
            assert fin["outcome"] == "inconclusive"
            # same number, different hash: conflict, both values kept
            e.eth2.head -= 7
            e.eth2.__post_init__()
            n = e.eth2.head - 64
            e.eth2.blocks[n] = {**e.eth2.blocks[n], "hash": h32(0xBAD)}
            r3 = run(c, providers=["publicnode-eth", "infura-eth"])
            fin3 = next(
                x for x in r3["consistency_checks"] if x["comparison"] == "finalized_block_hash"
            )
            assert fin3["outcome"] == "conflict"
            assert {fin3["value_a"], fin3["value_b"]} >= {h32(0xBAD)}
            listed = c.get(f"{API}/providers/infura-eth/consistency").json()
            assert listed["total"] >= 6
            assert (
                e.app.state.bip.probe_store.consistency(
                    chain="ethereum", provider_id=None, limit=5, offset=0
                )[1]
                >= 6
            )
        with e.engine.connect() as conn:  # Stage 3 evidence untouched
            assert (
                conn.execute(
                    select(func.count()).select_from(schema.evidence_verifications)
                ).scalar_one()
                == 0
            )
    finally:
        e.engine.dispose()


def test_shared_infrastructure_is_never_independent() -> None:
    base = default_registry().get("publicnode-eth").model_dump()
    a = ProviderSpec.model_validate({**base, "id": "aa-eth"})
    b = ProviderSpec.model_validate({**base, "id": "bb-eth"})
    reg = Registry((a, b))
    from datetime import UTC, datetime

    results = [
        ProviderResult(p, "ethereum", [], "reachable", 2, {"chain_id": "0x1"}, None)
        for p in ("aa-eth", "bb-eth")
    ]
    rows = consistency_checks(reg, results, datetime.now(UTC))
    assert rows[0]["independent"] is False
    assert "Not independent" in rows[0]["note"]


def _row(i: int, **kw: Any) -> J:
    from datetime import UTC, datetime

    base: J = {
        "run_id": i % 4,
        "connectivity": "reachable",
        "validation": "passed",
        "environment_restricted": False,
        "failure_kind": None,
        "latency_ms": 100 + i,
        "observed_at": datetime(2026, 11, 3, tzinfo=UTC),
    }
    return base | kw


def test_reliability_sample_size_rules() -> None:
    from datetime import UTC, datetime

    t = datetime(2026, 11, 3, tzinfo=UTC)
    few = compute_reliability("p", [_row(i) for i in range(5)], t, t)
    assert few["state"] == "insufficient_data"
    assert few["latency"]["median_ms"] == 102
    assert few["latency"]["p95_ms"] is None
    good = compute_reliability("p", [_row(i) for i in range(20)], t, t)
    assert good["state"] == "observed_reliable"
    assert good["latency"]["p95_ms"] == 118
    one_run = compute_reliability("p", [_row(i, run_id=1) for i in range(30)], t, t)
    assert one_run["state"] == "insufficient_data"
    degraded = compute_reliability(
        "p",
        [_row(i) for i in range(17)] + [_row(i, connectivity="timeout") for i in range(3)],
        t,
        t,
    )
    assert degraded["state"] == "observed_degraded"
    assert degraded["timeouts"] == 3
    bad = compute_reliability(
        "p",
        [_row(i) for i in range(10)] + [_row(i, connectivity="provider_error") for i in range(10)],
        t,
        t,
    )
    assert bad["state"] == "observed_unreliable"
    blocked = compute_reliability(
        "p",
        [_row(i, connectivity="network_blocked", environment_restricted=True) for i in range(30)],
        t,
        t,
    )
    assert blocked["state"] == "unknown"
    assert blocked["sample_size"] == 0


# ---------------------------------------------------------------- security and persistence


def test_credentials_never_appear_in_output_logs_or_db(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    e = Env(tmp_path)
    try:
        with TestClient(e.app) as c:
            e.transport.script(
                INFURA_HOST,
                Scripted(error=TransportError("connection", INFURA_HOST, "DNS resolution failed")),
            )
            r = c.post(f"{API}/providers/probe-runs", json={"providers": ["infura-eth"]})
            texts = [
                r.text,
                c.get(f"{API}/providers/profiles").text,
                c.get(f"{API}/providers/infura-eth/profile").text,
                c.get(f"{API}/providers/probe/preview").text,
            ]
        with e.engine.connect() as conn:
            dump = "\n".join(conn.connection.iterdump())  # type: ignore[union-attr]
        for t in [*texts, dump, caplog.text]:
            assert "test-key-not-real" not in t
    finally:
        e.engine.dispose()


def test_failed_run_is_auditable_and_history_persists(tmp_path: Path) -> None:
    e = Env(tmp_path, second=False)
    try:
        with TestClient(e.app) as c:
            ctx = e.app.state.bip

            def boom(plan: Any) -> Any:
                raise RuntimeError("boom")

            original = ctx.prober.run
            object.__setattr__(ctx.prober, "run", boom)
            with pytest.raises(RuntimeError):
                c.post(f"{API}/providers/probe-runs", json={})
            object.__setattr__(ctx.prober, "run", original)
            run(c)
        # same database, new app: history survives restarts
        from bip.api.app import create_app
        from tests.api.conftest import roomy_guard

        app2 = create_app(
            e.settings,
            engine=e.engine,
            transport=e.transport,
            disk_guard=roomy_guard(e.settings.resolved_data_root),
            environ={},
            rpc_sleep=lambda _s: None,
        )
        with TestClient(app2) as c2:
            runs = c2.get(f"{API}/providers/probe-runs").json()["items"]
            assert [x["status"] for x in runs] == ["completed", "failed"]
            assert "RuntimeError" in (runs[1]["failure_summary"] or "")
    finally:
        e.engine.dispose()


def test_catalog_and_profiles_endpoints(client: TestClient) -> None:
    cat = client.get(f"{API}/providers/capabilities").json()
    assert [c["id"] for c in cat["chains"]["ethereum"]] == [c.id for c in caps.ETHEREUM]
    profiles = client.get(f"{API}/providers/profiles").json()["items"]
    by = {p["id"]: p for p in profiles}
    assert by["infura-eth"]["connectivity"] == "authentication_required"
    assert by["publicnode-eth"]["connectivity"] == "not_tested"
    assert by["etherscan"]["adapter_available"] is False
    assert by["publicnode-eth"]["documented"]["basis"] == "search_derived"
    assert client.get(f"{API}/providers/nope-x/profile").status_code == 404
    # existing provider routes still work after the new static paths were added
    assert client.get(f"{API}/providers/publicnode-eth").status_code == 200
    assert client.get(f"{API}/providers/probe-runs/not-a-run").status_code == 422
    assert (
        client.get(f"{API}/providers/probe-runs/{'0' * 8}-0000-0000-0000-{'0' * 12}").status_code
        == 404
    )


def test_migration_v4_upgrades_a_v3_database(tmp_path: Path) -> None:
    import sqlite3

    from bip.db.engine import create_db_engine, database_path, migrate

    settings = load_settings(data_root=tmp_path / "d", env="test")
    engine = create_db_engine(settings)
    with engine.begin() as conn:  # simulate a v3 database with Stage 3 evidence
        for t in (*schema.V6_TABLES, *reversed(schema.V5_TABLES), *schema.V4_TABLES):
            t.drop(conn)
        conn.exec_driver_sql("DELETE FROM schema_migrations WHERE version >= 4")
        conn.exec_driver_sql(
            "INSERT INTO evidence_verifications (chain, tx_id, checked_at, outcome, "
            "independent_providers, providers_json, differences_json) VALUES "
            "('ethereum', 'x', '2026-11-03T00:00:00.000000+00:00', 'cross_verified', 2, '[]', "
            "'[]')"
        )
    engine.dispose()
    con = sqlite3.connect(database_path(settings))
    assert [r[0] for r in con.execute("SELECT version FROM schema_migrations")] == [1, 2, 3]
    con.close()
    engine = create_db_engine(settings)
    assert migrate(engine) == schema.SCHEMA_VERSION
    with engine.connect() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master")}
        kept = conn.exec_driver_sql("SELECT outcome FROM evidence_verifications").scalar_one()
    assert {t.name for t in schema.V4_TABLES} <= tables
    assert kept == "cross_verified"
    engine.dispose()


def test_solana_rate_limit_rpc_error_and_unhealthy_node(env: Env, client: TestClient) -> None:
    router = env.app.state.bip.router
    nxt = router._next_id + 1  # pyright: ignore[reportPrivateUsage]
    genesis = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
    env.transport.script(
        SOL_HOST,
        Scripted(body=json.dumps({"jsonrpc": "2.0", "id": nxt, "result": genesis}).encode()),
        Scripted(
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": nxt + 1,
                    "error": {"code": -32005, "message": "Node is behind by 90 slots"},
                }
            ).encode()
        ),
    )
    r = run(client, providers=["solana-public"], capabilities=["health"])
    o = obs(r, "solana-public", "health")
    assert (o["status"], o["failure_kind"]) == ("supported", "node_unhealthy")
    nxt = router._next_id + 1  # pyright: ignore[reportPrivateUsage]
    env.transport.script(
        SOL_HOST,
        Scripted(
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": nxt,
                    "error": {"code": 429, "message": "Too many requests"},
                }
            ).encode()
        ),
    )
    r2 = run(client, providers=["solana-public"])
    first = obs(r2, "solana-public", "network_identity")
    assert first["connectivity"] == "rate_limited"
    assert len([m for h, m in env.transport.requests if h == SOL_HOST]) == 3  # 2 + 1, no storm


def test_live_probe_cli_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from bip.tools import probe

    monkeypatch.delenv("BIP_LIVE_RPC", raising=False)
    assert probe.main([]) == 2
    assert "Refusing to run" in capsys.readouterr().out


# ---------------------------------------------------------------- review regressions


def test_capability_filter_always_checks_network_identity_first(
    env: Env, client: TestClient
) -> None:
    env.eth.chain_id = "0xaa36a7"  # Sepolia
    r = run(client, providers=["publicnode-eth"], capabilities=["latest_block"])
    assert obs(r, "publicnode-eth", "chain_id")["connectivity"] == "wrong_network"
    assert obs(r, "publicnode-eth", "latest_block")["status"] == "not_tested"
    assert r["requests_used"] == 1


def test_later_partial_runs_do_not_hide_earlier_evidence(env: Env, client: TestClient) -> None:
    run(client, providers=["publicnode-eth"])
    run(client, providers=["publicnode-eth"], capabilities=["chain_id"])  # skips the rest
    prof = client.get(f"{API}/providers/publicnode-eth/profile").json()
    matrix = {c["id"]: c["observation"] for c in prof["capabilities"]}
    assert matrix["historical_block"]["status"] == "supported"
    assert prof["historical_coverage"]["label"] == "historical_sample_observed"
    assert prof["coverage"]["supported"] >= 8


def test_environment_block_banner_clears_after_a_later_success(
    env: Env, client: TestClient
) -> None:
    env.transport.script(
        ETH_HOST, Scripted(error=TransportError("connection", ETH_HOST, "DNS resolution failed"))
    )
    run(client, providers=["publicnode-eth"])
    assert client.get(f"{API}/providers/profiles").json()["last_environment_block"] is not None
    run(client, providers=["publicnode-eth"])
    assert client.get(f"{API}/providers/profiles").json()["last_environment_block"] is None
    assert client.get(f"{API}/providers/probe/preview").json()["network_note"] is None


def test_rejected_finalized_block_never_feeds_consistency(tmp_path: Path) -> None:
    e = Env(tmp_path)
    try:
        with TestClient(e.app) as c:
            n = e.eth2.head - 64
            e.eth2.blocks[n] = {**e.eth2.blocks[n], "number": hex(e.eth2.head + 5)}
            r = run(c, providers=["publicnode-eth", "infura-eth"])
            assert obs(r, "infura-eth", "finalized_block")["connectivity"] == "invalid_response"
            assert not [
                x for x in r["consistency_checks"] if x["comparison"] == "finalized_block_hash"
            ]
    finally:
        e.engine.dispose()


def test_preview_rejects_malformed_query_with_422(client: TestClient) -> None:
    for q in ("provider=Bad_ID", "capability=X", "provider="):
        r = client.get(f"{API}/providers/probe/preview?{q}")
        assert r.status_code == 422, (q, r.status_code)


def test_rejected_parameters_are_not_historical_access_failure(
    env: Env, client: TestClient
) -> None:
    router = env.app.state.bip.router
    nxt = router._next_id + 1  # pyright: ignore[reportPrivateUsage]
    head = env.eth.blocks[env.eth.head]
    env.transport.script(
        ETH_HOST,
        Scripted(body=json.dumps({"jsonrpc": "2.0", "id": nxt, "result": "0x1"}).encode()),
        Scripted(body=json.dumps({"jsonrpc": "2.0", "id": nxt + 1, "result": head}).encode()),
        Scripted(
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": nxt + 2,
                    "error": {"code": -32602, "message": "invalid argument"},
                }
            ).encode()
        ),
    )
    run(client, providers=["publicnode-eth"], capabilities=["latest_block", "historical_block"])
    prof = client.get(f"{API}/providers/publicnode-eth/profile").json()
    assert prof["historical_coverage"]["label"] == "insufficient_evidence"
