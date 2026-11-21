"""Analysis runner: reproducible snapshots, anomaly observations and findings.

``analyze`` loads one wallet's stored observations, computes profile, behaviour rules and
anomaly detectors, and stores:

* an ``analysis_runs`` row keyed by an input hash (stored records + their verification state
  + parameters + analyzer version). Same inputs => the earlier run is reused, not recomputed;
* one ``anomaly_observations`` row per detector (every status, so "could not run" is visible);
* a :class:`~bip.contracts.findings.Finding` per anomaly / detected hypothesis rule, with a
  deterministic id, so a refresh never duplicates findings.

Source observations are only read, never modified.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update

from bip.config.settings import IntelSettings, Settings
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
from bip.db import schema
from bip.db.canonical import canonical_transaction
from bip.db.errors import RecordNotFoundError
from bip.db.repository import Repository, canonical_address
from bip.db.types import utcnow
from bip.intel import ANALYZER_VERSION, anomaly, behaviour, profile
from bip.intel.common import J, iso, label
from bip.intel.dataset import WalletDataset, load_wallet


def _params(settings: Settings, as_of: datetime) -> J:
    return {
        "intel": settings.intel.model_dump(),
        "as_of": as_of.date().isoformat(),
        "analyzer_version": ANALYZER_VERSION,
    }


def as_of_now() -> datetime:
    """Analysis reference time: the end of the current UTC day (stable within a day)."""
    today = utcnow().astimezone(UTC).date()
    return datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)


def first_seen_targets(repo: Repository, ds: WalletDataset) -> dict[str, datetime | None]:
    t = schema.transactions
    targets: set[str] = set()
    for tx in ds.transactions:
        if ds.chain == "solana":
            targets.update(tx.program_ids)
        elif tx.to_address and tx.from_address == ds.address:
            targets.add(tx.to_address)
    out: dict[str, datetime | None] = {}
    with repo.transaction() as conn:
        for target in sorted(targets)[:100]:
            cond = (
                func.instr(t.c.program_ids_json, json.dumps(target)) > 0  # exact, case-sensitive
                if ds.chain == "solana"
                else t.c.to_address == target
            )
            out[target] = conn.execute(
                select(func.min(t.c.block_time)).where(
                    t.c.chain == ds.chain, cond, canonical_transaction()
                )
            ).scalar_one()
    return out


def finding_id(detector_id: str, chain: str, address: str, tx_ids: list[str]) -> str:
    digest = hashlib.sha256("|".join([detector_id, chain, address, *sorted(tx_ids)]).encode())
    return f"bip.{detector_id}.{digest.hexdigest()[:24]}"


class IntelligenceService:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings

    @property
    def cfg(self) -> IntelSettings:
        return self._settings.intel

    def dataset(self, chain: str, address: str) -> WalletDataset:
        return load_wallet(self._repo, chain, address, self._settings.intel.max_records_per_wallet)

    # ------------------------------------------------------------------ analysis

    def analyze(self, chain: str, address: str) -> J:
        ds = self.dataset(chain, address)
        as_of = as_of_now()
        params = _params(self._settings, as_of)
        input_hash = ds.input_hash(params)
        ar = schema.analysis_runs
        with self._repo.transaction() as conn:
            prior = conn.execute(
                select(ar.c.id)
                .where(
                    ar.c.chain == ds.chain,
                    ar.c.address == ds.address,
                    ar.c.input_hash == input_hash,
                    ar.c.analyzer_version == ANALYZER_VERSION,
                    ar.c.status == "succeeded",
                )
                .order_by(ar.c.id.desc())
            ).first()
        if prior is not None:
            run = self.get_run(int(prior[0]))
            return {**run, "reused": True}
        with self._repo.transaction() as conn:
            pk = conn.execute(
                insert(ar).values(
                    chain=ds.chain,
                    address=ds.address,
                    analyzer_version=ANALYZER_VERSION,
                    input_hash=input_hash,
                    params_json=json.dumps(params, default=str),
                    started_at=utcnow(),
                    status="running",
                )
            ).inserted_primary_key
        assert pk is not None  # noqa: S101
        run_id = int(pk[0])
        try:
            prof = profile.build_profile(ds)
            rules = behaviour.evaluate(ds, self.cfg, as_of)
            ctx = anomaly.Context(now=as_of, first_seen_targets=first_seen_targets(self._repo, ds))
            detections = anomaly.run_detectors(ds, self.cfg, ctx)
            findings = self._record(run_id, ds, rules, detections)
            result = {
                "scope": prof["scope"],
                "metrics": prof["metrics"],
                "verification": prof["verification"],
                "behaviour": [
                    {
                        k: r[k]
                        for k in ("rule_id", "name", "status", "magnitude", "supporting_count")
                    }
                    for r in rules
                ],
                "anomalies": [
                    {k: d[k] for k in ("detector_id", "status", "severity")} for d in detections
                ],
                "findings": findings,
            }
            status = "succeeded"
        except Exception as exc:
            result = {"error": type(exc).__name__}
            status = "failed"
            with self._repo.transaction() as conn:
                conn.execute(
                    update(ar)
                    .where(ar.c.id == run_id)
                    .values(status=status, finished_at=utcnow(), result_json=json.dumps(result))
                )
            raise
        with self._repo.transaction() as conn:
            conn.execute(
                update(ar)
                .where(ar.c.id == run_id)
                .values(
                    status=status, finished_at=utcnow(), result_json=json.dumps(result, default=str)
                )
            )
        return {**self.get_run(run_id), "reused": False}

    def get_run(self, run_id: int) -> J:
        ar = schema.analysis_runs
        with self._repo.transaction() as conn:
            r = conn.execute(select(ar).where(ar.c.id == run_id)).mappings().first()
        if r is None:
            raise RecordNotFoundError(f"analysis run {run_id} not found")
        return {
            "run_id": r["id"],
            "chain": r["chain"],
            "address": r["address"],
            "analyzer_version": r["analyzer_version"],
            "input_hash": r["input_hash"],
            "params": json.loads(r["params_json"]),
            "started_at": iso(r["started_at"]),
            "finished_at": iso(r["finished_at"]),
            "status": r["status"],
            "result": json.loads(r["result_json"]) if r["result_json"] else None,
        }

    def latest_run(self, chain: str, address: str) -> J | None:
        ref = canonical_address(chain, address)
        ar = schema.analysis_runs
        with self._repo.transaction() as conn:
            r = conn.execute(
                select(ar.c.id)
                .where(ar.c.chain == ref.chain, ar.c.address == ref.address)
                .order_by(ar.c.id.desc())
            ).first()
        return self.get_run(int(r[0])) if r else None

    def _record(
        self, run_id: int, ds: WalletDataset, rules: list[J], detections: list[J]
    ) -> list[str]:
        ao = schema.anomaly_observations
        now = utcnow()
        produced: list[str] = []
        for d in detections:
            fid = None
            if d["status"] == "anomaly":
                fid = self._finding_from_detection(ds, d, now)
                produced.append(fid)
            with self._repo.transaction() as conn:
                conn.execute(
                    insert(ao).values(
                        run_id=run_id,
                        chain=ds.chain,
                        address=ds.address,
                        detector_id=d["detector_id"],
                        detector_version=d["version"],
                        status=d["status"],
                        severity=d["severity"],
                        confidence=str(d["confidence"]["value"]) if d["confidence"] else None,
                        window_start=_dt(d["window_start"]),
                        window_end=_dt(d["window_end"]),
                        payload_json=json.dumps(d, default=str),
                        finding_id=fid,
                        created_at=now,
                    )
                )
        for r in rules:
            if r["status"] == "detected" and r["claim_type"] == "hypothesis":
                produced.append(self._finding_from_rule(ds, r, now))
        return produced

    # ------------------------------------------------------------------ findings

    def _evidence(self, ds: WalletDataset, tx_ids: list[str]) -> list[EvidenceRef]:
        t = schema.transactions
        by_tx = {x.tx_id: x for x in ds.transactions}
        refs: list[EvidenceRef] = []
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(t.c.tx_id, t.c.provenance_json).where(
                    t.c.chain == ds.chain, t.c.tx_id.in_(tx_ids[:50])
                )
            ).all()
        for i, (tx_id, prov_json) in enumerate(sorted(rows)):
            prov = Provenance.model_validate_json(prov_json)
            row = by_tx.get(tx_id)
            effective = row.verification if row else prov.verification_status.value
            if effective == "cross_verified":
                prov = prov.model_copy(
                    update={"verification_status": VerificationStatus.CROSS_VERIFIED}
                )
            elif prov.verification_status is VerificationStatus.SINGLE_SOURCE:
                prov = prov.model_copy(
                    update={"verification_status": VerificationStatus.UNVERIFIED}
                )
            refs.append(
                EvidenceRef(
                    evidence_id=f"tx{i}",
                    kind=EvidenceKind.CHAIN_RECORD,
                    locator=f"transactions/{ds.chain}/{tx_id}",
                    provenance=prov,
                    description=f"Stored {ds.chain} transaction ({label(effective)})",
                )
            )
        return refs

    def _store_finding(self, finding: Finding) -> str:
        f = schema.findings
        with self._repo.transaction() as conn:
            exists = conn.execute(
                select(f.c.id).where(f.c.finding_id == finding.finding_id)
            ).first()
        if exists is None:
            self._repo.add_finding(None, finding)
        else:
            # same evidence, recomputed: keep the id (no duplicate), refresh status/confidence
            self._repo.refresh_finding(finding)
        return finding.finding_id

    def _make_finding(
        self,
        ds: WalletDataset,
        *,
        detector_id: str,
        version: str,
        title: str,
        statement: str,
        claim_type: ClaimType,
        method: str,
        explanation: str,
        tx_ids: list[str],
        conf: float | None,
        limitations: list[str],
        window: tuple[datetime | None, datetime | None],
        now: datetime,
    ) -> str:
        evidence = self._evidence(ds, tx_ids)
        metric = EvidenceRef(
            evidence_id="metric",
            kind=EvidenceKind.DERIVED_METRIC,
            locator=f"analysis/{detector_id}/{version}",
            description=title[:4000],
        )
        all_ev = [metric, *evidence]
        ids = tuple(e.evidence_id for e in all_ev)
        claims = [
            Claim(
                statement=statement[:4000],
                claim_type=claim_type,
                evidence_ids=ids,
                confidence=conf,
                method=method[:256],
            )
        ]
        corroborated = [
            e
            for e in evidence
            if e.provenance is not None
            and e.provenance.verification_status is VerificationStatus.CROSS_VERIFIED
        ]
        if corroborated:
            claims.append(
                Claim(
                    statement=f"{len(corroborated)} supporting transaction(s) were corroborated by "
                    "independent providers.",
                    claim_type=ClaimType.VERIFIED_FACT,
                    evidence_ids=tuple(e.evidence_id for e in corroborated),
                )
            )
        start, end = window
        if start is not None and end is not None and end < start:
            start, end = end, start
        if start is not None and start > now:
            start = None
        fid = finding_id(detector_id, ds.chain, ds.address, tx_ids)
        finding = Finding(
            finding_id=fid,
            finding_type=detector_id.replace("_", "-"),
            subject=EntityRef(kind=EntityKind.ADDRESS, identifier=ds.address, chain=ds.chain),
            claims=tuple(claims),
            evidence=tuple(all_ev),
            detected_at=now,
            observation_start=start,
            observation_end=end if start is None or (end is not None and end >= start) else None,
            explanation=explanation[:4000],
            status=FindingStatus.PARTIALLY_VERIFIED if corroborated else FindingStatus.UNVERIFIED,
            confidence=conf,
            limitations=tuple(x[:4000] for x in limitations)
            or ("Computed from the retrieved dataset only.",),
            produced_by=f"{detector_id}@{version}",
        )
        return self._store_finding(finding)

    def _finding_from_detection(self, ds: WalletDataset, d: J, now: datetime) -> str:
        tx_ids = sorted(
            {e["tx_id"] for e in d["evidence"]["transactions"]}
            | {e["tx_id"] for e in d["evidence"]["transfers"]}
        )
        return self._make_finding(
            ds,
            detector_id=d["detector_id"],
            version=d["version"],
            title=d["title"],
            statement=d["explanation"],
            claim_type=ClaimType.STATISTICAL_OBSERVATION,
            method=f"{d['detector_id']}@{d['version']}: {d['thresholds']}",
            explanation=f"{d['title']} (severity {d['severity']}). {d['explanation']}",
            tx_ids=tx_ids,
            conf=d["confidence"]["value"] if d["confidence"] else None,
            limitations=["Computed from the retrieved dataset only.", *d["limitations"]],
            window=(_dt(d["window_start"]), _dt(d["window_end"])),
            now=now,
        )

    def _finding_from_rule(self, ds: WalletDataset, r: J, now: datetime) -> str:
        tx_ids = sorted(
            {e["tx_id"] for e in r["supporting_transactions"]}
            | {e["tx_id"] for e in r["supporting_transfers"]}
        )
        return self._make_finding(
            ds,
            detector_id=r["rule_id"],
            version=r["rule_version"],
            title=r["name"],
            statement=r["explanation"],
            claim_type=ClaimType.HYPOTHESIS,
            method=f"{r['rule_id']}@{r['rule_version']}: {json.dumps(r['inputs'])}",
            explanation=f"{r['name']}: {r['explanation']}",
            tx_ids=tx_ids,
            conf=r["confidence"]["value"] if r["confidence"] else None,
            limitations=[
                "Hypothesis only: no identity or operator is established.",
                "Computed from the retrieved dataset only.",
            ],
            window=(_dt(r["window"]["start"]), _dt(r["window"]["end"])),
            now=now,
        )

    # ------------------------------------------------------------------ anomaly queries

    def anomalies(
        self,
        *,
        chain: str | None,
        address: str | None,
        status: str | None,
        watchlist_id: int | None,
        limit: int,
        offset: int,
    ) -> tuple[list[J], int]:
        """Detector results from each wallet's latest analysis run."""
        ao, ar = schema.anomaly_observations, schema.analysis_runs
        latest = (
            select(ar.c.chain, ar.c.address, func.max(ar.c.id).label("rid"))
            .where(ar.c.status == "succeeded")
            .group_by(ar.c.chain, ar.c.address)
            .subquery()
        )
        q = select(ao).join(latest, ao.c.run_id == latest.c.rid)
        if chain:
            q = q.where(ao.c.chain == chain)
        if address and chain:
            q = q.where(ao.c.address == canonical_address(chain, address).address)
        if status:
            q = q.where(ao.c.status == status)
        if watchlist_id is not None:
            we = schema.watchlist_entries
            q = q.where(
                ao.c.chain.in_(select(we.c.chain).where(we.c.watchlist_id == watchlist_id)),
                ao.c.address.in_(select(we.c.address).where(we.c.watchlist_id == watchlist_id)),
            )
        with self._repo.transaction() as conn:
            total = int(conn.execute(select(func.count()).select_from(q.subquery())).scalar_one())
            rows = (
                conn.execute(q.order_by(ao.c.id.desc()).limit(limit).offset(offset))
                .mappings()
                .all()
            )
        return [_anomaly_row(r) for r in rows], total

    def anomaly(self, ref: str) -> J:
        """One anomaly by finding id, or by observation id (``obs-<n>``)."""
        ao = schema.anomaly_observations
        cond = (
            (ao.c.id == int(ref[4:]))
            if ref.startswith("obs-") and ref[4:].isdigit()
            else ao.c.finding_id == ref
        )
        with self._repo.transaction() as conn:
            r = conn.execute(select(ao).where(cond).order_by(ao.c.id.desc())).mappings().first()
            f = schema.findings
            fr = conn.execute(
                select(f.c.payload_json, f.c.investigation_id).where(f.c.finding_id == ref)
            ).first()
        if r is None:
            raise RecordNotFoundError(f"anomaly {ref} not found")
        out = _anomaly_row(r)
        out["finding"] = json.loads(fr[0]) if fr else None
        out["investigation_id"] = fr[1] if fr else None
        return out

    def attach_finding(self, finding_id: str, investigation_id: int) -> J:
        f, i = schema.findings, schema.investigations
        with self._repo.transaction() as conn:
            if conn.execute(select(i.c.id).where(i.c.id == investigation_id)).first() is None:
                raise RecordNotFoundError(f"investigation {investigation_id} not found")
            res = conn.execute(
                update(f)
                .where(f.c.finding_id == finding_id)
                .values(investigation_id=investigation_id)
            )
            if res.rowcount == 0:
                raise RecordNotFoundError(f"finding {finding_id} not found")
            conn.execute(update(i).where(i.c.id == investigation_id).values(updated_at=utcnow()))
        return {"finding_id": finding_id, "investigation_id": investigation_id}

    def wallet_findings(self, chain: str, address: str, limit: int = 50) -> list[J]:
        ref = canonical_address(chain, address)
        f = schema.findings
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(f.c.payload_json, f.c.investigation_id)
                .where(
                    func.json_extract(f.c.payload_json, "$.subject.chain") == ref.chain,
                    func.json_extract(f.c.payload_json, "$.subject.identifier") == ref.address,
                )
                .order_by(f.c.id.desc())
                .limit(limit)
            ).all()
        out: list[J] = []
        for payload, inv in rows:
            p: dict[str, Any] = json.loads(payload)
            out.append({**p, "investigation_id": inv})
        return out


def _dt(v: object) -> datetime | None:
    return datetime.fromisoformat(v) if isinstance(v, str) else None


def _anomaly_row(r: Any) -> J:
    payload = json.loads(r["payload_json"])
    return {
        **payload,
        "observation_id": f"obs-{r['id']}",
        "run_id": r["run_id"],
        "chain": r["chain"],
        "address": r["address"],
        "finding_id": r["finding_id"],
        "created_at": iso(r["created_at"]),
    }
