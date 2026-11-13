"""Bounded relationship expansion: discovery without supplied addresses (Stage 3, schema v18).

A policy starts from *seeds* BIP chose itself from stored data (the subjects of strong signals and
of candidates under review, or the deployers of recently seen contracts) and walks the stored
relationship graph outward, both directions, to a fixed depth. It never contacts a provider and
never recurses without a bound: every run is limited by depth, entities reached, relationship rows
read and seeds. Each entity examined is logged in ``discovery_examined``, so an entity is not
re-examined by the same policy within its cooldown and an interrupted run resumes with fresh seeds.

An entity reached from two or more distinct seeds becomes a *candidate*: a normal discovery signal
(same scoring, deduplication, cooldown and alerts) whose explanation lists the seeds and the paths.
Shared links are a lead for a person to review, never proof of common control or intent, so these
candidates are labelled hypotheses and their confidence is capped low.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import insert, or_, select, update

from bip.config.settings import Settings
from bip.db import schema
from bip.db.errors import InvalidRecordError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.discovery.detectors import DetectorInfo, DetectorOutput, RawCandidate
from bip.discovery.review import ReviewService
from bip.discovery.service import DiscoveryService

VERSION = "1"
REVIEW_SEED_STATES = ("accepted", "monitoring", "investigating")


@dataclass(frozen=True, slots=True)
class Policy:
    id: str
    objective: str
    seeds: str
    stopping: str


POLICIES: dict[str, Policy] = {
    "shared_neighbourhood": Policy(
        "shared_neighbourhood",
        "Find entities linked to two or more seed entities (a shared funder, deployer or "
        "counterparty) within a bounded number of relationship steps.",
        "Subjects of open high-severity signals and of candidates a person accepted, is "
        "monitoring or is investigating.",
        "Depth, entities reached, relationship rows read and seeds are all capped; entities "
        "examined within the cooldown are skipped.",
    ),
    "deployer_network": Policy(
        "deployer_network",
        "Find funders and wallets connected to several recent contract deployers.",
        "Deployers of the most recently observed contract deployments.",
        "Depth, entities reached, relationship rows read and seeds are all capped; entities "
        "examined within the cooldown are skipped.",
    ),
}


@dataclass
class _Reach:
    kind: str
    depth: int
    seeds: dict[str, int] = field(default_factory=dict[str, int])
    """Seeds that reach this entity, with their distance (always within the depth limit)."""
    paths: list[list[str]] = field(default_factory=list[list[str]])
    tx_ids: set[str] = field(default_factory=set[str])


class _Producer:
    """Detector-shaped wrapper so candidates reuse the discovery scoring and storage."""

    def __init__(self, policy: Policy) -> None:
        self.info = DetectorInfo(
            id=f"expansion_{policy.id}",
            version=VERSION,
            family="related_wallets",
            title=f"Relationship expansion: {policy.id.replace('_', ' ')}",
            method=policy.objective,
            data_required="stored relationships (funding, deployment, repeated transfers)",
            coverage="only relationships BIP has stored and that are active (not invalidated)",
            limitations="shared links are leads, not proof of common control; the graph holds "
            "only what the collectors stored",
            refresh="on demand or by the off-by-default expansion job",
            cost="local database only",
            confidence_cap=0.4,
            chains=("ethereum", "solana"),
        )


class ExpansionEngine:
    def __init__(
        self,
        repo: Repository,
        settings: Settings,
        discovery: DiscoveryService,
        review: ReviewService,
    ) -> None:
        self._repo = repo
        self._cfg = settings.memory
        self._discovery = discovery
        self._review = review

    @staticmethod
    def policies() -> list[dict[str, str]]:
        return [
            {"id": p.id, "objective": p.objective, "seeds": p.seeds, "stopping": p.stopping}
            for p in POLICIES.values()
        ]

    def budgets(self) -> dict[str, int]:
        c = self._cfg
        return {
            "max_depth": c.expansion_max_depth,
            "max_entities": c.expansion_max_entities,
            "max_relationship_rows": c.expansion_max_rows,
            "max_seeds": c.expansion_max_seeds,
            "cooldown_days": c.expansion_cooldown_days,
        }

    def run(self, policy_id: str, chain: str) -> dict[str, Any]:
        policy = POLICIES.get(policy_id)
        if policy is None:
            raise InvalidRecordError(f"unknown policy; choose one of {', '.join(POLICIES)}")
        cfg = self._cfg
        cooldown = utcnow() - timedelta(days=cfg.expansion_cooldown_days)
        recent = self._recently_examined(policy.id, chain, cooldown)
        seeds = self._seeds(policy.id, chain)[: cfg.expansion_max_seeds]
        # Every chosen seed is expanded each run, so two seeds can meet within one run. The
        # cooldown only stops *intermediate* entities whose edges were read recently from being
        # read again (busy hubs); they can still be reached and become candidates.
        reach: dict[str, _Reach] = {ref: _Reach(kind, 0, {ref: 0}, [[ref]]) for ref, kind in seeds}
        expanded: set[str] = set()
        cooled: set[str] = set()
        rows_read = 0
        truncated: list[str] = []
        queue: deque[str] = deque(ref for ref, _ in seeds)
        while queue:
            ref = queue.popleft()
            node = reach[ref]
            if node.depth >= cfg.expansion_max_depth:
                continue
            if node.depth > 0 and ref in recent:
                cooled.add(ref)
                continue
            if rows_read >= cfg.expansion_max_rows:
                truncated.append("relationship_rows")
                break
            edges = self._edges(chain, ref, cfg.expansion_max_rows - rows_read)
            rows_read += len(edges)
            expanded.add(ref)
            for other, kind, rel_kind, tx_ids in edges:
                nxt = reach.get(other)
                if nxt is None:
                    if len(reach) >= cfg.expansion_max_entities + len(seeds):
                        if "entities" not in truncated:
                            truncated.append("entities")
                        continue
                    nxt = reach[other] = _Reach(kind, node.depth + 1)
                    queue.append(other)
                gained = False
                for seed, dist in node.seeds.items():
                    if dist + 1 > cfg.expansion_max_depth:
                        continue  # a seed only counts within the depth limit
                    if seed not in nxt.seeds or dist + 1 < nxt.seeds[seed]:
                        nxt.seeds[seed] = dist + 1
                        gained = True
                if gained and len(nxt.paths) < 5:
                    for path in node.paths[:2]:
                        nxt.paths.append([*path, f"-{rel_kind}-", other])
                if gained and other in expanded and other not in queue:
                    expanded.discard(other)  # it holds new seeds: pass them on
                    queue.append(other)
                nxt.tx_ids |= tx_ids
        candidates = [
            self._candidate(policy, chain, ref, r)
            for ref, r in reach.items()
            if r.depth > 0 and len(r.seeds) >= 2
        ]
        output = DetectorOutput(candidates, rows_read, bool(truncated))
        extra = {
            "producer": "relationship_expansion",
            "policy": {"id": policy.id, "objective": policy.objective, "seeds": policy.seeds},
            "budgets": self.budgets(),
            "seeds_used": len(seeds),
            "entities_examined": len(reach),
            "entities_expanded": len(expanded),
            "relationship_rows_read": rows_read,
            "truncated": truncated,
            "not_expanded_cooldown": len(cooled),
            "source": "local database only",
        }
        result = self._discovery.run_producer(_Producer(policy), output, chain, extra)
        # Logged only after the candidates are stored, so a failure leaves nothing on cooldown.
        depth_limited = {
            ref
            for ref, r in reach.items()
            if ref not in expanded and ref not in cooled and r.depth >= cfg.expansion_max_depth
        }
        self._log(
            policy.id,
            chain,
            reach,
            {c.subject_ref for c in candidates},
            expanded,
            depth_limited,
            result.get("run_id"),
        )
        return result

    # ------------------------------------------------------------------ pieces

    def _seeds(self, policy_id: str, chain: str) -> list[tuple[str, str]]:
        s, rel = schema.signals, schema.entity_relationships
        out: dict[str, str] = {}
        limit = self._cfg.expansion_max_seeds * 3
        with self._repo.transaction() as conn:
            if policy_id == "deployer_network":
                rows = conn.execute(
                    select(rel.c.target_ref)
                    .where(
                        rel.c.chain == chain, rel.c.kind == "deployed_by", rel.c.status == "active"
                    )
                    .order_by(rel.c.last_seen_at.desc())
                    .limit(limit)
                ).all()
                for (ref,) in rows:
                    out.setdefault(str(ref), "wallet")
                return list(out.items())
            rows = conn.execute(
                select(s.c.subject_ref, s.c.subject_kind)
                .where(
                    s.c.chain == chain,
                    s.c.status == "open",
                    s.c.severity == "high",
                    s.c.subject_kind.in_(("wallet", "contract")),
                )
                .order_by(s.c.score.desc())
                .limit(limit)
            ).all()
            for ref, kind in rows:
                out.setdefault(str(ref), str(kind))
        reviewed: list[int] = []
        for state in REVIEW_SEED_STATES:
            reviewed.extend(self._review.ids_in_state(state, limit))
        if reviewed:
            with self._repo.transaction() as conn:
                for ref, kind in conn.execute(
                    select(s.c.subject_ref, s.c.subject_kind).where(
                        s.c.id.in_(reviewed[:limit]),
                        s.c.chain == chain,
                        s.c.subject_kind.in_(("wallet", "contract")),
                    )
                ).all():
                    out.setdefault(str(ref), str(kind))
        return list(out.items())

    def _edges(self, chain: str, ref: str, budget: int) -> list[tuple[str, str, str, set[str]]]:
        rel, ev = schema.entity_relationships, schema.relationship_evidence
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(
                    rel.c.id,
                    rel.c.kind,
                    rel.c.source_ref,
                    rel.c.source_kind,
                    rel.c.target_ref,
                    rel.c.target_kind,
                )
                .where(
                    rel.c.chain == chain,
                    rel.c.status == "active",
                    or_(rel.c.source_ref == ref, rel.c.target_ref == ref),
                )
                .order_by(rel.c.id)
                .limit(max(budget, 0))
            ).all()
            ids = [int(r.id) for r in rows]
            txs: dict[int, set[str]] = {}
            for start in range(0, len(ids), 400):
                for rid, tx in conn.execute(
                    select(ev.c.relationship_id, ev.c.tx_id).where(
                        ev.c.relationship_id.in_(ids[start : start + 400])
                    )
                ).all():
                    txs.setdefault(int(rid), set()).add(str(tx))
        out: list[tuple[str, str, str, set[str]]] = []
        for r in rows:
            if r.source_ref == ref:
                out.append(
                    (str(r.target_ref), str(r.target_kind), str(r.kind), txs.get(int(r.id), set()))
                )
            else:
                out.append(
                    (str(r.source_ref), str(r.source_kind), str(r.kind), txs.get(int(r.id), set()))
                )
        return out

    def _recently_examined(self, policy_id: str, chain: str, since: Any) -> set[str]:
        d = schema.discovery_examined
        with self._repo.transaction() as conn:
            return {
                str(r)
                for (r,) in conn.execute(
                    select(d.c.ref).where(
                        d.c.policy == policy_id,
                        d.c.chain == chain,
                        d.c.examined_at >= since,
                        d.c.outcome.in_(("examined", "candidate")),  # edges were read
                    )
                ).all()
            }

    def _candidate(self, policy: Policy, chain: str, ref: str, r: _Reach) -> RawCandidate:
        n = len(r.seeds)
        return RawCandidate(
            detector_id=f"expansion_{policy.id}",
            detector_version=VERSION,
            family="related_wallets",
            chain=chain,
            subject_kind=r.kind if r.kind in ("wallet", "contract") else "wallet",
            subject_ref=ref,
            title=f"Linked to {n} seed entities within {r.depth} relationship step(s)",
            key=f"expansion:{policy.id}:{chain}:{ref}",
            observed_at=utcnow(),
            tx_ids=sorted(r.tx_ids)[:50],
            magnitude=min(1.0, n / 5.0),
            baseline_reliability=min(1.0, len(r.tx_ids) / 10.0),
            confidence_cap=0.4,
            explanation={
                "evidence_class": "hypothesis",
                "observed_facts": f"{n} seed entities reach this entity through stored, active "
                "relationships",
                "interpretation": "a shared funder, deployer or counterparty is a lead for "
                "review; it is not proof of common control, coordination or intent",
                "policy": policy.id,
                "objective": policy.objective,
                "seeds_linked": sorted(r.seeds)[:20],
                "seed_distances": dict(sorted(r.seeds.items())[:20]),
                "paths": r.paths[:5],
                "depth": r.depth,
            },
            z=float(n),
        )

    def _log(
        self,
        policy_id: str,
        chain: str,
        reach: dict[str, _Reach],
        candidates: set[str],
        expanded: set[str],
        depth_limited: set[str],
        run_id: object,
    ) -> None:
        """Record the entities whose edges were read (examined or candidate) and those left
        unexpanded by the depth limit. Entities skipped by the cooldown keep their earlier row,
        so the cooldown is not extended by being reached again."""
        d = schema.discovery_examined
        now = utcnow()
        with self._repo.transaction() as conn:
            for ref, r in reach.items():
                if ref in expanded:
                    outcome = "candidate" if ref in candidates else "examined"
                elif ref in depth_limited:
                    outcome = "skipped_depth"  # reached, edges not read; starts no cooldown
                else:
                    continue
                values = {
                    "depth": r.depth,
                    "via_ref": min(r.seeds, key=lambda k: (r.seeds[k], k)) if r.seeds else None,
                    "outcome": outcome,
                    "run_id": run_id if isinstance(run_id, int) else None,
                    "examined_at": now,
                }
                hit = conn.execute(
                    update(d)
                    .where(d.c.policy == policy_id, d.c.chain == chain, d.c.ref == ref)
                    .values(**values)
                )
                if hit.rowcount == 0:
                    conn.execute(insert(d).values(policy=policy_id, chain=chain, ref=ref, **values))
