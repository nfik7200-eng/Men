"""Opportunity *research leads*: reproducible, evidence-backed prompts for human research.

A lead is never investment advice and never a buy/sell signal. It says: "in BIP's stored data,
this token / program / contract drew participation from more distinct addresses recently than
before". Selection bias is explicit: BIP only holds data for wallets someone chose to ingest.

Rule ``rising_participation`` v1.0.0 (per chain, per subject):

* windows anchored at the latest stored event time T (not the wall clock):
  recent = (T - 7d, T], prior = (T - 14d, T - 7d];
* minimum data: >= 3 distinct participants and >= 10 events in the recent window, and at
  least 3 ingested wallets on the chain overall;
* candidate if recent participants >= 2 x max(prior participants, 1).

Score (0..1) is a transparent sum of weighted features, each reported with its contribution.
Leads whose evidence is not fully corroborated are labelled "unverified research lead".
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update

from bip.db import schema
from bip.db.canonical import canonical_transaction, orphaned_tx_ids
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.intel.common import J, iso, label, verification_summary
from bip.intel.dataset import effective_verification
from bip.intel.profile import KNOWN_PROGRAMS

RULE_ID = "rising_participation"
RULE_VERSION = "1.0.0"
WINDOW = timedelta(days=7)
MIN_PARTICIPANTS = 3
MIN_EVENTS = 10
MIN_WALLETS = 3
GROWTH = 2.0
WEIGHTS = {"participant_growth": 0.4, "breadth": 0.3, "activity": 0.2, "corroboration": 0.1}


def candidate_key(kind: str, chain: str, subject: str) -> str:
    return hashlib.sha256(f"{RULE_ID}|{kind}|{chain}|{subject}".encode()).hexdigest()


def _events(repo: Repository, chain: str) -> list[tuple[str, str, str, datetime, str, str]]:
    """(kind, subject, participant, time, tx_id, event_id) for the whole chain. One stored
    transfer or transaction is one event, even though it has two participants."""
    tr, t = schema.transfers, schema.transactions
    out: list[tuple[str, str, str, datetime, str, str]] = []
    with repo.transaction() as conn:
        for r in conn.execute(
            select(
                tr.c.id, tr.c.token, tr.c.from_address, tr.c.to_address, tr.c.block_time, tr.c.tx_id
            ).where(
                tr.c.chain == chain,
                tr.c.token.is_not(None),
                tr.c.block_time.is_not(None),
                tr.c.tx_id.not_in(orphaned_tx_ids(chain)),
            )
        ).all():
            for p in (r.from_address, r.to_address):
                if p:
                    out.append(("token", r.token, p, r.block_time, r.tx_id, f"tr:{r.id}"))
        for r in conn.execute(
            select(
                t.c.id,
                t.c.from_address,
                t.c.to_address,
                t.c.program_ids_json,
                t.c.block_time,
                t.c.tx_id,
            ).where(
                t.c.chain == chain,
                t.c.block_time.is_not(None),
                t.c.from_address.is_not(None),
                canonical_transaction(),
            )
        ).all():
            if chain == "solana":
                for p in json.loads(r.program_ids_json or "[]"):
                    if p not in KNOWN_PROGRAMS:
                        out.append(
                            ("program", p, r.from_address, r.block_time, r.tx_id, f"tx:{r.id}")
                        )
            elif r.to_address:
                out.append(
                    ("contract", r.to_address, r.from_address, r.block_time, r.tx_id, f"tx:{r.id}")
                )
    return out


def _ingested_wallets(repo: Repository, chain: str) -> int:
    r = schema.ingestion_runs
    with repo.transaction() as conn:
        return int(
            conn.execute(
                select(func.count(func.distinct(r.c.address))).where(r.c.chain == chain)
            ).scalar_one()
        )


def evaluate(repo: Repository, chain: str) -> J:
    """Compute leads for one chain. Returns status and candidates (not yet persisted)."""
    wallets = _ingested_wallets(repo, chain)
    events = _events(repo, chain)
    base: J = {
        "chain": chain,
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "ingested_wallets": wallets,
        "events_considered": len(events),
    }
    if wallets < MIN_WALLETS or not events:
        return {
            **base,
            "status": "insufficient_data",
            "candidates": [],
            "reason": f"Needs stored events and at least {MIN_WALLETS} ingested wallets on "
            f"{chain}; found {wallets} wallets and {len(events)} events.",
        }
    anchor = max(e[3] for e in events)
    recent_start, prior_start = anchor - WINDOW, anchor - 2 * WINDOW
    groups: dict[tuple[str, str], J] = defaultdict(
        lambda: {"recent": set(), "prior": set(), "events": set(), "tx": set()}
    )
    for kind, subject, party, at, tx_id, event_id in events:
        g = groups[(kind, subject)]
        if at > recent_start:
            g["recent"].add(party)
            g["events"].add(event_id)
            g["tx"].add(tx_id)
        elif at > prior_start:
            g["prior"].add(party)
    candidates: list[J] = []
    for (kind, subject), g in sorted(groups.items()):
        recent, prior = len(g["recent"]), len(g["prior"])
        n_events = len(g["events"])
        if recent < MIN_PARTICIPANTS or n_events < MIN_EVENTS:
            continue
        growth = recent / max(prior, 1)
        if growth < GROWTH:
            continue
        txs = sorted(g["tx"])
        statuses = list(effective_verification(repo, chain, txs).values())
        statuses += ["unverified"] * (len(txs) - len(statuses))
        ver = verification_summary(statuses)
        share = ver["corroborated_share"] or 0.0
        features = {
            "participant_growth": min(math.log2(growth) / 3, 1.0),
            "breadth": min(recent / 10, 1.0),
            "activity": min(n_events / 50, 1.0),
            "corroboration": share,
        }
        contributions = {k: round(WEIGHTS[k] * v, 4) for k, v in features.items()}
        candidates.append(
            {
                "candidate_key": candidate_key(kind, chain, subject),
                "kind": f"rising_{kind}_participation",
                "chain": chain,
                "subject": subject,
                "subject_kind": kind,
                "label": "research lead" if share == 1.0 else "unverified research lead",
                "score": round(sum(contributions.values()), 4),
                "features": {
                    "recent_participants": recent,
                    "prior_participants": prior,
                    "growth_ratio": round(growth, 3),
                    "recent_events": n_events,
                    "corroborated_share": share,
                },
                "feature_scores": {k: round(v, 4) for k, v in features.items()},
                "weights": WEIGHTS,
                "contributions": contributions,
                "observation_period": {
                    "recent_start": iso(recent_start),
                    "anchor": iso(anchor),
                    "prior_start": iso(prior_start),
                },
                "evidence": {"tx_ids": txs[:20], "total": len(txs)},
                "verification": ver,
                "coverage": {"ingested_wallets": wallets, "events_considered": len(events)},
                "uncertainty": [
                    "Participation is measured only over wallets someone chose to ingest into BIP "
                    "(selection bias).",
                    "Growth from a small prior count is fragile.",
                    "Rising activity is not evidence of quality, legitimacy or future returns.",
                    *(
                        []
                        if share == 1.0
                        else ["Not all evidence is corroborated by an independent provider."]
                    ),
                ],
                "disclaimer": "Research lead only. Not investment advice; no buy or sell "
                "recommendation.",
                "rule": {
                    "id": RULE_ID,
                    "version": RULE_VERSION,
                    "min_participants": MIN_PARTICIPANTS,
                    "min_events": MIN_EVENTS,
                    "growth_threshold": GROWTH,
                    "window_days": WINDOW.days,
                },
            }
        )
    candidates.sort(key=lambda c: (-c["score"], c["subject"]))
    return {
        **base,
        "status": "evaluated",
        "anchor": iso(anchor),
        "candidates": candidates,
        "reason": None if candidates else "No subject met the rule.",
    }


def persist(repo: Repository, chain: str, candidates: Iterable[J]) -> int:
    """Upsert by candidate key (no duplicates on refresh); mark vanished leads not current."""
    oc = schema.opportunity_candidates
    now = utcnow()
    keys: set[str] = set()
    with repo.transaction() as conn:
        for c in candidates:
            keys.add(c["candidate_key"])
            payload = json.dumps({**c, "current": True}, default=str)
            exists = conn.execute(
                select(oc.c.id).where(oc.c.candidate_key == c["candidate_key"])
            ).first()
            start = datetime.fromisoformat(c["observation_period"]["recent_start"])
            end = datetime.fromisoformat(c["observation_period"]["anchor"])
            values: dict[str, Any] = {
                "last_detected_at": now,
                "window_start": start,
                "window_end": end,
                "score": str(c["score"]),
                "verification": c["verification"]["overall"],
                "payload_json": payload,
            }
            if exists is None:
                conn.execute(
                    insert(oc).values(
                        candidate_key=c["candidate_key"],
                        kind=c["kind"],
                        chain=chain,
                        subject=c["subject"],
                        first_detected_at=now,
                        **values,
                    )
                )
            else:
                conn.execute(update(oc).where(oc.c.id == exists[0]).values(**values))
        for row in conn.execute(
            select(oc.c.id, oc.c.candidate_key, oc.c.payload_json).where(oc.c.chain == chain)
        ).all():
            if row.candidate_key not in keys:
                p = json.loads(row.payload_json)
                if p.get("current", True):
                    p["current"] = False
                    conn.execute(
                        update(oc).where(oc.c.id == row.id).values(payload_json=json.dumps(p))
                    )
    return len(keys)


def list_candidates(
    repo: Repository, *, chain: str | None, current_only: bool, limit: int, offset: int
) -> tuple[list[J], int]:
    oc = schema.opportunity_candidates
    with repo.transaction() as conn:
        q = select(oc).order_by(oc.c.last_detected_at.desc(), oc.c.id.desc())
        if chain:
            q = q.where(oc.c.chain == chain)
        rows = conn.execute(q).mappings().all()
    items = [_row(r) for r in rows]
    if current_only:
        items = [i for i in items if i["current"]]
    return items[offset : offset + limit], len(items)


def get_candidate(repo: Repository, key: str) -> J | None:
    oc = schema.opportunity_candidates
    with repo.transaction() as conn:
        r = conn.execute(select(oc).where(oc.c.candidate_key == key)).mappings().first()
    return _row(r) if r else None


def _row(r: Any) -> J:
    p = json.loads(r["payload_json"])
    return {
        **p,
        "first_detected_at": iso(r["first_detected_at"]),
        "last_detected_at": iso(r["last_detected_at"]),
        "verification_label": label(r["verification"]) if r["verification"] else None,
    }
