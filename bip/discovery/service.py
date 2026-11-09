"""Discovery service: run detectors, score, deduplicate, alert, and manage signals and rules.

A run is bounded (each detector has a row cap and a per-run signal cap), reads only stored
canonical data, and records what it covered. Nothing here contacts a provider.

Deduplication has two layers:

* the **signal key** identifies one event (for example one transfer). Seeing it again refreshes the
  signal instead of opening another;
* the **cooldown** merges a *new* event for the same detector and subject into the signal opened
  within ``cooldown_seconds``, so a busy subject yields one candidate with a growing occurrence
  count and evidence list, not a stream of near-duplicates. A signal the user dismissed stays
  dismissed during the cooldown.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection, RowMapping

from bip.config.settings import Settings
from bip.contracts.findings import (
    Claim,
    ClaimType,
    EntityKind,
    EntityRef,
    EvidenceKind,
    EvidenceRef,
    Finding,
    FindingStatus,
)
from bip.contracts.provenance import Provenance, VerificationStatus
from bip.core.logging import get_logger
from bip.db import schema
from bip.db.canonical import canonical_transaction
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.discovery import DISCOVERY_VERSION
from bip.discovery.detectors import (
    DETECTORS,
    Context,
    Detector,
    DetectorOutput,
    RawCandidate,
)
from bip.discovery.notify import (
    AlertMessage,
    InAppChannel,
    NotificationChannel,
    unavailable_channels,
)
from bip.discovery.scoring import HIGH_SCORE, MEDIUM_SCORE, Score, score_signal
from bip.entities.labels import approved_for

log = get_logger("bip.discovery")

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
SIGNAL_STATUSES = ("open", "dismissed", "promoted", "invalidated", "expired")
MAX_EVIDENCE_REFS = 50
MIN_REVIEWED = 20
FAMILIES = tuple(sorted({d.info.family for d in DETECTORS}))
SCORING_DESCRIPTION = (
    "score = 100 * (0.5*magnitude + 0.3*baseline_reliability + 0.2*evidence_finality); "
    f"severity high >= {HIGH_SCORE:g}, medium >= {MEDIUM_SCORE:g}; "
    "confidence = baseline_reliability * (0.5 + 0.5*finality), limited by the detector's cap"
)

Row = RowMapping


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _tx_refs(chain: str, tx_ids: Sequence[str]) -> list[dict[str, str]]:
    return [{"kind": "transaction", "chain": chain, "ref": tx} for tx in tx_ids]


class DiscoveryService:
    def __init__(
        self,
        repo: Repository,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = utcnow,
        channels: Sequence[NotificationChannel] | None = None,
    ) -> None:
        self._repo = repo
        self._cfg = settings.discovery
        self._clock = clock
        self._channels: tuple[NotificationChannel, ...] = tuple(channels or (InAppChannel(),))

    def now(self) -> datetime:
        """The service's clock, so scheduled work and tests agree on the time."""
        return self._clock()

    # ------------------------------------------------------------------ catalogue

    def detectors(self) -> list[dict[str, Any]]:
        return [
            {
                "id": d.info.id,
                "version": d.info.version,
                "family": d.info.family,
                "title": d.info.title,
                "method": d.info.method,
                "data_required": d.info.data_required,
                "coverage": d.info.coverage,
                "limitations": d.info.limitations,
                "refresh": d.info.refresh,
                "cost": d.info.cost,
                "confidence_cap": d.info.confidence_cap,
                "chains": list(d.info.chains),
            }
            for d in DETECTORS
        ]

    def channels(self) -> dict[str, Any]:
        return {
            "available": [c.name for c in self._channels],
            "unavailable": unavailable_channels(),
        }

    # ------------------------------------------------------------------ running

    def run(self, chain: str | None = None) -> dict[str, Any]:
        """Run every applicable detector over stored data. Returns the run summary."""
        now = self._clock()
        chains = [chain] if chain else self._chains_with_data()
        run_id = self._start_run(now, chain)
        summary: dict[str, Any] = {
            "discovery_version": DISCOVERY_VERSION,
            "scoring": SCORING_DESCRIPTION,
            "chains": chains,
            "detectors": {},
            "errors": [],
        }
        new_ids: list[int] = []
        for c in chains:
            for det in DETECTORS:
                if c not in det.info.chains:
                    continue
                key = f"{det.info.id}:{c}"
                try:
                    output = det.run(Context(self._repo, self._cfg, c, now))
                    stats, created = self._store_output(det, output, c, now, run_id)
                except Exception as exc:  # one failing detector must not abort the pass
                    log.exception("detector %s failed", key)
                    summary["errors"].append({"detector": key, "error": type(exc).__name__})
                    continue
                summary["detectors"][key] = stats
                new_ids.extend(created)
        summary["expired"] = self._expire(now)
        summary["alerts_created"] = self._evaluate_alerts(new_ids, now)
        status = "partial" if summary["errors"] else "succeeded"
        self._finish_run(run_id, status, summary)
        return {"run_id": run_id, "status": status, **summary}

    def _chains_with_data(self) -> list[str]:
        t = schema.transactions
        with self._repo.transaction() as conn:
            return sorted(str(c) for (c,) in conn.execute(select(t.c.chain).distinct()).all())

    def _start_run(self, now: datetime, chain: str | None) -> int:
        with self._repo.transaction() as conn:
            result = conn.execute(
                insert(schema.discovery_runs).values(
                    started_at=now, chain=chain, status="running", summary_json=None
                )
            )
            pk = result.inserted_primary_key
            assert pk is not None  # noqa: S101
            return int(pk[0])

    def _finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        with self._repo.transaction() as conn:
            conn.execute(
                update(schema.discovery_runs)
                .where(schema.discovery_runs.c.id == run_id)
                .values(finished_at=self._clock(), status=status, summary_json=json.dumps(summary))
            )

    def runs(self, limit: int) -> list[dict[str, Any]]:
        r = schema.discovery_runs
        with self._repo.transaction() as conn:
            rows = conn.execute(select(r).order_by(r.c.id.desc()).limit(limit)).mappings().all()
        return [self._run_row(x) for x in rows]

    @staticmethod
    def _run_row(x: Row) -> dict[str, Any]:
        return {
            "id": x["id"],
            "chain": x["chain"],
            "status": x["status"],
            "started_at": _iso(x["started_at"]),
            "finished_at": _iso(x["finished_at"]),
            "summary": json.loads(x["summary_json"]) if x["summary_json"] else None,
        }

    def last_run(self) -> dict[str, Any] | None:
        runs = self.runs(1)
        return runs[0] if runs else None

    # ------------------------------------------------------------------ scoring and storing

    def _store_output(
        self, det: Detector, output: DetectorOutput, chain: str, now: datetime, run_id: int
    ) -> tuple[dict[str, Any], list[int]]:
        scored = self._score_all(det, output, chain)
        scored.sort(key=lambda item: (item[1].score, item[0].z), reverse=True)
        limit = self._cfg.max_signals_per_detector
        kept, dropped = scored[:limit], max(0, len(scored) - limit)
        counts = {"created": 0, "merged": 0, "refreshed": 0, "suppressed": 0}
        created: list[int] = []
        with self._repo.transaction() as conn:
            for cand, sc, finality in kept:
                coverage = self._coverage(det, output, finality)
                outcome, signal_id = self._upsert(conn, cand, sc, coverage, now, run_id)
                counts[outcome] += 1
                if outcome == "created":
                    created.append(signal_id)
        stats = {
            "candidates": len(scored),
            "rows_scanned": output.rows_scanned,
            "truncated": output.truncated,
            "dropped_by_signal_cap": dropped,
            **counts,
        }
        return stats, created

    def _score_all(
        self, det: Detector, output: DetectorOutput, chain: str
    ) -> list[tuple[RawCandidate, Score, dict[str, int]]]:
        if not output.candidates:
            return []
        wanted = sorted({tx for c in output.candidates for tx in c.tx_ids})
        t = schema.transactions
        finality: dict[str, str | None] = {}
        with self._repo.transaction() as conn:
            for start in range(0, len(wanted), 400):
                for tx_id, fin in conn.execute(
                    select(t.c.tx_id, t.c.finality).where(
                        t.c.chain == chain, t.c.tx_id.in_(wanted[start : start + 400])
                    )
                ).all():
                    finality[str(tx_id)] = fin
        out: list[tuple[RawCandidate, Score, dict[str, int]]] = []
        for cand in output.candidates:
            total = len(cand.tx_ids)
            final = (
                total
                if cand.evidence_finalized
                else sum(1 for tx in cand.tx_ids if finality.get(tx) == "finalized")
            )
            share = final / total if total else 0.0
            notes = {
                **cand.notes,
                "evidence_finality": f"{final} of {total} supporting transactions finalized",
            }
            sc = score_signal(
                magnitude=cand.magnitude,
                baseline_reliability=cand.baseline_reliability,
                evidence_finality=share,
                confidence_cap=min(cand.confidence_cap, det.info.confidence_cap),
                notes=notes,
            )
            out.append((cand, sc, {"finalized": final, "total": total}))
        return out

    def _coverage(
        self, det: Detector, output: DetectorOutput, finality: dict[str, int]
    ) -> dict[str, Any]:
        return {
            "detector": det.info.id,
            "version": det.info.version,
            "coverage": det.info.coverage,
            "limitations": det.info.limitations,
            "refresh": det.info.refresh,
            "cost": det.info.cost,
            "rows_scanned": output.rows_scanned,
            "row_cap_reached": output.truncated,
            "baseline_window_days": self._cfg.baseline_days,
            "lookback_hours": self._cfg.lookback_hours,
            "evidence_finality": finality,
            "provisional_evidence": finality["finalized"] < finality["total"],
        }

    def _upsert(
        self,
        conn: Connection,
        cand: RawCandidate,
        sc: Score,
        coverage: dict[str, Any],
        now: datetime,
        run_id: int,
    ) -> tuple[str, int]:
        s = schema.signals
        ttl = now + timedelta(days=self._cfg.ttl_days)
        explanation = {
            **cand.explanation,
            "score_components": sc.components,
            "scoring": SCORING_DESCRIPTION,
        }
        refs = _tx_refs(cand.chain, cand.tx_ids)
        existing = conn.execute(select(s).where(s.c.signal_key == cand.key)).mappings().first()
        if existing is not None:
            if existing["status"] not in ("open", "promoted"):
                return "suppressed", int(existing["id"])
            conn.execute(
                update(s)
                .where(s.c.id == existing["id"])
                .values(
                    last_seen_at=now,
                    occurrences=int(existing["occurrences"]) + 1,
                    score=sc.score,
                    severity=sc.severity,
                    confidence=sc.confidence,
                    explanation_json=json.dumps(explanation),
                    coverage_json=json.dumps(coverage),
                    evidence_json=json.dumps(self._merge_refs(existing, refs)),
                    expires_at=ttl if existing["status"] == "open" else existing["expires_at"],
                    run_id=run_id,
                )
            )
            self._attach_evidence(conn, int(existing["id"]), cand.chain, cand.tx_ids)
            return "refreshed", int(existing["id"])
        if self._cfg.cooldown_seconds > 0:
            near = (
                conn.execute(
                    select(s)
                    .where(
                        s.c.detector_id == cand.detector_id,
                        s.c.chain == cand.chain,
                        s.c.subject_ref == cand.subject_ref,
                        s.c.first_seen_at >= now - timedelta(seconds=self._cfg.cooldown_seconds),
                    )
                    .order_by(s.c.first_seen_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if near is not None:
                return self._merge(conn, near, cand, sc, refs, now, run_id)
        values: dict[str, Any] = {
            "signal_key": cand.key,
            "detector_id": cand.detector_id,
            "detector_version": cand.detector_version,
            "family": cand.family,
            "chain": cand.chain,
            "subject_kind": cand.subject_kind,
            "subject_ref": cand.subject_ref,
            "title": cand.title[:200],
            "severity": sc.severity,
            "confidence": sc.confidence,
            "score": sc.score,
            "status": "open",
            "explanation_json": json.dumps(explanation),
            "evidence_json": json.dumps(refs[:MAX_EVIDENCE_REFS]),
            "coverage_json": json.dumps(coverage),
            "observed_at": cand.observed_at,
            "first_seen_at": now,
            "last_seen_at": now,
            "occurrences": 1,
            "expires_at": ttl,
            "run_id": run_id,
        }
        pk = conn.execute(insert(s).values(**values)).inserted_primary_key
        assert pk is not None  # noqa: S101
        self._attach_evidence(conn, int(pk[0]), cand.chain, cand.tx_ids)
        return "created", int(pk[0])

    def _merge(
        self,
        conn: Connection,
        near: Row,
        cand: RawCandidate,
        sc: Score,
        refs: list[dict[str, str]],
        now: datetime,
        run_id: int,
    ) -> tuple[str, int]:
        s = schema.signals
        signal_id = int(near["id"])
        if near["status"] not in ("open", "promoted"):
            return "suppressed", signal_id  # a dismissed or invalidated subject stays quiet
        stronger = sc.score > float(near["score"])
        conn.execute(
            update(s)
            .where(s.c.id == signal_id)
            .values(
                last_seen_at=now,
                occurrences=int(near["occurrences"]) + 1,
                evidence_json=json.dumps(self._merge_refs(near, refs)),
                run_id=run_id,
                **(
                    {"score": sc.score, "severity": sc.severity, "confidence": sc.confidence}
                    if stronger
                    else {}
                ),
            )
        )
        self._attach_evidence(conn, signal_id, cand.chain, cand.tx_ids)
        return "merged", signal_id

    @staticmethod
    def _merge_refs(row: Row, refs: list[dict[str, str]]) -> list[dict[str, str]]:
        merged: dict[tuple[str, str], dict[str, str]] = {}
        for ref in [*json.loads(row["evidence_json"]), *refs]:
            merged.setdefault((ref["chain"], ref["ref"]), ref)
        return list(merged.values())[-MAX_EVIDENCE_REFS:]

    @staticmethod
    def _attach_evidence(
        conn: Connection, signal_id: int, chain: str, tx_ids: Sequence[str]
    ) -> None:
        e = schema.signal_evidence
        have = {
            str(x)
            for (x,) in conn.execute(
                select(e.c.tx_id).where(e.c.signal_id == signal_id, e.c.chain == chain)
            ).all()
        }
        for tx in dict.fromkeys(tx_ids):
            if tx not in have:
                conn.execute(insert(e).values(signal_id=signal_id, chain=chain, tx_id=tx))

    def _expire(self, now: datetime) -> int:
        s = schema.signals
        with self._repo.transaction() as conn:
            result = conn.execute(
                update(s)
                .where(s.c.status == "open", s.c.expires_at.is_not(None), s.c.expires_at < now)
                .values(status="expired", status_note="review window ended")
            )
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------ alerts

    def _evaluate_alerts(self, signal_ids: list[int], now: datetime) -> int:
        if not signal_ids:
            return 0
        rules = [r for r in self.list_rules() if r["enabled"]]
        if not rules:
            return 0
        s, a = schema.signals, schema.alerts
        created = 0
        with self._repo.transaction() as conn:
            rows = conn.execute(select(s).where(s.c.id.in_(signal_ids))).mappings().all()
            for sig in rows:
                for rule in rules:
                    if not self._rule_matches(rule, sig):
                        continue
                    exists = conn.execute(
                        select(a.c.id).where(a.c.rule_id == rule["id"], a.c.signal_id == sig["id"])
                    ).first()
                    if exists is not None:
                        continue
                    message = AlertMessage(
                        alert_key=f"{rule['id']}:{sig['id']}",
                        severity=str(sig["severity"]),
                        title=str(sig["title"]),
                        signal_id=int(sig["id"]),
                    )
                    delivery = self._channels[0].deliver(message)
                    conn.execute(
                        insert(a).values(
                            rule_id=rule["id"],
                            signal_id=sig["id"],
                            severity=sig["severity"],
                            title=sig["title"],
                            created_at=now,
                            read_at=None,
                            delivery=delivery,
                        )
                    )
                    created += 1
        return created

    @staticmethod
    def _rule_matches(rule: Mapping[str, Any], sig: Row) -> bool:
        if SEVERITY_RANK[str(sig["severity"])] < SEVERITY_RANK[str(rule["min_severity"])]:
            return False
        if float(sig["score"]) < float(rule["min_score"]):
            return False
        if rule["families"] and sig["family"] not in rule["families"]:
            return False
        return not (rule["chains"] and sig["chain"] not in rule["chains"])

    # ------------------------------------------------------------------ rules

    @staticmethod
    def _rule(x: Row) -> dict[str, Any]:
        return {
            "id": x["id"],
            "name": x["name"],
            "enabled": bool(x["enabled"]),
            "min_severity": x["min_severity"],
            "min_score": float(x["min_score"]),
            "families": json.loads(x["families_json"]),
            "chains": json.loads(x["chains_json"]),
            "created_at": _iso(x["created_at"]),
        }

    def list_rules(self) -> list[dict[str, Any]]:
        r = schema.alert_rules
        with self._repo.transaction() as conn:
            rows = conn.execute(select(r).order_by(r.c.id)).mappings().all()
        return [self._rule(x) for x in rows]

    @staticmethod
    def _validate_rule(
        min_severity: str, min_score: float, families: Sequence[str], chains: Sequence[str]
    ) -> None:
        if min_severity not in SEVERITY_RANK:
            raise InvalidRecordError("min_severity must be low, medium or high")
        if not 0 <= min_score <= 100:
            raise InvalidRecordError("min_score must be between 0 and 100")
        unknown = sorted(set(families) - set(FAMILIES))
        if unknown:
            raise InvalidRecordError(f"unknown signal families: {', '.join(unknown)}")
        if any(not c or len(c) > 32 for c in chains):
            raise InvalidRecordError("chains must be short non-empty identifiers")

    def create_rule(
        self,
        name: str,
        *,
        min_severity: str = "high",
        min_score: float = 0.0,
        families: Sequence[str] = (),
        chains: Sequence[str] = (),
        enabled: bool = True,
    ) -> dict[str, Any]:
        self._validate_rule(min_severity, min_score, families, chains)
        r = schema.alert_rules
        with self._repo.transaction() as conn:
            if conn.execute(select(r.c.id).where(r.c.name == name)).first() is not None:
                raise InvalidRecordError(f"an alert rule named {name!r} already exists")
            pk = conn.execute(
                insert(r).values(
                    name=name,
                    enabled=enabled,
                    min_severity=min_severity,
                    min_score=min_score,
                    families_json=json.dumps(sorted(set(families))),
                    chains_json=json.dumps(sorted(set(chains))),
                    created_at=self._clock(),
                )
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            row = conn.execute(select(r).where(r.c.id == pk[0])).mappings().one()
        return self._rule(row)

    def update_rule(self, rule_id: int, changes: Mapping[str, Any]) -> dict[str, Any]:
        r = schema.alert_rules
        with self._repo.transaction() as conn:
            row = conn.execute(select(r).where(r.c.id == rule_id)).mappings().first()
            if row is None:
                raise RecordNotFoundError(f"alert rule {rule_id} not found")
            current = self._rule(row)
            merged = {**current, **{k: v for k, v in changes.items() if v is not None}}
            self._validate_rule(
                merged["min_severity"], merged["min_score"], merged["families"], merged["chains"]
            )
            conn.execute(
                update(r)
                .where(r.c.id == rule_id)
                .values(
                    enabled=bool(merged["enabled"]),
                    min_severity=merged["min_severity"],
                    min_score=float(merged["min_score"]),
                    families_json=json.dumps(sorted(set(merged["families"]))),
                    chains_json=json.dumps(sorted(set(merged["chains"]))),
                )
            )
            row = conn.execute(select(r).where(r.c.id == rule_id)).mappings().one()
        return self._rule(row)

    def delete_rule(self, rule_id: int) -> None:
        r = schema.alert_rules
        with self._repo.transaction() as conn:
            result = conn.execute(r.delete().where(r.c.id == rule_id))
        if not result.rowcount:
            raise RecordNotFoundError(f"alert rule {rule_id} not found")

    # ------------------------------------------------------------------ alerts API

    def list_alerts(self, *, unread_only: bool, limit: int, offset: int) -> dict[str, Any]:
        a = schema.alerts
        cond = a.c.read_at.is_(None) if unread_only else a.c.id > 0
        with self._repo.transaction() as conn:
            total = int(conn.execute(select(func.count()).select_from(a).where(cond)).scalar_one())
            rows = (
                conn.execute(
                    select(a)
                    .where(cond)
                    .order_by(a.c.created_at.desc(), a.c.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
            unread = int(
                conn.execute(
                    select(func.count()).select_from(a).where(a.c.read_at.is_(None))
                ).scalar_one()
            )
        items = [
            {
                "id": x["id"],
                "rule_id": x["rule_id"],
                "signal_id": x["signal_id"],
                "severity": x["severity"],
                "title": x["title"],
                "created_at": _iso(x["created_at"]),
                "read_at": _iso(x["read_at"]),
                "delivery": x["delivery"],
            }
            for x in rows
        ]
        return {"total": total, "unread": unread, "limit": limit, "offset": offset, "items": items}

    def mark_read(self, alert_id: int) -> dict[str, Any]:
        a = schema.alerts
        with self._repo.transaction() as conn:
            row = conn.execute(select(a).where(a.c.id == alert_id)).mappings().first()
            if row is None:
                raise RecordNotFoundError(f"alert {alert_id} not found")
            if row["read_at"] is None:
                conn.execute(update(a).where(a.c.id == alert_id).values(read_at=self._clock()))
        return {"id": alert_id, "read": True}

    def mark_all_read(self) -> int:
        a = schema.alerts
        with self._repo.transaction() as conn:
            result = conn.execute(
                update(a).where(a.c.read_at.is_(None)).values(read_at=self._clock())
            )
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------ signals API

    def _signal(self, x: Row) -> dict[str, Any]:
        out = self._signal_core(x)
        refs = {str(x["subject_ref"])}
        observed: Any = out["explanation"].get("observed")
        values: list[Any] = (
            list(cast("dict[str, Any]", observed).values()) if isinstance(observed, dict) else []
        )
        refs.update(str(v) for v in values if isinstance(v, str) and 20 < len(v) <= 100)
        found = approved_for(self._repo, str(x["chain"]), refs)
        out["attribution"] = {
            "labels": found,
            "note": (
                "Only labels a person approved with evidence are shown. No label means "
                "unattributed, not 'not an exchange'."
            ),
        }
        return out

    @staticmethod
    def _signal_core(x: Row) -> dict[str, Any]:
        return {
            "id": x["id"],
            "key": x["signal_key"],
            "detector": {"id": x["detector_id"], "version": x["detector_version"]},
            "family": x["family"],
            "chain": x["chain"],
            "subject": {"kind": x["subject_kind"], "ref": x["subject_ref"]},
            "title": x["title"],
            "severity": x["severity"],
            "confidence": x["confidence"],
            "score": x["score"],
            "status": x["status"],
            "explanation": json.loads(x["explanation_json"]),
            "evidence": json.loads(x["evidence_json"]),
            "coverage": json.loads(x["coverage_json"]),
            "observed_at": _iso(x["observed_at"]),
            "first_seen_at": _iso(x["first_seen_at"]),
            "last_seen_at": _iso(x["last_seen_at"]),
            "occurrences": x["occurrences"],
            "expires_at": _iso(x["expires_at"]),
            "status_note": x["status_note"],
            "promoted_to": x["promoted_to"],
        }

    def list_signals(
        self,
        *,
        status: str | None = None,
        family: str | None = None,
        chain: str | None = None,
        severity: str | None = None,
        detector_id: str | None = None,
        subject_ref: str | None = None,
        min_score: float | None = None,
        sort: str = "score",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        s = schema.signals
        conds = [s.c.id > 0]
        if status:
            conds.append(s.c.status == status)
        if family:
            conds.append(s.c.family == family)
        if chain:
            conds.append(s.c.chain == chain)
        if severity:
            conds.append(s.c.severity == severity)
        if detector_id:
            conds.append(s.c.detector_id == detector_id)
        if subject_ref:
            conds.append(s.c.subject_ref == subject_ref)
        if min_score is not None:
            conds.append(s.c.score >= min_score)
        order = (
            (s.c.last_seen_at.desc(), s.c.id.desc())
            if sort == "recent"
            else (s.c.score.desc(), s.c.id.desc())
        )
        with self._repo.transaction() as conn:
            total = int(
                conn.execute(select(func.count()).select_from(s).where(*conds)).scalar_one()
            )
            rows = (
                conn.execute(select(s).where(*conds).order_by(*order).limit(limit).offset(offset))
                .mappings()
                .all()
            )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [self._signal(x) for x in rows],
        }

    def get_signal(self, signal_id: int) -> dict[str, Any]:
        return self._signal(self._row(signal_id))

    def _row(self, signal_id: int) -> Row:
        s = schema.signals
        with self._repo.transaction() as conn:
            row = conn.execute(select(s).where(s.c.id == signal_id)).mappings().first()
        if row is None:
            raise RecordNotFoundError(f"signal {signal_id} not found")
        return row

    def feed(self, limit: int, offset: int) -> dict[str, Any]:
        """Open signals ranked by score, with how fresh and how complete the pass behind them is."""
        page = self.list_signals(status="open", sort="score", limit=limit, offset=offset)
        last = self.last_run()
        age = None
        if last and last["finished_at"]:
            age = (self._clock() - datetime.fromisoformat(last["finished_at"])).total_seconds()
        return {
            **page,
            "freshness": {
                "last_run_at": last["finished_at"] if last else None,
                "last_run_status": last["status"] if last else None,
                "age_seconds": age,
                "stale": age is None or age > 24 * 3600,
            },
            "scope": (
                "Candidates come from data BIP already stored for the wallets it ingested. "
                "They are signals to review, not findings, and not attribution."
            ),
        }

    def dismiss(self, signal_id: int, note: str | None) -> dict[str, Any]:
        row = self._row(signal_id)
        if row["status"] != "open":
            raise InvalidRecordError(
                f"only an open signal can be dismissed (it is {row['status']})"
            )
        s = schema.signals
        with self._repo.transaction() as conn:
            conn.execute(
                update(s)
                .where(s.c.id == signal_id)
                .values(status="dismissed", status_note=(note or "dismissed by user")[:300])
            )
        return self.get_signal(signal_id)

    def _require_promotable(self, signal_id: int) -> Row:
        row = self._row(signal_id)
        if row["status"] not in ("open", "promoted"):
            raise InvalidRecordError(f"a {row['status']} signal cannot be promoted")
        return row

    def promote_to_watchlist(
        self, signal_id: int, watchlist_id: int, note: str | None
    ) -> dict[str, Any]:
        row = self._require_promotable(signal_id)
        if row["subject_kind"] == "asset":
            raise InvalidRecordError("a native-asset signal has no address to watch")
        self._repo.add_watchlist_entry(
            watchlist_id,
            str(row["chain"]),
            str(row["subject_ref"]),
            (note or f"from signal {signal_id}: {row['title']}")[:500],
        )
        return self._mark_promoted(signal_id, f"watchlist:{watchlist_id}")

    def promote_to_investigation(self, signal_id: int, investigation_id: int) -> dict[str, Any]:
        row = self._require_promotable(signal_id)
        finding = self._finding_from(row)
        self._repo.add_finding(investigation_id, finding)
        return self._mark_promoted(signal_id, f"investigation:{investigation_id}")

    def _mark_promoted(self, signal_id: int, target: str) -> dict[str, Any]:
        s = schema.signals
        with self._repo.transaction() as conn:
            conn.execute(
                update(s).where(s.c.id == signal_id).values(status="promoted", promoted_to=target)
            )
        return self.get_signal(signal_id)

    def _finding_from(self, row: Row) -> Finding:
        signal = self._signal(row)
        chain = str(row["chain"])
        tx_ids = [e["ref"] for e in signal["evidence"] if e["kind"] == "transaction"][:20]
        t = schema.transactions
        with self._repo.transaction() as conn:
            stored = conn.execute(
                select(t.c.tx_id, t.c.provenance_json).where(
                    t.c.chain == chain, t.c.tx_id.in_(tx_ids), canonical_transaction()
                )
            ).all()
        evidence: list[EvidenceRef] = [
            EvidenceRef(
                evidence_id="metric",
                kind=EvidenceKind.DERIVED_METRIC,
                locator=f"signals/{row['id']}",
                description=str(row["title"])[:4000],
            )
        ]
        for i, (tx_id, prov_json) in enumerate(sorted(stored)):
            prov = Provenance.model_validate_json(prov_json)
            if prov.verification_status is VerificationStatus.SINGLE_SOURCE:
                prov = prov.model_copy(
                    update={"verification_status": VerificationStatus.UNVERIFIED}
                )
            evidence.append(
                EvidenceRef(
                    evidence_id=f"tx{i}",
                    kind=EvidenceKind.CHAIN_RECORD,
                    locator=f"transactions/{chain}/{tx_id}",
                    provenance=prov,
                    description=f"Stored {chain} transaction supporting the signal",
                )
            )
        kind = {
            "wallet": EntityKind.ADDRESS,
            "contract": EntityKind.CONTRACT,
            "pool": EntityKind.CONTRACT,
            "token": EntityKind.TOKEN,
            "asset": EntityKind.TOKEN,
        }.get(str(row["subject_kind"]), EntityKind.ADDRESS)
        explanation = signal["explanation"]
        limitations = [
            str(signal["coverage"].get("limitations") or "Computed from stored data only."),
            str(explanation.get("caveat") or explanation.get("not_claimed") or "")
            or "A signal is a candidate for review, not a verified fact.",
        ]
        claim = Claim(
            statement=str(explanation.get("statement", row["title"]))[:4000],
            claim_type=ClaimType.STATISTICAL_OBSERVATION,
            evidence_ids=tuple(e.evidence_id for e in evidence),
            confidence=float(row["confidence"]),
            method=str(explanation.get("method", row["detector_id"]))[:256],
        )
        return Finding(
            finding_id=f"signal-{row['id']}-{row['detector_id']}"[:128],
            finding_type=str(row["detector_id"]).replace("_", "-"),
            subject=EntityRef(kind=kind, identifier=str(row["subject_ref"]), chain=chain),
            claims=(claim,),
            evidence=tuple(evidence),
            detected_at=row["first_seen_at"],
            explanation=json.dumps(explanation)[:4000],
            status=FindingStatus.UNVERIFIED,
            confidence=float(row["confidence"]),
            limitations=tuple(x[:4000] for x in limitations if x),
            produced_by=f"{row['detector_id']}@{row['detector_version']}",
        )

    # ------------------------------------------------------------------ performance

    def performance(self) -> dict[str, Any]:
        """How each detector's signals fared when a person looked at them.

        ``dismissal_rate`` is dismissed / (dismissed + promoted). It is an analyst's judgement
        about usefulness, not ground truth: a promoted signal is not confirmed and a dismissed one
        is not proven wrong. The rate is withheld until ``MIN_REVIEWED`` signals were decided, so a
        handful of reviews cannot pass for a measurement.
        """
        s = schema.signals
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(s.c.detector_id, s.c.status, func.count()).group_by(
                    s.c.detector_id, s.c.status
                )
            ).all()
        counts: dict[str, dict[str, int]] = {d.info.id: {} for d in DETECTORS}
        for detector, status, n in rows:
            counts.setdefault(str(detector), {})[str(status)] = int(n)
        out: list[dict[str, Any]] = []
        for detector_id, by_status in sorted(counts.items()):
            total = sum(by_status.values())
            reviewed = by_status.get("dismissed", 0) + by_status.get("promoted", 0)
            out.append(
                {
                    "detector": detector_id,
                    "total": total,
                    "by_status": {k: by_status.get(k, 0) for k in SIGNAL_STATUSES},
                    "reviewed": reviewed,
                    "dismissal_rate": round(by_status.get("dismissed", 0) / reviewed, 3)
                    if reviewed >= MIN_REVIEWED
                    else None,
                    "invalidated_share": round(by_status.get("invalidated", 0) / total, 3)
                    if total
                    else None,
                    "assessment": "measured"
                    if reviewed >= MIN_REVIEWED
                    else "insufficient_reviews",
                }
            )
        return {
            "min_reviewed": MIN_REVIEWED,
            "detectors": out,
            "caveats": [
                "Dismissal and promotion are analyst decisions, not ground truth.",
                "A promoted signal is a lead to investigate, not a confirmed finding.",
            ],
        }

    # ------------------------------------------------------------------ retention

    def prune(self, *, older_than_days: int) -> dict[str, int]:
        """Delete closed signals (and their alerts and evidence) older than the cutoff.

        Only ``dismissed``, ``expired`` and ``invalidated`` signals are removed; open and promoted
        ones are never pruned.
        """
        s = schema.signals
        cutoff = self._clock() - timedelta(days=older_than_days)
        with self._repo.transaction() as conn:
            result = conn.execute(
                s.delete().where(
                    s.c.status.in_(("dismissed", "expired", "invalidated")),
                    s.c.last_seen_at < cutoff,
                )
            )
        return {"signals_deleted": int(result.rowcount or 0)}
