"""Persisted provider state: call outcomes, explicit health checks, enable/disable overrides."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import insert, select, update

from bip.db import schema
from bip.db.repository import Repository
from bip.db.types import utcnow


@dataclass(frozen=True, slots=True)
class ProviderState:
    provider_id: str
    enabled_override: bool | None = None
    override_reason: str | None = None
    last_check_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    last_latency_ms: int | None = None
    last_outcome: str | None = None


@dataclass(frozen=True, slots=True)
class HealthCheckRecord:
    provider_id: str
    checked_at: datetime
    ok: bool
    latency_ms: int | None
    outcome: str
    detail: str | None
    observed_network: str | None
    head_block: int | None
    head_slot: int | None


class ProviderStateStore:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def states(self) -> dict[str, ProviderState]:
        c, o = schema.provider_checks, schema.provider_overrides
        with self._repo.transaction() as conn:
            checks = conn.execute(select(c)).mappings().all()
            overrides = conn.execute(select(o)).mappings().all()
        out: dict[str, ProviderState] = {}
        for r in checks:
            out[r["provider_id"]] = ProviderState(
                provider_id=r["provider_id"],
                last_check_at=r["last_check_at"],
                last_success_at=r["last_success_at"],
                last_failure_at=r["last_failure_at"],
                consecutive_failures=int(r["consecutive_failures"] or 0),
                last_latency_ms=r["last_latency_ms"],
                last_outcome=r["last_outcome"],
            )
        for r in overrides:
            base = out.get(r["provider_id"], ProviderState(provider_id=r["provider_id"]))
            out[r["provider_id"]] = replace(
                base, enabled_override=bool(r["enabled"]), override_reason=r["reason"]
            )
        return out

    def record_call(
        self, provider_id: str, *, ok: bool, latency_ms: int | None, outcome: str
    ) -> None:
        c = schema.provider_checks
        now = utcnow()
        with self._repo.transaction() as conn:
            row = conn.execute(select(c).where(c.c.provider_id == provider_id)).mappings().first()
            if row is None:
                conn.execute(
                    insert(c).values(
                        provider_id=provider_id,
                        last_check_at=now,
                        last_success_at=now if ok else None,
                        last_failure_at=None if ok else now,
                        last_outcome=outcome,
                        consecutive_failures=0 if ok else 1,
                        last_latency_ms=latency_ms,
                    )
                )
                return
            values: dict[str, object] = {
                "last_check_at": now,
                "last_outcome": outcome,
                "last_latency_ms": latency_ms,
            }
            if ok:
                values.update(last_success_at=now, consecutive_failures=0)
            else:
                values.update(
                    last_failure_at=now,
                    consecutive_failures=int(row["consecutive_failures"] or 0) + 1,
                )
            conn.execute(update(c).where(c.c.provider_id == provider_id).values(**values))

    def record_health_check(self, record: HealthCheckRecord) -> None:
        with self._repo.transaction() as conn:
            conn.execute(
                insert(schema.provider_health_checks).values(
                    provider_id=record.provider_id,
                    checked_at=record.checked_at,
                    ok=record.ok,
                    latency_ms=record.latency_ms,
                    outcome=record.outcome,
                    detail=(record.detail or "")[:300] or None,
                    observed_network=record.observed_network,
                    head_block=record.head_block,
                    head_slot=record.head_slot,
                )
            )

    def recent_health_checks(self, provider_id: str, limit: int = 10) -> list[HealthCheckRecord]:
        h = schema.provider_health_checks
        with self._repo.transaction() as conn:
            rows = (
                conn.execute(
                    select(h)
                    .where(h.c.provider_id == provider_id)
                    .order_by(h.c.checked_at.desc(), h.c.id.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [
            HealthCheckRecord(
                provider_id=r["provider_id"],
                checked_at=r["checked_at"],
                ok=bool(r["ok"]),
                latency_ms=r["latency_ms"],
                outcome=r["outcome"],
                detail=r["detail"],
                observed_network=r["observed_network"],
                head_block=r["head_block"],
                head_slot=r["head_slot"],
            )
            for r in rows
        ]

    def set_enabled(self, provider_id: str, enabled: bool, reason: str | None) -> None:
        o = schema.provider_overrides
        now = utcnow()
        with self._repo.transaction() as conn:
            exists = conn.execute(
                select(o.c.provider_id).where(o.c.provider_id == provider_id)
            ).first()
            if exists is None:
                conn.execute(
                    insert(o).values(
                        provider_id=provider_id, enabled=enabled, reason=reason, updated_at=now
                    )
                )
            else:
                conn.execute(
                    update(o)
                    .where(o.c.provider_id == provider_id)
                    .values(enabled=enabled, reason=reason, updated_at=now)
                )
            conn.execute(
                insert(schema.activity_log).values(
                    event_type="provider.enabled" if enabled else "provider.disabled",
                    summary=f"Provider {provider_id} {'enabled' if enabled else 'disabled'}",
                    created_at=now,
                )
            )
