"""Probe-run persistence, reliability snapshots, cross-provider consistency and profiles.

Reliability (method ``reliability@1.0.0``), computed per provider from stored capability
observations in a window (default: the last 30 days):

* **attempts**: observations where a request was sent and the failure was *not* an
  environment restriction (DNS/proxy). Environment-blocked attempts are counted separately and
  never count against the provider;
* **successes**: attempts with a valid JSON-RPC exchange (``reachable`` connectivity and
  validation not failed), including explicit "method not found" and "data not available"
  answers: the provider answered correctly;
* states: ``unknown`` (no attempts, only environment-blocked ones), ``insufficient_data``
  (< 20 attempts or < 3 distinct runs), else ``observed_reliable`` (success rate ≥ 0.95 and no
  invalid responses), ``observed_degraded`` (≥ 0.80, or any invalid response) or
  ``observed_unreliable`` (< 0.80);
* median latency needs ≥ 5 successful samples; p95 (nearest rank) needs ≥ 20.

Consistency checks compare like-for-like values from one run (chain identity; block hash at the
*same* block number or slot). Different heads are never a conflict. Providers in the same
infrastructure group are recorded with ``independent = false``. Nothing here touches the
Stage 3 evidence tables.
"""

from __future__ import annotations

import json
import math
import statistics
import uuid
from collections import Counter
from datetime import datetime, timedelta
from itertools import combinations
from typing import Any

from sqlalchemy import func, insert, select, update

from bip.db import schema
from bip.db.errors import RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.providers import capabilities as caps
from bip.providers.prober import Observation, ProbePlan, ProviderResult
from bip.providers.registry import Registry, independent

J = dict[str, Any]

RELIABILITY_VERSION = "1.0.0"
MIN_ATTEMPTS = 20
MIN_RUNS = 3
MIN_LATENCY_SAMPLES = 5
MIN_P95_SAMPLES = 20
RELIABLE_RATE = 0.95
DEGRADED_RATE = 0.80
DEFAULT_WINDOW = timedelta(days=30)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


# ---------------------------------------------------------------------- runs


class ProbeStore:
    def __init__(self, repo: Repository, registry: Registry) -> None:
        self._repo = repo
        self._registry = registry

    def data_origin(self) -> str:
        return self._repo.get_meta("data_origin") or "live_network"

    def start_run(self, plan: ProbePlan, capabilities: list[str] | None) -> tuple[int, str]:
        key = str(uuid.uuid4())
        with self._repo.transaction() as conn:
            pk = conn.execute(
                insert(schema.provider_probe_runs).values(
                    run_key=key,
                    started_at=utcnow(),
                    mode="manual",
                    data_origin=self.data_origin(),
                    providers_json=json.dumps([p.spec.id for p in plan.providers]),
                    capabilities_json=json.dumps(capabilities),
                    budget_json=json.dumps(plan.budget),
                    status="running",
                )
            ).inserted_primary_key
        assert pk is not None  # noqa: S101
        return int(pk[0]), key

    def finish_run(
        self,
        run_id: int,
        results: list[ProviderResult],
        *,
        started: datetime,
        error: str | None = None,
    ) -> None:
        """Store every observation, consistency checks and reliability snapshots atomically."""
        now = utcnow()
        obs = [o for r in results for o in r.observations]
        used = sum(r.requests_used for r in results)
        skipped = sum(r.requests_skipped for r in results)
        if error is not None:
            status = "failed"
        elif any(r.stop_reason for r in results) or skipped:
            status = "partial"
        else:
            status = "completed"
        summary = {
            "providers": {
                r.provider_id: {
                    "chain": r.chain,
                    "connectivity": r.connectivity,
                    "requests_used": r.requests_used,
                    "stop_reason": r.stop_reason,
                    "statuses": dict(Counter(o.status for o in r.observations)),
                    "environment_restricted": any(o.environment_restricted for o in r.observations),
                }
                for r in results
            },
            "rate_limit_events": sum(o.connectivity == "rate_limited" for o in obs if o.requested),
            "statuses": dict(Counter(o.status for o in obs)),
        }
        failures = sorted({r.stop_reason for r in results if r.stop_reason})
        checks = consistency_checks(self._registry, results, now)
        with self._repo.transaction() as conn:
            for o in obs:
                conn.execute(
                    insert(schema.provider_capability_observations).values(
                        run_id=run_id, **_obs_row(o)
                    )
                )
            for c in checks:
                conn.execute(insert(schema.provider_consistency_checks).values(run_id=run_id, **c))
            conn.execute(
                update(schema.provider_probe_runs)
                .where(schema.provider_probe_runs.c.id == run_id)
                .values(
                    finished_at=now,
                    requests_used=used,
                    requests_skipped=skipped,
                    duration_ms=int((now - started).total_seconds() * 1000),
                    status=status,
                    summary_json=json.dumps(summary),
                    failure_summary=(error or "; ".join(failures))[:500] or None,
                )
            )
        for r in results:
            snap = self.reliability(r.provider_id)
            with self._repo.transaction() as conn:
                conn.execute(
                    insert(schema.provider_reliability_observations).values(
                        run_id=run_id,
                        provider_id=r.provider_id,
                        computed_at=now,
                        method_version=RELIABILITY_VERSION,
                        window_start=_dt(snap["window"]["start"]),
                        window_end=_dt(snap["window"]["end"]),
                        state=snap["state"],
                        sample_size=snap["sample_size"],
                        payload_json=json.dumps(snap),
                    )
                )

    def run(self, run_key: str) -> J:
        r = schema.provider_probe_runs
        with self._repo.transaction() as conn:
            row = conn.execute(select(r).where(r.c.run_key == run_key)).mappings().first()
            if row is None:
                raise RecordNotFoundError(f"probe run {run_key} not found")
            obs = (
                conn.execute(
                    select(schema.provider_capability_observations)
                    .where(schema.provider_capability_observations.c.run_id == row["id"])
                    .order_by(schema.provider_capability_observations.c.id)
                )
                .mappings()
                .all()
            )
            checks = (
                conn.execute(
                    select(schema.provider_consistency_checks).where(
                        schema.provider_consistency_checks.c.run_id == row["id"]
                    )
                )
                .mappings()
                .all()
            )
        return {
            **_run_row(row),
            "observations": [_obs_out(o) for o in obs],
            "consistency_checks": [_check_out(c) for c in checks],
        }

    def runs(self, limit: int, offset: int) -> tuple[list[J], int]:
        r = schema.provider_probe_runs
        with self._repo.transaction() as conn:
            total = int(conn.execute(select(func.count()).select_from(r)).scalar_one())
            rows = (
                conn.execute(select(r).order_by(r.c.id.desc()).limit(limit).offset(offset))
                .mappings()
                .all()
            )
        return [_run_row(x) for x in rows], total

    def latest_provider_run(self, provider_id: str) -> J | None:
        """Connectivity and counts for this provider from the latest finished run with it."""
        r = schema.provider_probe_runs
        with self._repo.transaction() as conn:
            row = conn.execute(
                select(r.c.run_key, r.c.finished_at, r.c.summary_json)
                .where(
                    func.instr(r.c.providers_json, json.dumps(provider_id)) > 0,
                    r.c.summary_json.is_not(None),
                )
                .order_by(r.c.id.desc())
            ).first()
        if row is None:
            return None
        entry = json.loads(row[2])["providers"].get(provider_id)
        if entry is None:
            return None
        return {"run_id": row[0], "finished_at": iso(row[1]), **entry}

    def last_environment_block(self) -> J | None:
        """The latest environment-restricted observation, unless a later observation reached a
        provider (then the restriction is no longer current and is not reported)."""
        o = schema.provider_capability_observations
        with self._repo.transaction() as conn:
            row = conn.execute(
                select(o.c.id, o.c.provider_id, o.c.failure_kind, o.c.observed_at)
                .where(o.c.environment_restricted.is_(True))
                .order_by(o.c.id.desc())
            ).first()
            if row is None:
                return None
            later = conn.execute(
                select(o.c.id).where(o.c.id > row[0], o.c.connectivity == "reachable")
            ).first()
        if later is not None:
            return None
        return {"provider_id": row[1], "failure_kind": row[2], "observed_at": iso(row[3])}

    # ------------------------------------------------------------------ queries

    def history(
        self, provider_id: str, capability: str | None, limit: int, offset: int
    ) -> tuple[list[J], int]:
        o = schema.provider_capability_observations
        q = select(o).where(o.c.provider_id == provider_id)
        if capability:
            q = q.where(o.c.capability == capability)
        with self._repo.transaction() as conn:
            total = int(conn.execute(select(func.count()).select_from(q.subquery())).scalar_one())
            rows = (
                conn.execute(q.order_by(o.c.id.desc()).limit(limit).offset(offset)).mappings().all()
            )
        return [_obs_out(r) for r in rows], total

    def latest_by_capability(self, provider_id: str) -> dict[str, J]:
        """Latest *tested* observation per capability; a later run that merely skipped a
        capability (not selected, over budget, stopped) never hides earlier evidence."""
        o = schema.provider_capability_observations
        out: dict[str, J] = {}
        with self._repo.transaction() as conn:
            for tested in (True, False):
                cond = (o.c.status != "not_tested") if tested else (o.c.status == "not_tested")
                latest = (
                    select(o.c.capability, func.max(o.c.id).label("mid"))
                    .where(o.c.provider_id == provider_id, cond)
                    .group_by(o.c.capability)
                    .subquery()
                )
                for r in conn.execute(select(o).join(latest, o.c.id == latest.c.mid)).mappings():
                    out.setdefault(r["capability"], _obs_out(r))
        return out

    def consistency(
        self, *, chain: str | None, provider_id: str | None, limit: int, offset: int
    ) -> tuple[list[J], int]:
        c = schema.provider_consistency_checks
        q = select(c)
        if chain:
            q = q.where(c.c.chain == chain)
        if provider_id:
            q = q.where((c.c.provider_a == provider_id) | (c.c.provider_b == provider_id))
        with self._repo.transaction() as conn:
            total = int(conn.execute(select(func.count()).select_from(q.subquery())).scalar_one())
            rows = (
                conn.execute(q.order_by(c.c.id.desc()).limit(limit).offset(offset)).mappings().all()
            )
        return [_check_out(r) for r in rows], total

    def reliability(self, provider_id: str, window: timedelta = DEFAULT_WINDOW) -> J:
        o = schema.provider_capability_observations
        since = utcnow() - window
        with self._repo.transaction() as conn:
            rows = (
                conn.execute(
                    select(o).where(
                        o.c.provider_id == provider_id,
                        o.c.observed_at >= since,
                        o.c.method.is_not(None),
                        o.c.status != "not_tested",
                    )
                )
                .mappings()
                .all()
            )
        return compute_reliability(provider_id, [dict(r) for r in rows], since, utcnow())


# ---------------------------------------------------------------------- reliability


def compute_reliability(provider_id: str, rows: list[J], start: datetime, end: datetime) -> J:
    blocked = [
        r
        for r in rows
        if r["environment_restricted"]
        or r["failure_kind"]
        in {"dns_failure", "proxy_refused", "connection_failed", "unsafe_destination"}
    ]
    attempts = [r for r in rows if r not in blocked]
    ok = [r for r in attempts if r["connectivity"] == "reachable" and r["validation"] != "failed"]
    count = Counter(r["connectivity"] for r in attempts)
    runs = len({r["run_id"] for r in attempts})
    latencies = sorted(int(r["latency_ms"]) for r in ok if r["latency_ms"] is not None)
    invalid = count.get("invalid_response", 0)
    rate = len(ok) / len(attempts) if attempts else None
    limitations: list[str] = []
    if not attempts:
        state = "unknown" if blocked else "insufficient_data"
        limitations.append(
            "No request reached the provider; availability and reliability are unknown."
            if blocked
            else "No probe observations in the window."
        )
    elif len(attempts) < MIN_ATTEMPTS or runs < MIN_RUNS:
        state = "insufficient_data"
        limitations.append(
            f"Needs at least {MIN_ATTEMPTS} attempts across {MIN_RUNS} runs; "
            f"has {len(attempts)} attempts across {runs} run(s)."
        )
    elif rate is not None and rate >= RELIABLE_RATE and invalid == 0:
        state = "observed_reliable"
    elif rate is not None and (rate < DEGRADED_RATE):
        state = "observed_unreliable"
    else:
        state = "observed_degraded"
    if blocked:
        limitations.append(
            f"{len(blocked)} attempt(s) blocked by the local environment were "
            "excluded (they say nothing about the provider)."
        )
    limitations.append(
        "Probes are a handful of small requests; they do not measure quota, "
        "sustained throughput or historical depth."
    )
    times = [r["observed_at"] for r in rows]
    return {
        "provider_id": provider_id,
        "method": f"reliability@{RELIABILITY_VERSION}",
        "state": state,
        "sample_size": len(attempts),
        "environment_blocked": len(blocked),
        "runs": runs,
        "successes": len(ok),
        "failures": len(attempts) - len(ok),
        "success_rate": round(rate, 4) if rate is not None and state != "unknown" else None,
        "rate_limit_events": count.get("rate_limited", 0),
        "timeouts": count.get("timeout", 0),
        "invalid_responses": invalid,
        "provider_errors": count.get("provider_error", 0),
        "unauthorized": count.get("unauthorized", 0),
        "latency": {
            "samples": len(latencies),
            "median_ms": statistics.median(latencies)
            if len(latencies) >= MIN_LATENCY_SAMPLES
            else None,
            "p95_ms": latencies[math.ceil(0.95 * len(latencies)) - 1]
            if len(latencies) >= MIN_P95_SAMPLES
            else None,
        },
        "last_success_at": iso(max((r["observed_at"] for r in ok), default=None)),
        "last_failure_at": iso(
            max((r["observed_at"] for r in attempts if r not in ok), default=None)
        ),
        "window": {
            "start": iso(start),
            "end": iso(end),
            "first_observation": iso(min(times, default=None)),
            "last_observation": iso(max(times, default=None)),
        },
        "rules": {
            "min_attempts": MIN_ATTEMPTS,
            "min_runs": MIN_RUNS,
            "reliable_rate": RELIABLE_RATE,
            "degraded_rate": DEGRADED_RATE,
            "min_latency_samples": MIN_LATENCY_SAMPLES,
            "min_p95_samples": MIN_P95_SAMPLES,
        },
        "limitations": limitations,
    }


# ---------------------------------------------------------------------- consistency

_COMPARABLE = {
    "ethereum": (
        ("chain_identity", "chain_id"),
        ("finalized_block_hash", "finalized"),
        ("historical_block_hash", "historical"),
    ),
    "solana": (
        ("chain_identity", "genesis"),
        ("finalized_block_hash", "finalized_block"),
        ("historical_block_hash", "historical"),
    ),
}


def consistency_checks(registry: Registry, results: list[ProviderResult], now: datetime) -> list[J]:
    out: list[J] = []
    by_chain: dict[str, list[ProviderResult]] = {}
    for r in results:
        if r.connectivity in {"reachable", "wrong_network"}:
            by_chain.setdefault(r.chain, []).append(r)
    for chain, rs in by_chain.items():
        for a, b in combinations(sorted(rs, key=lambda r: r.provider_id), 2):
            sa, sb = registry.get(a.provider_id), registry.get(b.provider_id)
            indep = independent(sa, sb)
            shared = (
                ""
                if indep
                else (f" Not independent: both are in infrastructure group {sa.infrastructure!r}.")
            )
            for comparison, key in _COMPARABLE[chain]:
                va, vb = a.values.get(key), b.values.get(key)
                if va is None or vb is None:
                    continue
                row: J = {
                    "chain": chain,
                    "comparison": comparison,
                    "provider_a": a.provider_id,
                    "provider_b": b.provider_id,
                    "infrastructure_a": sa.infrastructure,
                    "infrastructure_b": sb.infrastructure,
                    "independent": indep,
                    "checked_at": now,
                }
                if comparison == "chain_identity":
                    row.update(reference=None, value_a=str(va)[:200], value_b=str(vb)[:200])
                    same = str(va) == str(vb)
                    row["outcome"] = "consistent" if same else "conflict"
                    row["note"] = (
                        "Same network identity." if same else "Different network identity reported."
                    ) + shared
                else:
                    (pa, ha), (pb, hb) = va, vb
                    if pa != pb:
                        row.update(
                            reference=f"{pa} vs {pb}",
                            value_a=ha,
                            value_b=hb,
                            outcome="inconclusive",
                            note="Different block numbers or slots (different sync points"
                            " or samples); not comparable and not a conflict." + shared,
                        )
                    else:
                        same = ha == hb
                        row.update(
                            reference=str(pa),
                            value_a=ha,
                            value_b=hb,
                            outcome="consistent" if same else "conflict",
                            note=(
                                "Same hash at the same position."
                                if same
                                else "Different hashes at the same position (possible reorg "
                                "or faulty data); both values kept."
                            )
                            + shared,
                        )
                out.append(row)
    return out


# ---------------------------------------------------------------------- profiles


def historical_coverage(chain: str, latest: dict[str, J]) -> J:
    h = latest.get("historical_block")
    recent = latest.get("latest_block" if chain == "ethereum" else "finalized_block")
    archive = "archive_capability_unverified"
    if h is None or h["status"] == "not_tested":
        label = (
            "recent_only_observed"
            if recent and recent["status"] == "supported"
            else "insufficient_evidence"
        )
        note = "No historical sample has been tested."
    elif h["status"] == "supported":
        label = "historical_sample_observed"
        note = "One historical sample succeeded; full archive access is not established."
    elif h["rpc_error_code"] == -32007:
        label = "insufficient_evidence"
        note = "The sampled slot was skipped (no block was produced); this is not a failure."
    elif h["failure_kind"] == "null_result" or (
        h["failure_kind"] == "rpc_not_available" and h["rpc_error_code"] != -32007
    ):
        label = "historical_access_failed"
        note = "The provider answered but did not return the historical sample."
    else:
        label = "insufficient_evidence"
        note = "The historical request did not complete; nothing is established."
    return {
        "label": label,
        "archive": archive,
        "note": note,
        "sample": h["reference"] if h else None,
        "observed_at": h["observed_at"] if h else None,
    }


def coverage_counts(chain: str, latest: dict[str, J]) -> J:
    statuses = Counter(
        latest[c.id]["status"] if c.id in latest else "not_tested" for c in caps.catalog(chain)
    )
    return {s: statuses.get(s, 0) for s in caps.STATUSES} | {"total": len(caps.catalog(chain))}


# ---------------------------------------------------------------------- row helpers


def _obs_row(o: Observation) -> J:
    return {
        "provider_id": o.provider_id,
        "chain": o.chain,
        "network": o.network,
        "infrastructure": o.infrastructure,
        "capability": o.capability,
        "method": o.method,
        "status": o.status,
        "connectivity": o.connectivity,
        "failure_kind": o.failure_kind,
        "environment_restricted": o.environment_restricted,
        "explanation": o.explanation[:500],
        "http_status": o.http_status,
        "rpc_error_code": o.rpc_error_code,
        "latency_ms": o.latency_ms,
        "response_bytes": o.response_bytes,
        "validation": o.validation,
        "reference_json": json.dumps(o.reference) if o.reference is not None else None,
        "observed_at": o.observed_at,
    }


def _obs_out(r: Any) -> J:
    keys = (
        "provider_id",
        "chain",
        "network",
        "infrastructure",
        "capability",
        "method",
        "status",
        "connectivity",
        "failure_kind",
        "explanation",
        "http_status",
        "rpc_error_code",
        "latency_ms",
        "response_bytes",
        "validation",
    )
    out: J = {k: r[k] for k in keys}
    out["environment_restricted"] = bool(r["environment_restricted"])
    out["reference"] = json.loads(r["reference_json"]) if r["reference_json"] else None
    out["observed_at"] = iso(r["observed_at"])
    out["run_id"] = r["run_id"]
    return out


def _run_row(r: Any) -> J:
    return {
        "run_id": r["run_key"],
        "started_at": iso(r["started_at"]),
        "finished_at": iso(r["finished_at"]),
        "mode": r["mode"],
        "data_origin": r["data_origin"],
        "providers": json.loads(r["providers_json"]),
        "capabilities": json.loads(r["capabilities_json"]),
        "budget": json.loads(r["budget_json"]),
        "requests_used": r["requests_used"],
        "requests_skipped": r["requests_skipped"],
        "duration_ms": r["duration_ms"],
        "status": r["status"],
        "summary": json.loads(r["summary_json"]) if r["summary_json"] else None,
        "failure_summary": r["failure_summary"],
    }


def _check_out(r: Any) -> J:
    keys = (
        "chain",
        "comparison",
        "provider_a",
        "provider_b",
        "infrastructure_a",
        "infrastructure_b",
        "reference",
        "value_a",
        "value_b",
        "outcome",
        "note",
    )
    return {
        **{k: r[k] for k in keys},
        "independent": bool(r["independent"]),
        "checked_at": iso(r["checked_at"]),
    }


def _dt(v: object) -> datetime | None:
    return datetime.fromisoformat(v) if isinstance(v, str) else None
