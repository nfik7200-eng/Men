"""Budget-aware, read-only provider capability prober.

A probe run is always started explicitly (API or CLI), never at startup or on page loads. For
each selected provider it sends at most ``probe.max_calls_per_provider`` requests (hard ceiling 8)
through :meth:`ProviderRouter.probe`: pinned provider, no retries, no failover, the router's
host allow-list, IP-pinned transport, redirect refusal and size caps, plus a per-provider
spacing of at least ``probe.min_interval_s``. The whole run is bounded by
``probe.max_calls_per_run`` (≤ 48), ``probe.max_run_seconds`` (≤ 90) and runs at most
``probe.max_concurrent_providers`` (≤ 2) providers in parallel.

Failure handling is deliberately conservative:

* a DNS or proxy refusal is an *environment* restriction: the provider's availability stays
  unknown, and the provider is not probed further in that run (no identical failures);
* HTTP 429 / JSON-RPC rate limiting stops that provider for the run (Retry-After is honoured by
  the router's cooldown); it never marks a provider unavailable;
* only an explicit "method not found" (-32601) makes a capability ``unsupported``;
* malformed, contradictory or wrong-network answers are never ``supported``.

Transaction hashes and signatures used for lookups come from the provider's own live answers
in the same run (latest block / signature history), never from stored records, so offline
fixture identifiers can never be sent to a real provider.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bip.chains.common import ParseError, SourceInfo, arr, integer, obj, text
from bip.chains.ethereum import parse as eth
from bip.chains.identity import EXPECTED_IDENTITY, identity_matches
from bip.chains.solana import parse as sol
from bip.config.settings import ProbeSettings
from bip.db.errors import InvalidRecordError
from bip.db.types import utcnow
from bip.providers import capabilities as caps
from bip.providers.registry import ProviderSpec, Registry
from bip.providers.state import ProviderState
from bip.router.router import ProbeResponse, ProviderRouter

J = dict[str, Any]

ENVIRONMENT_RESTRICTION = (
    "Live verification could not be completed because of an environment network restriction. "
    "Provider availability remains unknown."
)
CONNECTION_UNKNOWN = (
    "The connection could not be established, and BIP cannot tell whether the local environment "
    "or the provider caused it. Provider availability remains unknown."
)


@dataclass
class Observation:
    provider_id: str
    chain: str
    network: str | None
    infrastructure: str | None
    capability: str
    method: str | None
    status: str
    connectivity: str
    explanation: str
    validation: str = "not_applicable"
    failure_kind: str | None = None
    environment_restricted: bool = False
    http_status: int | None = None
    rpc_error_code: int | None = None
    latency_ms: int | None = None
    response_bytes: int | None = None
    reference: J | None = None
    observed_at: datetime = field(default_factory=utcnow)

    @property
    def requested(self) -> bool:
        """A request actually left BIP for this observation."""
        return self.method is not None and self.status != "not_tested"

    def public(self) -> J:
        return {
            "provider_id": self.provider_id,
            "chain": self.chain,
            "network": self.network,
            "infrastructure": self.infrastructure,
            "capability": self.capability,
            "method": self.method,
            "status": self.status,
            "connectivity": self.connectivity,
            "explanation": self.explanation,
            "validation": self.validation,
            "failure_kind": self.failure_kind,
            "environment_restricted": self.environment_restricted,
            "http_status": self.http_status,
            "rpc_error_code": self.rpc_error_code,
            "latency_ms": self.latency_ms,
            "response_bytes": self.response_bytes,
            "reference": self.reference,
            "observed_at": self.observed_at.isoformat(),
        }


@dataclass
class ProviderPlan:
    spec: ProviderSpec
    requests: list[caps.Capability]
    over_budget: list[caps.Capability]
    unselected: list[caps.Capability]


@dataclass
class ProbePlan:
    providers: list[ProviderPlan]
    ineligible: dict[str, str]
    budget: J

    @property
    def max_requests(self) -> int:
        return min(sum(len(p.requests) for p in self.providers), self.budget["max_calls_per_run"])


@dataclass
class ProviderResult:
    provider_id: str
    chain: str
    observations: list[Observation]
    connectivity: str
    requests_used: int
    values: J
    stop_reason: str | None
    requests_skipped: int = 0
    """Planned requests that were not sent (stopped, budget, or missing sample)."""


class RunBudget:
    """Global request and time budget shared by the provider workers (thread-safe)."""

    def __init__(self, max_calls: int, seconds: float, clock: Callable[[], float]) -> None:
        self._lock = threading.Lock()
        self._max = max_calls
        self._deadline = clock() + seconds
        self._clock = clock
        self.used = 0

    def take(self) -> str | None:
        with self._lock:
            if self._clock() >= self._deadline:
                return "run time budget reached"
            if self.used >= self._max:
                return f"run request budget of {self._max} reached"
            self.used += 1
            return None


def eligibility(
    spec: ProviderSpec, state: ProviderState | None, router: ProviderRouter
) -> str | None:
    """Why this provider cannot be probed, or ``None`` when it can."""
    if not spec.has_adapter:
        return "no RPC adapter for this source"
    if spec.chains[0] not in caps.CATALOG:
        return "no capability catalogue for this chain"
    view = router.view(spec, {spec.id: state} if state else {})
    if not view.enabled:
        return "provider is disabled"
    if view.resolution.endpoint is None:
        if spec.requires_credentials:
            return f"requires configuration: {view.resolution.reason}"
        return view.resolution.reason
    return None


class Prober:
    def __init__(
        self,
        registry: Registry,
        router: ProviderRouter,
        settings: ProbeSettings,
        *,
        states: Callable[[], dict[str, ProviderState]] = dict,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._router = router
        self._cfg = settings
        self._states = states
        self._clock = clock

    def budget(self, max_calls_per_provider: int | None = None) -> J:
        per = self._cfg.max_calls_per_provider
        if max_calls_per_provider is not None:
            per = min(per, max_calls_per_provider)  # can only be lowered
        return {
            "max_calls_per_provider": per,
            "max_calls_per_run": self._cfg.max_calls_per_run,
            "max_run_seconds": self._cfg.max_run_seconds,
            "max_concurrent_providers": self._cfg.max_concurrent_providers,
            "request_timeout_s": self._cfg.request_timeout_s,
            "retries": self._cfg.retries,
            "max_response_bytes": self._cfg.max_response_bytes,
            "min_interval_s": self._cfg.min_interval_s,
        }

    # ------------------------------------------------------------------ planning

    def plan(
        self,
        provider_ids: Iterable[str] | None = None,
        capability_ids: Iterable[str] | None = None,
        *,
        max_calls_per_provider: int | None = None,
    ) -> ProbePlan:
        states = self._states()
        budget = self.budget(max_calls_per_provider)
        wanted_caps = set(capability_ids) if capability_ids is not None else None
        if wanted_caps is not None:
            known = caps.capability_ids("ethereum") | caps.capability_ids("solana")
            unknown = sorted(wanted_caps - known)
            if unknown:
                raise InvalidRecordError(f"unknown capability identifiers: {unknown}")
        explicit = provider_ids is not None
        specs = (
            [self._registry.get(p) for p in dict.fromkeys(provider_ids or [])]
            if explicit
            else [s for s in self._registry.all() if s.has_adapter]
        )
        ineligible: dict[str, str] = {}
        plans: list[ProviderPlan] = []
        for spec in specs:
            reason = eligibility(spec, states.get(spec.id), self._router)
            if reason is not None:
                ineligible[spec.id] = reason
                continue
            chain = spec.chains[0]
            selected: list[caps.Capability] = []
            unselected: list[caps.Capability] = []
            for c in caps.requestable(chain):
                kids = {d.id for d in caps.derived(chain, c.id)}
                wanted = wanted_caps is None or c.id in wanted_caps or bool(kids & wanted_caps)
                if wanted and c.method in spec.methods:
                    selected.append(c)
                else:
                    unselected.append(c)
            per = budget["max_calls_per_provider"]
            plans.append(ProviderPlan(spec, selected[:per], selected[per:], unselected))
        if explicit and ineligible:
            detail = "; ".join(f"{k}: {v}" for k, v in ineligible.items())
            raise InvalidRecordError(f"cannot probe: {detail}")
        return ProbePlan(plans, ineligible, budget)

    # ------------------------------------------------------------------ execution

    def run(self, plan: ProbePlan) -> list[ProviderResult]:
        budget = RunBudget(
            plan.budget["max_calls_per_run"], plan.budget["max_run_seconds"], self._clock
        )
        workers = max(1, min(plan.budget["max_concurrent_providers"], len(plan.providers)))
        if not plan.providers:
            return []
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bip-probe") as pool:
            futures = [pool.submit(self._probe_provider, p, budget) for p in plan.providers]
            return [f.result() for f in futures]

    def _probe_provider(self, p: ProviderPlan, budget: RunBudget) -> ProviderResult:
        spec = p.spec
        chain = spec.chains[0]
        ctx: J = {}
        out: list[Observation] = []
        used = 0
        stop: str | None = None

        def base(cap: caps.Capability, **kw: Any) -> Observation:
            return Observation(
                provider_id=spec.id,
                chain=chain,
                network=spec.network,
                infrastructure=spec.infrastructure,
                capability=cap.id,
                method=cap.method,
                **kw,
            )

        for cap in p.requests:
            if stop is not None:
                out.append(
                    base(
                        cap,
                        status="not_tested",
                        connectivity="not_tested",
                        explanation=f"Not tested: {stop}.",
                    )
                )
                out += self._derived_not_tested(spec, cap, f"parent not tested: {stop}")
                continue
            params, missing = _params(chain, cap.id, ctx, self._cfg)
            if params is None:
                out.append(
                    base(
                        cap,
                        status="not_tested",
                        connectivity="not_tested",
                        explanation=f"Not tested: no safe sample ({missing}).",
                    )
                )
                out += self._derived_not_tested(spec, cap, "parent not tested")
                continue
            refusal = budget.take()
            if refusal is not None:
                stop = refusal
                out.append(
                    base(
                        cap,
                        status="not_tested",
                        connectivity="not_tested",
                        explanation=f"Not tested: {refusal}.",
                    )
                )
                out += self._derived_not_tested(spec, cap, f"parent not tested: {refusal}")
                continue
            resp = self._router.probe(
                spec,
                cap.method or "",
                params,
                timeout_s=self._cfg.request_timeout_s,
                max_bytes=self._cfg.max_response_bytes,
                min_interval_s=self._cfg.min_interval_s,
            )
            if resp.sent:
                used += 1
            obs = classify(spec, cap, resp, ctx)
            out.append(obs)
            out += _derive(spec, cap, obs, ctx)
            stop = _stop_reason(obs) if stop is None else stop
        for cap in p.over_budget:
            out.append(
                base(
                    cap,
                    status="not_tested",
                    connectivity="not_tested",
                    explanation="Not tested: per-provider request cap of "
                    f"{len(p.requests)} reached (select fewer capabilities to test it).",
                )
            )
            out += self._derived_not_tested(spec, cap, "parent over budget")
        for cap in p.unselected:
            reason = (
                "method not in this provider's read-only allow-list"
                if cap.method not in spec.methods
                else "not selected for this run"
            )
            out.append(
                base(
                    cap,
                    status="not_tested",
                    connectivity="not_tested",
                    explanation=f"Not tested: {reason}.",
                )
            )
            out += self._derived_not_tested(spec, cap, reason)
        skipped = len(p.requests) - used
        return ProviderResult(spec.id, chain, out, _connectivity(out), used, ctx, stop, skipped)

    def _derived_not_tested(
        self, spec: ProviderSpec, parent: caps.Capability, why: str
    ) -> list[Observation]:
        return [
            Observation(
                spec.id,
                parent.chain,
                spec.network,
                spec.infrastructure,
                d.id,
                None,
                "not_tested",
                "not_tested",
                f"Not tested: {why}.",
            )
            for d in caps.derived(parent.chain, parent.id)
        ]


# ---------------------------------------------------------------------- request parameters


def _params(chain: str, cap: str, ctx: J, cfg: ProbeSettings) -> tuple[Any, str | None]:
    if chain == "ethereum":
        head = ctx.get("head_number")
        match cap:
            case "chain_id" | "block_number":
                return [], None
            case "latest_block":
                return ["latest", False], None
            case "finalized_block":
                return ["finalized", False], None
            case "historical_block":
                if head is None:
                    return None, "head block number unknown"
                target = max(0, (head - cfg.ethereum_history_lookback_blocks) // 1000 * 1000)
                ctx["historical_target"] = target
                return [hex(target), False], None
            case "transaction_lookup" | "transaction_receipt":
                tx = ctx.get("sample_tx")
                return ([tx], None) if tx else (None, "latest block listed no transaction")
            case "event_logs":
                if head is None:
                    return None, "head block number unknown"
                return [
                    {"fromBlock": hex(head), "toBlock": hex(head), "topics": [eth.TRANSFER_TOPIC]}
                ], None
            case "native_balance" | "contract_code":
                return [caps.ETH_PROBE_ADDRESS, "latest"], None
            case _:
                return None, "unknown capability"
    else:
        fin = ctx.get("finalized_slot")
        block_opts = {
            "commitment": "finalized",
            "transactionDetails": "none",
            "rewards": False,
            "maxSupportedTransactionVersion": 0,
        }
        match cap:
            case "network_identity" | "health" | "version":
                return [], None
            case "current_slot" | "block_height" | "latest_blockhash":
                return [{"commitment": "finalized"}], None
            case "finalized_block":
                return ([fin, block_opts], None) if fin is not None else (None, "slot unknown")
            case "historical_block":
                if fin is None:
                    return None, "finalized slot unknown"
                target = max(0, (fin - cfg.solana_history_lookback_slots) // 1000 * 1000)
                ctx["historical_target"] = target
                return [target, block_opts], None
            case "signature_history":
                return [caps.SOL_PROBE_ADDRESS, {"limit": 1, "commitment": "finalized"}], None
            case "transaction_lookup":
                sig = ctx.get("sample_signature")
                if not sig:
                    return None, "no signature returned live by signature_history"
                return [
                    sig,
                    {
                        "encoding": "json",
                        "commitment": "finalized",
                        "maxSupportedTransactionVersion": 0,
                    },
                ], None
            case "native_balance":
                return [caps.SOL_PROBE_ADDRESS, {"commitment": "finalized"}], None
            case _:
                return None, "unknown capability"


# ---------------------------------------------------------------------- classification


def classify(spec: ProviderSpec, cap: caps.Capability, r: ProbeResponse, ctx: J) -> Observation:
    o = Observation(
        spec.id,
        cap.chain,
        spec.network,
        spec.infrastructure,
        cap.id,
        cap.method,
        "inconclusive",
        "not_tested",
        "",
        http_status=r.http_status,
        rpc_error_code=r.rpc_error_code,
        latency_ms=r.latency_ms,
        response_bytes=r.response_bytes,
    )
    if not r.sent:
        o.status = "not_tested"
        o.connectivity = "rate_limited" if r.refused and "cooldown" in r.refused else "not_tested"
        o.explanation = f"Not sent: {r.refused}."
        return o
    if r.transport_failure is not None:
        return _transport(o, r)
    if (
        r.http_status is not None
        and r.rpc_error_class is not None
        and r.rpc_error_code is None
        and not (200 <= r.http_status < 300)
    ):
        return _http(o, r, spec)
    if r.rpc_error_class is not None:
        return _rpc_error(o, r, cap)
    try:
        return _validate(o, cap, r.result, ctx)
    except (ParseError, ValueError, TypeError, KeyError) as exc:
        o.status = "inconclusive"
        o.connectivity = "invalid_response"
        o.validation = "failed"
        o.failure_kind = "validation_failed"
        o.explanation = f"Response failed validation ({str(exc)[:150]}); not counted as support."
        return o


def _transport(o: Observation, r: ProbeResponse) -> Observation:
    kind, detail = r.transport_failure, (r.transport_detail or "")
    o.status = "inconclusive"
    if kind == "proxy":
        o.connectivity, o.failure_kind, o.environment_restricted = (
            "network_blocked",
            "proxy_refused",
            True,
        )
        o.explanation = f"Proxy refused the connection. {ENVIRONMENT_RESTRICTION}"
    elif kind == "connection" and "DNS" in detail:
        o.connectivity, o.failure_kind, o.environment_restricted = (
            "network_blocked",
            "dns_failure",
            True,
        )
        o.explanation = f"DNS resolution failed. {ENVIRONMENT_RESTRICTION}"
    elif kind == "connection":
        o.connectivity, o.failure_kind = "network_blocked", "connection_failed"
        o.explanation = CONNECTION_UNKNOWN
    elif kind == "unsafe_destination":
        o.connectivity, o.failure_kind = "network_blocked", "unsafe_destination"
        o.explanation = (
            "The host resolved to a non-public address, so BIP refused to connect "
            "(SSRF protection). Provider availability remains unknown."
        )
    elif kind == "timeout":
        o.connectivity, o.failure_kind = "timeout", "timeout"
        o.explanation = (
            "No complete response within the probe timeout. A timeout alone does "
            "not show the provider is unreliable or unavailable."
        )
    elif kind == "tls":
        o.connectivity, o.failure_kind = "tls_error", "tls_error"
        o.explanation = "TLS handshake or certificate verification failed."
    elif kind == "redirect":
        o.connectivity, o.failure_kind = "provider_error", "redirect_refused"
        o.explanation = "The endpoint answered with a redirect, which BIP never follows."
    elif kind == "too_large":
        o.connectivity, o.failure_kind = "reachable", "response_too_large"
        o.explanation = (
            "The response exceeded the probe size cap and was discarded; support "
            "is not established by this request."
        )
    else:  # pragma: no cover
        o.connectivity, o.failure_kind = "provider_error", str(kind)
        o.explanation = "Transport failure."
    return o


def _http(o: Observation, r: ProbeResponse, spec: ProviderSpec) -> Observation:
    status = r.http_status or 0
    o.status = "inconclusive"
    o.failure_kind = f"http_{status}"
    if status == 429:
        o.connectivity = "rate_limited"
        after = f" Retry-After: {r.retry_after_s:g}s (honoured)." if r.retry_after_s else ""
        o.explanation = (
            "HTTP 429: the provider rate-limited BIP. Probing of this provider "
            f"stopped for this run; it is not marked unavailable.{after}"
        )
    elif status == 401:
        o.connectivity = "unauthorized"
        o.explanation = "HTTP 401: credentials were missing or rejected" + (
            " (check the configured endpoint key)." if spec.requires_credentials else "."
        )
    elif status == 403:
        o.connectivity = "unauthorized"
        o.explanation = (
            "HTTP 403: the endpoint denied access (key, IP, region or plan policy). "
            "This does not show that the provider is down."
        )
    elif status >= 500:
        o.connectivity = "provider_error"
        o.explanation = f"HTTP {status}: the provider reported a server error."
    else:
        o.connectivity = "provider_error"
        o.explanation = f"HTTP {status}: the endpoint did not accept the JSON-RPC request."
    return o


def _rpc_error(o: Observation, r: ProbeResponse, cap: caps.Capability) -> Observation:
    cls = r.rpc_error_class
    msg = (r.rpc_error_message or "").lower()
    o.connectivity = "reachable"
    o.failure_kind = f"rpc_{cls}"
    code = f" (code {r.rpc_error_code})" if r.rpc_error_code is not None else ""
    if cap.method == "getHealth" and ("behind" in msg or "unhealthy" in msg):
        o.status = "supported"
        o.validation = "passed"
        o.failure_kind = "node_unhealthy"
        o.explanation = f"getHealth works and reported the node as not healthy{code}."
        return o
    o.status = "inconclusive"
    if cls == "method_unsupported":
        o.status = "unsupported"
        o.explanation = f"The provider reported the method as not available{code}."
    elif cls == "rate_limited":
        o.connectivity = "rate_limited"
        o.explanation = (
            f"JSON-RPC rate-limit error{code}. Probing of this provider stopped for "
            "this run; it is not marked unavailable."
        )
    elif cls == "not_available":
        o.explanation = (
            f"The provider does not have the requested data{code} (for example a "
            "skipped slot or data outside its history). Support is not established "
            "or ruled out."
        )
    elif cls == "invalid_request":
        o.explanation = f"The provider rejected the parameters{code}; support is not established."
    elif cls == "malformed":
        o.connectivity = "invalid_response"
        o.validation = "failed"
        o.explanation = "The response was not valid JSON-RPC; not counted as support."
    else:
        o.connectivity = "provider_error"
        o.explanation = f"The provider returned an error{code}; support is not established."
    return o


def _ok(o: Observation, explanation: str, reference: J | None = None) -> Observation:
    o.status = "supported"
    o.connectivity = "reachable"
    o.validation = "passed"
    o.explanation = explanation
    o.reference = reference
    return o


def _validate(o: Observation, cap: caps.Capability, result: object, ctx: J) -> Observation:
    m = cap.method
    if m == "eth_chainId":
        value = text(result, "eth_chainId", max_len=20)
        ctx["chain_id"] = value
        if not identity_matches("ethereum", value):
            o.status, o.connectivity, o.validation = "inconclusive", "wrong_network", "failed"
            o.failure_kind = "wrong_network"
            o.explanation = (
                f"eth_chainId returned {value!r}, expected "
                f"{EXPECTED_IDENTITY['ethereum'].label}. This endpoint does not serve "
                "the registered network."
            )
            o.reference = {"chain_id": value}
            return o
        return _ok(o, "Returned chain id 1 (Ethereum mainnet).", {"chain_id": value})
    if m == "getGenesisHash":
        value = text(result, "getGenesisHash", max_len=44)
        ctx["genesis"] = value
        if not identity_matches("solana", value):
            o.status, o.connectivity, o.validation = "inconclusive", "wrong_network", "failed"
            o.failure_kind = "wrong_network"
            o.explanation = "Genesis hash differs from Solana mainnet-beta."
            o.reference = {"genesis_hash": value}
            return o
        return _ok(o, "Returned the mainnet-beta genesis hash.", {"genesis_hash": value})
    if m == "eth_getBlockByNumber":
        if result is None:
            o.status, o.connectivity = "inconclusive", "reachable"
            o.failure_kind = "null_result"
            which = {
                "finalized_block": "the 'finalized' tag",
                "historical_block": f"block {ctx.get('historical_target')}",
            }.get(cap.id, "the block")
            o.explanation = (
                f"The provider returned null for {which}; the data may not be "
                "served by this endpoint. Support is not established."
            )
            if cap.id == "historical_block":
                o.reference = {"block": ctx.get("historical_target"), "distance": _distance(ctx)}
            return o
        h = eth.parse_block_header(result)
        if cap.id == "historical_block" and h.number != ctx.get("historical_target"):
            raise ValueError("returned block number differs from the requested one")
        ref: J = {"block": h.number, "hash": h.hash}
        if cap.id == "latest_block":
            ctx["head_number"], ctx["head_hash"] = h.number, h.hash
            txs = obj(result, "block").get("transactions")
            hashes = [
                t for t in arr(txs or [], "block.transactions", max_len=5000) if isinstance(t, str)
            ]
            if hashes:
                ctx["sample_tx"] = eth.hash32(hashes[0], "block.transactions[0]")
            return _ok(o, f"Block {h.number} header validated.", ref)
        if cap.id == "finalized_block":
            ctx["finalized"] = (h.number, h.hash)
            head = ctx.get("head_number")
            if head is not None and h.number > head:
                raise ValueError("finalized block is above the latest block")
            return _ok(o, f"Finalized block {h.number} header validated.", ref)
        ctx["historical"] = (h.number, h.hash)
        ref["distance_from_head"] = _distance(ctx)
        return _ok(
            o,
            f"Historical block {h.number} ({ref['distance_from_head']} blocks below "
            "the observed head) returned. This is one sample, not proof of full archive "
            "access.",
            ref,
        )
    if m == "eth_getTransactionByHash":
        t = obj(result, "tx")
        if eth.hash32(t.get("hash"), "tx.hash") != ctx.get("sample_tx"):
            raise ValueError("returned transaction hash differs from the requested one")
        return _ok(o, "Transaction found and its hash matches.", {"tx": ctx["sample_tx"]})
    if m == "eth_getTransactionReceipt":
        rc = obj(result, "receipt")
        if eth.hash32(rc.get("transactionHash"), "receipt.transactionHash") != ctx["sample_tx"]:
            raise ValueError("receipt belongs to another transaction")
        eth.quantity(rc.get("status"), "receipt.status")
        return _ok(o, "Receipt found for the sample transaction.", {"tx": ctx["sample_tx"]})
    if m == "eth_getLogs":
        logs = arr(result, "logs", max_len=20_000)
        n20 = n721 = 0
        for lg in logs:
            lo = obj(lg, "log")
            topics = arr(lo.get("topics"), "log.topics", max_len=8)
            if not topics or topics[0] != eth.TRANSFER_TOPIC:
                raise ValueError("log does not match the requested Transfer topic")
            if eth.quantity(lo.get("blockNumber"), "log.blockNumber") != ctx["head_number"]:
                raise ValueError("log outside the requested block")
            n20 += len(topics) == 3
            n721 += len(topics) == 4
        ctx["log_counts"] = (n20, n721)
        return _ok(
            o,
            f"{len(logs)} Transfer logs returned for block {ctx['head_number']} "
            f"({n20} ERC-20-shaped, {n721} ERC-721-shaped).",
            {"block": ctx["head_number"], "logs": len(logs)},
        )
    if m == "eth_getBalance":
        eth.quantity(result, "eth_getBalance")
        return _ok(o, "Balance returned as a valid quantity.")
    if m == "eth_blockNumber":
        n = eth.quantity(result, "eth_blockNumber")
        return _ok(o, f"Head block number {n}.", {"block": n})
    if m == "eth_getCode":
        code = text(result, "eth_getCode", max_len=100_000)
        if not code.startswith("0x"):
            raise ValueError("code is not hex")
        return _ok(o, "Bytecode read returned valid hex.")
    if m == "getSlot":
        ctx["finalized_slot"] = integer(result, "getSlot")
        return _ok(o, f"Finalized slot {ctx['finalized_slot']}.", {"slot": ctx["finalized_slot"]})
    if m == "getBlock":
        target = (
            ctx["finalized_slot"] if cap.id == "finalized_block" else ctx.get("historical_target")
        )
        if result is None:
            o.status, o.connectivity, o.failure_kind = "inconclusive", "reachable", "null_result"
            o.explanation = f"No block returned for slot {target}; support is not established."
            o.reference = {"slot": target}
            return o
        b = obj(result, "block")
        blockhash = text(b.get("blockhash"), "block.blockhash", max_len=44)
        integer(b.get("parentSlot"), "block.parentSlot")
        ref = {"slot": target, "blockhash": blockhash}
        if cap.id == "finalized_block":
            ctx["finalized_block"] = (target, blockhash)
            return _ok(o, f"Finalized block at slot {target} validated.", ref)
        ctx["historical"] = (target, blockhash)
        ref["distance_from_finalized"] = ctx["finalized_slot"] - target
        return _ok(
            o,
            f"Historical block at slot {target} ({ref['distance_from_finalized']} slots "
            "below finalized) returned. One sample, not proof of archive access.",
            ref,
        )
    if m == "getSignaturesForAddress":
        sigs = sol.parse_signatures(result)
        if len(sigs) > 1:
            raise ValueError("more signatures than the requested limit")
        if sigs:
            ctx["sample_signature"] = sigs[0].signature
        return _ok(
            o,
            f"Signature history returned {len(sigs)} entry (limit 1).",
            {"signature": sigs[0].signature} if sigs else None,
        )
    if m == "getTransaction":
        if result is None:
            o.status, o.connectivity, o.failure_kind = "inconclusive", "reachable", "null_result"
            o.explanation = "The sample transaction was not returned; support is not established."
            return o
        keys = obj(
            obj(obj(result, "tx").get("transaction"), "tx.transaction").get("message"), "message"
        ).get("accountKeys")
        fee_payer = text(arr(keys, "accountKeys", max_len=256)[0], "accountKeys[0]", max_len=44)
        parsed = sol.parse_transaction(result, wallet=fee_payer, src=_SRC, commitment="finalized")
        if parsed.tx.signature != ctx["sample_signature"]:
            raise ValueError("returned transaction signature differs from the requested one")
        kinds = [k for _, k in parsed.transfers]
        meta = obj(result, "tx").get("meta")
        ctx["tx_features"] = {
            "native": sum(k == "solana_system_transfer_ix" for k in kinds),
            "spl": sum(k.startswith("spl_") for k in kinds),
            "token_meta": isinstance(meta, dict)
            and "preTokenBalances" in meta
            and "postTokenBalances" in meta,
        }
        return _ok(
            o,
            f"Transaction {parsed.tx.signature[:12]}… parsed ({parsed.parse_status}).",
            {"signature": parsed.tx.signature, "slot": parsed.tx.slot},
        )
    if m == "getBalance":
        v = obj(result, "getBalance")
        integer(v.get("value"), "getBalance.value")
        return _ok(o, "Balance returned with a context slot.")
    if m == "getHealth":
        if result != "ok":
            raise ValueError("getHealth did not return 'ok'")
        return _ok(o, "Node reported healthy.")
    if m == "getVersion":
        v = obj(result, "getVersion")
        version = text(v.get("solana-core"), "solana-core", max_len=40)
        return _ok(o, f"Node version {version}.", {"solana_core": version})
    if m == "getBlockHeight":
        return _ok(o, f"Block height {integer(result, 'getBlockHeight')}.")
    if m == "getLatestBlockhash":
        value = obj(obj(result, "getLatestBlockhash").get("value"), "value")
        text(value.get("blockhash"), "blockhash", max_len=44)
        return _ok(o, "Latest blockhash returned.")
    raise ValueError(f"no validator for {m}")  # pragma: no cover


def _distance(ctx: J) -> int | None:
    head, target = ctx.get("head_number"), ctx.get("historical_target")
    return head - target if head is not None and target is not None else None


def _derive(
    spec: ProviderSpec, cap: caps.Capability, parent: Observation, ctx: J
) -> list[Observation]:
    out: list[Observation] = []
    for d in caps.derived(cap.chain, cap.id):
        o = Observation(
            spec.id,
            cap.chain,
            spec.network,
            spec.infrastructure,
            d.id,
            None,
            "not_tested",
            "not_tested",
            "",
        )
        if parent.status != "supported":
            o.explanation = f"Not tested: depends on {cap.id}, which was {parent.status}."
            out.append(o)
            continue
        if d.id in {"erc20_transfer_logs", "erc721_transfer_logs"}:
            n20, n721 = ctx.get("log_counts", (0, 0))
            n = n20 if d.id == "erc20_transfer_logs" else n721
            shape = "ERC-20" if d.id == "erc20_transfer_logs" else "ERC-721"
            if n:
                o.status, o.validation, o.connectivity = "supported", "passed", "reachable"
                o.explanation = f"{n} {shape}-shaped Transfer logs returned and validated."
            else:
                o.explanation = (
                    f"Not tested: the sampled block contained no {shape}-shaped Transfer logs."
                )
        else:
            f = ctx.get("tx_features", {})
            key = {
                "native_transfer_data": "native",
                "spl_transfer_data": "spl",
                "token_balance_changes": "token_meta",
            }[d.id]
            if f.get(key):
                o.status, o.validation, o.connectivity = "supported", "passed", "reachable"
                o.explanation = (
                    "Token balance metadata present in the sample transaction."
                    if key == "token_meta"
                    else f"{f[key]} decodable {'SOL' if key == 'native' else 'SPL'} "
                    "transfer instruction(s) in the sample transaction."
                )
            else:
                o.explanation = "Not tested: the sample transaction did not contain this data."
        out.append(o)
    return out


def _stop_reason(o: Observation) -> str | None:
    if o.environment_restricted or o.failure_kind in {"connection_failed", "unsafe_destination"}:
        return "the provider could not be reached from this environment"
    if o.failure_kind == "redirect_refused":
        return "the endpoint redirects, which BIP never follows"
    return {
        "wrong_network": "the endpoint serves a different network",
        "rate_limited": "the provider rate-limited BIP",
        "unauthorized": "access was denied",
        "timeout": "a request timed out (the run stays bounded)",
        "tls_error": "TLS failed",
    }.get(o.connectivity)


def _connectivity(obs: list[Observation]) -> str:
    requested = [o for o in obs if o.requested or o.connectivity == "rate_limited"]
    if any(o.connectivity == "wrong_network" for o in requested):
        return "wrong_network"
    if any(o.connectivity == "reachable" for o in requested):
        return "reachable"
    return requested[0].connectivity if requested else "not_tested"


_SRC = SourceInfo("probe", "getTransaction", utcnow(), None)
