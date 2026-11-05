"""Cross-listing product matching with explicit confidence. Never merges on weak evidence.

Order of evidence:
  1. Shared strong identifier (GTIN/UPC/EAN/ISBN, or MPN with compatible brand)  -> auto, 1.0
  2. Brand + model tokens + spec agreement (no conflicts)                         -> auto if strong
  3. Weaker partial agreement                                                      -> review queue
  4. Any hard conflict (brand/category/spec/model)                                 -> no match
"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Listing, MatchCandidate, Product

AUTO_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.55
_SPEC_NOISE = re.compile(r"^\d+(\.\d+)?(gb|tb|mb|ghz|mhz|inch|in|hz|mah|mp|w|g)$")
_ID_KEYS = ("gtin", "upc", "ean", "isbn")


@dataclass
class MatchResult:
    score: float
    decision: str  # auto | review | none
    reasons: list[str] = field(default_factory=list)


def _tokens(title: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[-./][a-z0-9]+)*", title.lower())


def _sim_tokens(title: str) -> list[str]:
    """Hyphen/slash-insensitive tokens for fuzzy similarity ('X441NA-GA190' == 'X441NA GA190')."""
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", title.lower())


def model_tokens(title: str) -> set[str]:
    """Alphanumeric model codes such as 'x441na-ga190' -> {'x441na','ga190'}; spec noise removed."""
    out: set[str] = set()
    for t in _tokens(title):
        if _SPEC_NOISE.match(t):
            continue
        for part in re.split(r"[-/]", t):
            if len(part) >= 3 and re.search(r"[a-z]", part) and re.search(r"\d", part) and not _SPEC_NOISE.match(part):
                out.add(part)
    return out


def _norm_id(v) -> str:
    return re.sub(r"[^0-9a-z]", "", str(v).lower())


def match_score(a: Listing, b: Listing) -> MatchResult:
    # 1. strong identifiers
    for k in _ID_KEYS:
        if a.identifiers.get(k) and b.identifiers.get(k) and _norm_id(a.identifiers[k]) == _norm_id(b.identifiers[k]):
            return MatchResult(1.0, "auto", [f"shared {k}: {a.identifiers[k]}"])
    brand_a, brand_b = (a.brand or "").lower(), (b.brand or "").lower()
    if a.identifiers.get("mpn") and b.identifiers.get("mpn") and \
            _norm_id(a.identifiers["mpn"]) == _norm_id(b.identifiers["mpn"]) and (not brand_a or not brand_b or brand_a == brand_b):
        return MatchResult(0.98, "auto", [f"shared MPN {a.identifiers['mpn']} (brand compatible)"])

    reasons: list[str] = []
    if a.category and b.category and a.category != b.category:
        return MatchResult(0.0, "none", [f"category differs ({a.category} vs {b.category})"])
    if brand_a and brand_b and brand_a != brand_b:
        return MatchResult(0.0, "none", [f"brand differs ({a.brand} vs {b.brand})"])

    ma, mb = model_tokens(a.title), model_tokens(b.title)
    jacc = (len(ma & mb) / len(ma | mb)) if ma and mb else None
    agree, conflicts = 0, []
    for key, tol in (("screen_in", 0.15), ("ram_gb", 0), ("storage_gb", 0), ("cpu_family", None)):
        va, vb = a.specs.get(key), b.specs.get(key)
        if va is None or vb is None:
            continue
        same = (va == vb) if tol in (None, 0) else abs(va - vb) <= tol
        if same:
            agree += 1
        else:
            conflicts.append(f"{key}: {va} vs {vb}")
    if conflicts:
        return MatchResult(0.2, "none", ["spec conflict: " + "; ".join(conflicts)])
    if jacc == 0:
        return MatchResult(0.25, "none", [f"model codes disagree ({sorted(ma)} vs {sorted(mb)})"])

    ratio = SequenceMatcher(None, " ".join(sorted(_sim_tokens(a.title))), " ".join(sorted(_sim_tokens(b.title)))).ratio()
    score = 0.5 * (jacc or 0.0) + 0.2 * ratio + 0.3 * min(agree, 3) / 3
    if jacc is not None:
        reasons.append(f"model-code overlap {jacc:.2f} ({sorted(ma & mb)})")
    reasons += [f"title similarity {ratio:.2f}", f"{agree} agreeing spec(s), 0 conflicts"]
    if brand_a and brand_a == brand_b:
        reasons.append(f"same brand {a.brand}")
    if score >= AUTO_THRESHOLD and (jacc or 0) >= 0.5 and agree >= 2:
        return MatchResult(round(score, 3), "auto", reasons)
    if score >= REVIEW_THRESHOLD and ((jacc or 0) > 0 or ratio >= 0.8):
        return MatchResult(round(score, 3), "review", reasons)
    return MatchResult(round(score, 3), "none", reasons)


def attach_or_queue(s: Session, listing: Listing) -> MatchResult | None:
    """Compare a new listing to listings of OTHER sources. Auto-attach, queue for review, or leave alone."""
    pool = s.scalars(select(Listing).where(
        Listing.source_id != listing.source_id, Listing.active.is_(True),
        Listing.product_id.is_not(None), Listing.product_id != listing.product_id)).all()
    best: tuple[MatchResult, Listing] | None = None
    for other in pool:
        r = match_score(listing, other)
        if r.decision != "none" and (best is None or r.score > best[0].score):
            best = (r, other)
    if not best:
        return None
    r, other = best
    if r.decision == "auto":
        old = listing.product_id
        listing.product_id = other.product_id
        listing.match_status, listing.match_confidence, listing.match_reasons = "auto", r.score, r.reasons
        if old and not s.scalar(select(Listing.id).where(Listing.product_id == old, Listing.id != listing.id)):
            p = s.get(Product, old)
            if p:
                s.delete(p)
    else:
        listing.match_status, listing.match_confidence, listing.match_reasons = "review", r.score, r.reasons
        s.add(MatchCandidate(listing_id=listing.id, product_id=other.product_id, score=r.score, reasons=r.reasons))
    return r


def resolve_candidate(s: Session, cand_id: int, accept: bool) -> None:
    """Human review decision. Accepting merges the listing into the candidate product."""
    c = s.get(MatchCandidate, cand_id)
    if not c or c.status != "pending":
        return
    c.status = "accepted" if accept else "rejected"
    l = c.listing
    if accept:
        old = l.product_id
        l.product_id = c.product_id
        l.match_status = "confirmed"
        if old and not s.scalar(select(Listing.id).where(Listing.product_id == old, Listing.id != l.id)):
            p = s.get(Product, old)
            if p:
                s.delete(p)
    else:
        l.match_status = "self"
