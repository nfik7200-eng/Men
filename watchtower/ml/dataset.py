"""Labelled pair dataset built ONLY from real LIVE listings, with explicit label provenance.

LABEL PROVENANCE (every pair carries `label_source` and `trust`):

  trust = "human"         independent of the rule matcher
      human_confirmed       listing.match_status == 'confirmed' (an admin accepted a review candidate)      -> positive
      human_accepted        MatchCandidate.status == 'accepted'                                             -> positive
      human_rejected        MatchCandidate.status == 'rejected'                                             -> negative
      reviewed_override     a row in a reviewed-labels JSON file (see `load_reviewed_labels`)               -> either
  trust = "rule_derived"  produced by the very rule matcher we want to evaluate  => CIRCULAR
      rule_auto             same product, cross-merchant, match_status == 'auto'                             -> positive
      rule_veto             different products AND hard_conflict / brand conflict / category conflict       -> negative
      distinct_cross_merchant_products
                            different products that each already link >=2 merchants (e.g. Pi 4 4GB vs Pi 5 4GB) -> negative

CIRCULARITY RISK. A 'rule_derived' label is the matcher's own output. Scoring the rule matcher on such labels measures
agreement with itself (it will look perfect), and training a model on them teaches it to imitate the rules, not to be right.
Therefore: rule-derived labels are NEVER counted toward "enough labelled data" (see sufficiency.py), the evaluation report
labels every metric with its trust tier, and the model feature set excludes the features that merely echo the vetoes.
Listings in review state, pairs with conflicting labels, and unlabelled blocked candidates are excluded, never guessed.

LEAKAGE. Splits are by product GROUP (a product's listings, or a singleton listing, form one group). Train pairs use only
groups in the train side and test pairs only groups in the test side; straddling pairs are dropped. So no listing and no
product appears on both sides.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, asdict
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from watchtower import config
from watchtower.models import Listing, MatchCandidate, Source

from . import pairs as P

TRUST_ORDER = {"human": 2, "rule_derived": 1}


@dataclass(frozen=True)
class LabelledPair:
    a_id: int
    b_id: int
    label: int  # 1 = same product, 0 = different product
    label_source: str
    trust: str  # human | rule_derived

    def key(self) -> tuple[int, int]:
        return (self.a_id, self.b_id)


# --------------------------------------------------------------------------------------- read-only access
class RefusedDatabase(RuntimeError):
    pass


def open_readonly_session(db_path: str | Path) -> Session:
    """Read-only SQLAlchemy session on a SNAPSHOT COPY. Refuses the live application DB: experiment on `cli backup` output."""
    p = Path(db_path).resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    live = config.DB_URL.replace("sqlite:///", "")
    if live and Path(live).resolve() == p:
        raise RefusedDatabase(f"{p} is the application database; run on a snapshot copy (python -m watchtower.cli backup)")
    engine = create_engine(f"sqlite:///file:{p}?mode=ro&uri=true")
    return Session(engine)


def load_live_listings(s: Session) -> list[Listing]:
    """LIVE-source listings only (Source.kind == 'live'); demo/sandbox rows never become evidence."""
    return list(s.scalars(select(Listing).join(Source, Source.id == Listing.source_id)
                          .where(Source.kind == "live").order_by(Listing.id)))


def load_candidate_decisions(s: Session, live_ids: set[int]) -> list[tuple[int, int, str]]:
    """(listing_id, product_id, status) for HUMAN-decided review candidates (accepted/rejected) on live listings."""
    rows = s.execute(select(MatchCandidate.listing_id, MatchCandidate.product_id, MatchCandidate.status)
                     .where(MatchCandidate.status.in_(("accepted", "rejected")))).all()
    return [(l, p, st) for l, p, st in rows if l in live_ids]


def load_reviewed_labels(path: str | Path | None, listings) -> list[LabelledPair]:
    """Optional human labels: JSON list of {"a": "<source_key>:<external_id>", "b": "...", "label": 0|1, "reviewer": "..."}.
    Needs `listings` rows carrying `.source.key`. Unknown refs are skipped. Missing file -> no labels."""
    if not path or not Path(path).exists():
        return []
    ref = {f"{l.source.key}:{l.external_id}": l.id for l in listings}
    out = []
    for row in json.loads(Path(path).read_text()):
        a, b = ref.get(row.get("a")), ref.get(row.get("b"))
        if a and b and a != b and row.get("label") in (0, 1) and row.get("reviewer"):
            a, b = sorted((a, b))
            out.append(LabelledPair(a, b, int(row["label"]), "reviewed_override", "human"))
    return out


# --------------------------------------------------------------------------------------- grouping
def group_of_listing(l) -> str:
    return f"p{l.product_id}" if getattr(l, "product_id", None) else f"l{l.id}"


# --------------------------------------------------------------------------------------- building
def build_labelled_pairs(listings, decisions=(), reviewed=()) -> tuple[list[LabelledPair], dict]:
    """Return (pairs, stats). Pure given its inputs. `listings` must already be live-only (asserted via `source_kind` if present)."""
    rows = {l.id: P.view(l) for l in listings}
    for l in listings:
        kind = getattr(getattr(l, "source", None), "kind", "live")
        if kind != "live":
            raise ValueError(f"non-live listing {l.id} (source kind {kind!r}) cannot be evidence")
    in_review = {i for i, r in rows.items() if r.match_status == "review"}
    members: dict[int, list[int]] = {}
    for i, r in rows.items():
        if r.product_id:
            members.setdefault(r.product_id, []).append(i)
    multi = {p for p, ids in members.items() if len({rows[i].source_id for i in ids}) >= 2}
    found: dict[tuple[int, int], list[LabelledPair]] = {}
    stats = {"excluded_review_state": len(in_review), "unlabelled_candidates": 0, "conflicting_dropped": 0}

    def add(a, b, label, src, trust):
        a, b = sorted((a, b))
        found.setdefault((a, b), []).append(LabelledPair(a, b, label, src, trust))

    for p, ids in sorted(members.items()):  # positives: cross-merchant listings of one product
        ids = sorted(i for i in ids if i not in in_review)
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                a, b = ids[x], ids[y]
                if rows[a].source_id == rows[b].source_id:
                    continue
                st = {rows[a].match_status, rows[b].match_status}
                if "confirmed" in st:
                    add(a, b, 1, "human_confirmed", "human")
                elif "auto" in st:
                    add(a, b, 1, "rule_auto", "rule_derived")
    for lid, pid, status in decisions:  # human review decisions: candidate listing vs the product's other-merchant members
        if lid not in rows:
            continue
        for m in members.get(pid, []):
            if m != lid and rows[m].source_id != rows[lid].source_id:
                add(lid, m, 1 if status == "accepted" else 0, f"human_{status}", "human")
    for r in reviewed:
        add(r.a_id, r.b_id, r.label, r.label_source, r.trust)

    for a, b in P.candidate_pairs(listings):  # negatives among plausible confusers (blocked candidates)
        if (a, b) in found or a in in_review or b in in_review:
            continue
        ra, rb = rows[a], rows[b]
        if ra.product_id and ra.product_id == rb.product_id:
            continue
        f = P.pair_features(ra, rb)
        if ra.product_id in multi and rb.product_id in multi:
            add(a, b, 0, "distinct_cross_merchant_products", "rule_derived")
        elif f["hard_conflict"] or f["brand_conflict"] or f["category_conflict"]:
            add(a, b, 0, "rule_veto", "rule_derived")
        else:
            stats["unlabelled_candidates"] += 1

    out: list[LabelledPair] = []
    for key in sorted(found):
        cands = found[key]
        best = max(TRUST_ORDER[c.trust] for c in cands)
        top = [c for c in cands if TRUST_ORDER[c.trust] == best]
        if len({c.label for c in top}) > 1:
            stats["conflicting_dropped"] += 1
            continue
        out.append(top[0])
    return out, stats


def summarize(pairs: list[LabelledPair]) -> dict:
    by: dict[str, int] = {}
    for p in pairs:
        by[f"{p.label_source}:{p.label}"] = by.get(f"{p.label_source}:{p.label}", 0) + 1
    return {
        "n_pairs": len(pairs),
        "positives_human": sum(p.label == 1 and p.trust == "human" for p in pairs),
        "negatives_human": sum(p.label == 0 and p.trust == "human" for p in pairs),
        "positives_rule_derived": sum(p.label == 1 and p.trust == "rule_derived" for p in pairs),
        "negatives_rule_derived": sum(p.label == 0 and p.trust == "rule_derived" for p in pairs),
        "by_label_source": dict(sorted(by.items())),
    }


# --------------------------------------------------------------------------------------- group splits
class LeakageError(AssertionError):
    pass


def _pair_groups(p: LabelledPair, group_of: dict[int, str]) -> tuple[str, str]:
    return group_of[p.a_id], group_of[p.b_id]


def assert_no_group_leak(train: list[LabelledPair], test: list[LabelledPair], group_of: dict[int, str]) -> None:
    gtr = {g for p in train for g in _pair_groups(p, group_of)}
    gte = {g for p in test for g in _pair_groups(p, group_of)}
    ltr = {i for p in train for i in (p.a_id, p.b_id)}
    lte = {i for p in test for i in (p.a_id, p.b_id)}
    if gtr & gte or ltr & lte:
        raise LeakageError(f"train/test share groups {sorted(gtr & gte)[:5]} or listings {sorted(ltr & lte)[:5]}")


def group_folds(pairs: list[LabelledPair], group_of: dict[int, str], k: int, seed: int) -> list[tuple[list[LabelledPair], list[LabelledPair], int]]:
    """k folds over GROUPS. For each fold: (train_pairs, test_pairs, n_dropped_straddling). Deterministic for a given seed.
    Only groups that occur in `pairs` are split. A pair is kept only if both of its groups are on the same side."""
    groups = sorted({g for p in pairs for g in _pair_groups(p, group_of)})
    rng = random.Random(seed)
    rng.shuffle(groups)
    k = max(2, min(k, len(groups))) if len(groups) >= 2 else 1
    fold_of = {g: i % k for i, g in enumerate(groups)}
    folds = []
    for f in range(k):
        train, test, dropped = [], [], 0
        for p in pairs:
            ga, gb = _pair_groups(p, group_of)
            fa, fb = fold_of[ga], fold_of[gb]
            if fa == f and fb == f:
                test.append(p)
            elif fa != f and fb != f:
                train.append(p)
            else:
                dropped += 1
        if k > 1:
            assert_no_group_leak(train, test, group_of)
        folds.append((train, test, dropped))
    return folds


def group_split(pairs: list[LabelledPair], group_of: dict[int, str], test_fraction: float = 0.3, seed: int = 0):
    """Single train/test split by group. Returns (train, test, n_dropped)."""
    groups = sorted({g for p in pairs for g in _pair_groups(p, group_of)})
    rng = random.Random(seed)
    rng.shuffle(groups)
    n_test = max(1, math.ceil(test_fraction * len(groups))) if len(groups) >= 2 else 0
    test_g = set(groups[:n_test])
    train, test, dropped = [], [], 0
    for p in pairs:
        ga, gb = _pair_groups(p, group_of)
        if ga in test_g and gb in test_g:
            test.append(p)
        elif ga not in test_g and gb not in test_g:
            train.append(p)
        else:
            dropped += 1
    assert_no_group_leak(train, test, group_of)
    return train, test, dropped


def pairs_to_dicts(pairs):
    return [asdict(p) for p in pairs]
