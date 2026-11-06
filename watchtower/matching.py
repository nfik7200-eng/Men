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

from .identifiers import clean_mpn, normalise_gtin
from .specs import canon_brand

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


_STOP = {"the", "a", "an", "and", "with", "for", "of", "to", "in", "on", "by", "official", "genuine", "original", "new",
         "ram", "memory", "board", "development", "module", "version", "edition"}
_GENERIC_CATEGORIES = {None, "", "maker-electronics"}


def name_signature(title: str, brand: str | None) -> frozenset[str]:
    """Normalised bag of distinguishing words: units glued ('4 GB' == '4GB'), brand and filler removed."""
    t = re.sub(r"(\d+)\s*(gb|tb|mb|ghz|mhz)\b", r"\1\2", title.lower())
    toks = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", t)
    drop = set(re.findall(r"[a-z0-9]+", canon_brand(brand))) | _STOP
    # spec-like tokens (4gb, 15.6, 5v) are compared via Listing.specs, not as part of the model name
    spec_like = re.compile(r"^\d+(\.\d+)?(gb|tb|mb|ghz|mhz|in|inch|mah|v|a)$|^\d+\.\d+$")
    return frozenset(x for x in toks if x not in drop and not spec_like.match(x))


def gtin_of(l: Listing) -> str | None:
    return l.gtin or normalise_gtin((l.identifiers or {}).get("gtin") or (l.identifiers or {}).get("upc") or (l.identifiers or {}).get("ean"))


def mpn_of(l: Listing) -> str | None:
    m = clean_mpn(l.mpn or (l.identifiers or {}).get("mpn"), l.sku)
    return re.sub(r"[^0-9a-z]", "", m.lower()) if m else None


def hard_conflict(a: Listing, b: Listing) -> str | None:
    """Differences that make two listings materially different products/offers. Checked even when identifiers agree,
    because merchants copy identifiers across variants."""
    for label, fa, fb in (("variant", variant_set(a.title), variant_set(b.title)),
                          ("condition", {condition_of(a)}, {condition_of(b)}),
                          ("bundle", {bool(_BUNDLE.search(a.title.lower()))}, {bool(_BUNDLE.search(b.title.lower()))}),
                          ("generation", generation_set(a.title), generation_set(b.title))):
        if fa != fb:
            return f"{label} differs ({sorted(fa, key=str)} vs {sorted(fb, key=str)})"
    ca, cb = colour_set(a.title), colour_set(b.title)
    if ca and cb and ca != cb:
        return f"colour differs ({sorted(ca)} vs {sorted(cb)})"
    conflicts = []
    for key, tol in (("screen_in", 0.15), ("ram_gb", 0), ("storage_gb", 0), ("cpu_family", None)):
        va, vb = a.specs.get(key), b.specs.get(key)
        if va is None or vb is None:
            continue
        if not ((va == vb) if tol in (None, 0) else abs(va - vb) <= tol):
            conflicts.append(f"{key}: {va} vs {vb}")
    return ("spec conflict: " + "; ".join(conflicts)) if conflicts else None


def match_score(a: Listing, b: Listing) -> MatchResult:
    """Staged: 1 GTIN -> 2 MPN(+brand) -> 3 brand+model/name signature -> 4 specs -> 5 fuzzy title (supporting only).
    Hard conflicts veto at every stage. Never uses the merchant SKU."""
    ba, bb = canon_brand(a.brand), canon_brand(b.brand)
    if a.category and b.category and a.category != b.category and a.category not in _GENERIC_CATEGORIES and b.category not in _GENERIC_CATEGORIES:
        return MatchResult(0.0, "none", [f"category differs ({a.category} vs {b.category})"])
    if ba and bb and ba != bb:
        return MatchResult(0.0, "none", [f"brand differs ({a.brand} vs {b.brand})"])
    veto = hard_conflict(a, b)

    ga, gb = gtin_of(a), gtin_of(b)
    if ga and ga == gb:  # stage 1
        if veto:  # same barcode but materially different listings: suspicious data, a human decides
            return MatchResult(0.6, "review", [f"shared GTIN {ga} but {veto}"])
        return MatchResult(1.0, "auto", [f"shared GTIN {ga}"])
    ma, mb = mpn_of(a), mpn_of(b)
    if ma and ma == mb and ba and ba == bb:  # stage 2
        if veto:
            return MatchResult(0.2, "none", [f"shared MPN {ma} but {veto} (merchants copy MPNs across variants)"])
        return MatchResult(0.98, "auto", [f"shared MPN {ma} (same brand)"])
    if veto:
        return MatchResult(0.15 if not veto.startswith("spec") else 0.2, "none", [veto])

    reasons: list[str] = []
    sa, sb = name_signature(a.title, a.brand), name_signature(b.title, b.brand)
    if ba and ba == bb and sa and sa == sb and len(sa) >= 2:  # stage 3: brand + identical normalised model name
        agree_specs = sum(1 for k in ("screen_in", "ram_gb", "storage_gb", "cpu_family")
                          if a.specs.get(k) is not None and a.specs.get(k) == b.specs.get(k))
        return MatchResult(0.95, "auto", [f"same brand {a.brand} and identical normalised name {sorted(sa)}",
                                          f"{agree_specs} agreeing spec(s); no shared GTIN/MPN published"])
    ma_t, mb_t = model_tokens(a.title), model_tokens(b.title)
    shares_code = bool(ma_t and mb_t and len(ma_t & mb_t) / len(ma_t | mb_t) >= 0.5)
    if ba and ba == bb and sa and sb and sa != sb and not shares_code:
        only_a, only_b = sa - sb, sb - sa
        if any(t.isdigit() for t in only_a | only_b):  # 'Zero W' vs 'Zero 2 W', 'Pi 4' vs 'Pi 5': different model/generation
            return MatchResult(0.2, "none", [f"model numbers differ: only in A {sorted(only_a)}, only in B {sorted(only_b)}"])
        code = lambda t: bool(re.search(r"[a-z]", t) and re.search(r"\d", t))
        ca, cb = {t for t in only_a if code(t)}, {t for t in only_b if code(t)}
        if ca and cb:
            return MatchResult(0.2, "none", [f"model codes differ: {sorted(ca)} vs {sorted(cb)}"])
        if ca or cb:
            return MatchResult(0.6, "review", [f"a model code appears on one side only ({sorted(ca | cb)}): same product?"])
        if (only_a == set() or only_b == set()) and len(sa & sb) >= 1:  # one name is the other plus descriptive words
            return MatchResult(0.6, "review", [f"one name extends the other by {sorted(only_a | only_b)}: variant or same product?"])

    jacc = (len(ma_t & mb_t) / len(ma_t | mb_t)) if ma_t and mb_t else None
    agree = sum(1 for k in ("screen_in", "ram_gb", "storage_gb", "cpu_family")
                if a.specs.get(k) is not None and a.specs.get(k) == b.specs.get(k))
    if jacc == 0:
        return MatchResult(0.25, "none", [f"model codes disagree ({sorted(ma_t)} vs {sorted(mb_t)})"])
    ratio = SequenceMatcher(None, " ".join(sorted(_sim_tokens(a.title))), " ".join(sorted(_sim_tokens(b.title)))).ratio()
    score = 0.5 * (jacc or 0.0) + 0.2 * ratio + 0.3 * min(agree, 3) / 3  # stage 5 fuzzy: supporting evidence only
    if jacc is not None:
        reasons.append(f"model-code overlap {jacc:.2f} ({sorted(ma_t & mb_t)})")
    reasons += [f"title similarity {ratio:.2f}", f"{agree} agreeing spec(s), 0 conflicts"]
    if ba and ba == bb:
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
        if not s.scalar(select(MatchCandidate.id).where(MatchCandidate.listing_id == listing.id,
                                                        MatchCandidate.product_id == other.product_id)):
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


def rematch_all(s: Session) -> dict:
    """Re-run matching for every listing not yet matched or confirmed (e.g. after matcher/identifier changes).
    Rejected candidates are never re-queued. Returns counts."""
    out = {"checked": 0, "auto": 0, "review": 0}
    rejected = {(c.listing_id, c.product_id) for c in s.scalars(select(MatchCandidate).where(MatchCandidate.status == "rejected"))}
    for l in s.scalars(select(Listing).where(Listing.match_status.in_(("self", "review")), Listing.active.is_(True)).order_by(Listing.id)).all():
        out["checked"] += 1
        before = l.product_id
        r = attach_or_queue(s, l)
        if r and r.decision == "auto":
            out["auto"] += 1
        elif r and r.decision == "review":
            out["review"] += 1
        _ = before, rejected
    return out
