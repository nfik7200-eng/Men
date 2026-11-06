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


# --- hard-conflict features: any difference means "different product/offer", never an auto-merge ----------------
VARIANT_WORDS = {"ultra", "pro", "plus", "max", "mini", "lite", "fe", "se", "air", "xl", "edge", "fold", "flip",
                 "neo", "turbo", "note", "prime", "classic", "premium", "slim"}
COLOURS = {"black", "white", "silver", "gray", "grey", "blue", "red", "green", "gold", "rose", "pink", "purple",
           "yellow", "orange", "bronze", "navy", "graphite", "midnight", "starlight", "titanium", "lavender", "beige"}
_USED = re.compile(r"refurb|renewed|reconditioned|pre-?owned|\bused\b|open[- ]box|for parts|like new|\bb-?grade\b|\bgood condition\b")
_BUNDLE = re.compile(r"bundle|combo|\bkit\b|\bpack of \d|\b\d+[- ]?pack\b|\b(?:set|lot) of\b|"
                     r"\bwith (?:free )?\w+ (?:bag|mouse|case|charger|cover|keyboard|headphones?|earbuds)|"
                     r"\+\s*(?:\w+\s+){0,2}(?:bag|mouse|case|charger|cover|keyboard|stand|dock|headphones?|earbuds|sleeve)")
_GEN = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)[ -]?gen\b|\bgen(?:eration)?[ -]?(\d{1,2})\b")


def _words(title: str) -> set[str]:
    return set(re.findall(r"[a-z]+", title.lower()))


def variant_set(title: str) -> set[str]:
    v = _words(title) & VARIANT_WORDS
    if re.search(r"[a-z0-9]\+(?![a-z0-9])", title.lower()):
        v.add("plus")
    return v


def condition_of(l: Listing) -> str:
    c = (getattr(l, "condition", None) or "").lower()
    if c:
        return "new" if c == "new" else "used"
    return "used" if _USED.search(l.title.lower()) else "new"


def colour_set(title: str) -> set[str]:
    return _words(title) & COLOURS


def generation_set(title: str) -> set[str]:
    return {a or b for a, b in _GEN.findall(title.lower())}


def full_codes(title: str) -> set[str]:
    """Whole hyphenated model codes ('a315-51-31gk'), used to detect suffix/region variants."""
    return {t for t in _tokens(title) if "-" in t and re.search(r"[a-z]", t) and re.search(r"\d", t) and len(t) >= 6}


def _strong_id(a: Listing, b: Listing) -> tuple[str, str] | None:
    for k in _ID_KEYS:
        va, vb = a.identifiers.get(k), b.identifiers.get(k)
        if va and vb and len(_norm_id(va)) >= 8 and _norm_id(va) == _norm_id(vb):
            return k, str(va)
    return None


def match_score(a: Listing, b: Listing) -> MatchResult:
    # 1. strong, validated identifiers (GTIN-length); a shared GTIN names the same product
    sid = _strong_id(a, b)
    if sid:
        return MatchResult(1.0, "auto", [f"shared {sid[0]}: {sid[1]}"])
    brand_a, brand_b = (a.brand or "").lower(), (b.brand or "").lower()
    # 2. hard conflicts first (category/brand) — they also veto MPN matches
    if a.category and b.category and a.category != b.category:
        return MatchResult(0.0, "none", [f"category differs ({a.category} vs {b.category})"])
    if brand_a and brand_b and brand_a != brand_b:
        return MatchResult(0.0, "none", [f"brand differs ({a.brand} vs {b.brand})"])
    ma_id, mb_id = a.identifiers.get("mpn"), b.identifiers.get("mpn")
    if ma_id and mb_id and len(_norm_id(ma_id)) >= 5 and _norm_id(ma_id) == _norm_id(mb_id) and brand_a and brand_a == brand_b:
        return MatchResult(0.98, "auto", [f"shared MPN {ma_id} (same brand)"])

    for label, fa, fb in (("variant", variant_set(a.title), variant_set(b.title)),
                          ("condition", {condition_of(a)}, {condition_of(b)}),
                          ("bundle", {bool(_BUNDLE.search(a.title.lower()))}, {bool(_BUNDLE.search(b.title.lower()))}),
                          ("generation", generation_set(a.title), generation_set(b.title))):
        if fa != fb:
            return MatchResult(0.15, "none", [f"{label} differs ({sorted(fa, key=str)} vs {sorted(fb, key=str)})"])
    ca, cb = colour_set(a.title), colour_set(b.title)
    if ca and cb and ca != cb:
        return MatchResult(0.15, "none", [f"colour differs ({sorted(ca)} vs {sorted(cb)})"])

    reasons: list[str] = []
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
    cap_review = False
    fa, fb = full_codes(a.title), full_codes(b.title)
    if fa and fb and not (fa & fb) and any(x.startswith(y + "-") or y.startswith(x + "-") for x in fa for y in fb):
        cap_review = True  # same code with an extra suffix (region/SKU variant): a human decides
        reasons.append("model code differs only by a suffix (region/SKU variant?)")
    if score >= AUTO_THRESHOLD and (jacc or 0) >= 0.5 and agree >= 2 and not cap_review:
        return MatchResult(round(score, 3), "auto", reasons)
    if score >= REVIEW_THRESHOLD and ((jacc or 0) > 0 or ratio >= 0.8):
        return MatchResult(round(score, 3), "review", reasons)
    if cap_review and jacc:
        return MatchResult(round(max(score, REVIEW_THRESHOLD), 3), "review", reasons)
    return MatchResult(round(score, 3), "none", reasons)


def attach_or_queue(s: Session, listing: Listing) -> MatchResult | None:
    """Compare a new listing to listings of OTHER sources. Auto-attach, queue for review, or leave alone."""
    q = select(Listing).where(
        Listing.source_id != listing.source_id, Listing.active.is_(True),
        Listing.product_id.is_not(None), Listing.product_id != listing.product_id)
    if listing.category:
        q = q.where((Listing.category == listing.category) | Listing.category.is_(None))
    pool = s.scalars(q).all()
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
