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

from .models import Listing, MatchCandidate, Product, Source

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
                 "neo", "turbo", "note", "prime", "classic", "premium", "slim",
                 "wifi", "wireless", "bluetooth", "no", "non", "headers", "header", "poe", "lte"}  # hardware options change the product
# Product-type words: an accessory/part named in only one title ('Pi 5 Camera Cable', 'Pi 5 Active Cooler', 'Compute Module 5')
# is a different product from the bare board even when the model name is a prefix of it.
TYPE_WORDS = {"cable", "case", "cooler", "fan", "heatsink", "battery", "bumper", "baseplate", "adapter", "adaptor", "charger", "supply",
              "psu", "cover", "stand", "mount", "enclosure", "camera", "display", "touchscreen", "screen", "compute",
              "hat", "shield", "breakout", "wire", "connector", "sensor", "antenna", "holder", "sleeve", "rtc"}
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


_ALIASES = [(r"\brev(?:ision)?\.?\s*(\d)", r"r\1"), (r"\bzero\s*2\s*w(h?)\b", r"zero 2 w\1"), (r"\bpicow(h?)\b", r"pico w\1")]
_UNIT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?:\s*(gb|tb|mb|ghz|mhz|mah|inch|in|mm|cm)(?![a-z0-9])|(v|a|w)(?![a-z0-9]))")


def _norm_title(title: str) -> str:
    t = title.lower()
    for pat, rep in _ALIASES:
        t = re.sub(pat, rep, t)
    return t


def unit_values(title: str) -> dict[str, set[float]]:
    """Numbers carrying units (capacity, volts, amps, watts, GHz, mAh, inches, mm) and bare version numbers (1.3, 2.0),
    keyed by kind. These distinguish products ('32GB' vs '64GB', '3A' vs '5A', 'Camera 1.3' vs '2.1')."""
    t, out = title.lower(), {}
    for num, unit1, unit2 in _UNIT.findall(t):
        unit, v = unit1 or unit2, float(num)
        key = {"in": "in", "inch": "in", "tb": "cap", "gb": "cap"}.get(unit, unit)
        out.setdefault(key, set()).add(round(v * 1024 if unit == "tb" else v, 3))
    for m in re.finditer(r"(?<![\w.])(\d+\.\d+)(?![\w.])", _UNIT.sub(" ", t)):
        out.setdefault("ver", set()).add(round(float(m.group(1)), 3))
    return out


def unit_conflict(a: str, b: str) -> str | None:
    ua, ub = unit_values(a), unit_values(b)
    for k in ua.keys() & ub.keys():
        if not (ua[k] <= ub[k] or ub[k] <= ua[k]):  # neither states a subset of the other: different products
            return f"{k} differs ({sorted(ua[k])} vs {sorted(ub[k])})"
    return None


def name_signature(title: str, brand: str | None) -> frozenset[str]:
    """Normalised bag of distinguishing words: units glued ('4 GB' == '4GB'), brand and filler removed."""
    t = re.sub(r"(\d+)\s*(gb|tb|mb|ghz|mhz)\b", r"\1\2", _norm_title(title))
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
                          ("product type", _words(a.title) & TYPE_WORDS, _words(b.title) & TYPE_WORDS),
                          ("condition", {condition_of(a)}, {condition_of(b)}),
                          ("bundle", {bool(_BUNDLE.search(a.title.lower()))}, {bool(_BUNDLE.search(b.title.lower()))}),
                          ("generation", generation_set(a.title), generation_set(b.title))):
        if fa != fb:
            return f"{label} differs ({sorted(fa, key=str)} vs {sorted(fb, key=str)})"
    ca, cb = colour_set(a.title), colour_set(b.title)
    if ca and cb and ca != cb:
        return f"colour differs ({sorted(ca)} vs {sorted(cb)})"
    uc = unit_conflict(a.title, b.title)
    if uc:
        return f"spec value {uc}"
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
    agree_specs = sum(1 for k in ("screen_in", "ram_gb", "storage_gb", "cpu_family")
                      if a.specs.get(k) is not None and a.specs.get(k) == b.specs.get(k))
    agree_units = sum(1 for k in unit_values(a.title).keys() & unit_values(b.title).keys())
    if ba and ba == bb and sa and sa == sb:  # stage 3: brand + identical normalised model name
        if len(sa) >= 2 or agree_specs + agree_units >= 1:
            return MatchResult(0.95, "auto", [f"same brand {a.brand} and identical normalised name {sorted(sa)}",
                                              f"{agree_specs + agree_units} agreeing spec value(s); no shared GTIN/MPN published"])
        return MatchResult(0.6, "review", [f"identical one-word name {sorted(sa)} but nothing else agrees (RAM/size missing?)"])
    if (not ba or not bb) and sa and sa == sb and len(sa) >= 2:
        return MatchResult(0.55, "review", [f"identical names {sorted(sa)} but the brand is missing on one side"])

    ma_t, mb_t = model_tokens(a.title), model_tokens(b.title)
    shares_code = bool(ma_t and mb_t and len(ma_t & mb_t) / len(ma_t | mb_t) >= 0.5)
    overlap = len(sa & sb)
    related = bool(sa and sb) and overlap >= 1 and overlap / min(len(sa), len(sb)) >= 0.5  # names must really share words
    if ba and ba == bb and related and sa != sb and not shares_code:
        only_a, only_b = sa - sb, sb - sa
        if any(t.isdigit() for t in only_a | only_b):  # 'Zero W' vs 'Zero 2 W', 'Pi 4' vs 'Pi 5': different model/generation
            return MatchResult(0.2, "none", [f"model numbers differ: only in A {sorted(only_a)}, only in B {sorted(only_b)}"])
        short = lambda ts: {t for t in ts if len(t) <= 2 and t.isalpha()}
        if short(only_a) and short(only_b):  # 'A+' vs 'B+', 'H' vs 'W': different variant letters
            return MatchResult(0.2, "none", [f"variant letters differ: {sorted(short(only_a))} vs {sorted(short(only_b))}"])
        code = lambda t: bool(re.search(r"[a-z]", t) and re.search(r"\d", t))
        ca, cb = {t for t in only_a if code(t)}, {t for t in only_b if code(t)}
        if ca and cb:
            return MatchResult(0.2, "none", [f"model codes differ: {sorted(ca)} vs {sorted(cb)}"])
        if ca or cb:
            return MatchResult(0.6, "review", [f"a model code appears on one side only ({sorted(ca | cb)}): same product?"])
        if (only_a == set() or only_b == set()) and overlap >= 1:  # one name is the other plus descriptive words
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
    if score >= AUTO_THRESHOLD and (jacc or 0) >= 0.5 and agree >= 2 and not cap_review and ba and ba == bb:
        return MatchResult(round(score, 3), "auto", reasons)
    if score >= REVIEW_THRESHOLD and ((jacc or 0) > 0 or ratio >= 0.8):
        return MatchResult(round(score, 3), "review", reasons)
    if cap_review and jacc:
        return MatchResult(round(max(score, REVIEW_THRESHOLD), 3), "review", reasons)
    return MatchResult(round(score, 3), "none", reasons)


def _drop_product_if_empty(s: Session, pid: int | None, keep_listing_id: int) -> None:
    """Delete a product left with no listings, FIRST closing any pending candidates that point at it (otherwise the
    foreign key breaks the commit — reproduced by the review)."""
    if pid is None or s.scalar(select(Listing.id).where(Listing.product_id == pid, Listing.id != keep_listing_id)):
        return
    for c in s.scalars(select(MatchCandidate).where(MatchCandidate.product_id == pid)).all():
        s.delete(c)
    p = s.get(Product, pid)
    if p:
        s.delete(p)


def _members(s: Session, pid: int) -> list[Listing]:
    return list(s.scalars(select(Listing).where(Listing.product_id == pid, Listing.active.is_(True))))


def _reverse_pending(s: Session, listing: Listing, product_id: int) -> list[MatchCandidate]:
    """Pending candidates asking the SAME question from the other side: a member of `product_id` proposed for the
    listing's own product. (l -> P) and (m -> l's product) with m in P are one human decision, not two."""
    return list(s.scalars(select(MatchCandidate).join(Listing, Listing.id == MatchCandidate.listing_id).where(
        MatchCandidate.status == "pending", MatchCandidate.product_id == listing.product_id,
        Listing.product_id == product_id, MatchCandidate.listing_id != listing.id)))


def _under_review(s: Session, l: Listing) -> bool:
    """A listing awaits a decision if it has a pending candidate, or a pending candidate proposes a merge INTO its product."""
    return bool(s.scalar(select(MatchCandidate.id).where(MatchCandidate.status == "pending", MatchCandidate.listing_id == l.id))
                or s.scalar(select(MatchCandidate.id).where(MatchCandidate.status == "pending", MatchCandidate.product_id == l.product_id,
                                                            MatchCandidate.listing_id != l.id)))


def _queue(s: Session, listing: Listing, product_id: int, score: float, reasons: list[str]) -> None:
    if s.scalar(select(MatchCandidate.id).where(MatchCandidate.listing_id == listing.id, MatchCandidate.product_id == product_id)):
        return
    if _reverse_pending(s, listing, product_id):
        return  # the pair is already in the queue from the other side
    s.add(MatchCandidate(listing_id=listing.id, product_id=product_id, score=score, reasons=reasons))


def attach_or_queue(s: Session, listing: Listing) -> MatchResult | None:
    """Compare a listing to listings of OTHER sources. Auto-attach only when the evidence is unambiguous; otherwise queue
    for review. Guards: (1) a product holds at most ONE listing per merchant, (2) equally good candidates are
    ambiguous, (3) the listing must not conflict with ANY member of the target product (no transitive drift),
    (4) human rejections are never re-queued."""
    if listing.match_status == "confirmed":
        return None  # a human decision is never re-litigated by the matcher
    live = listing.source is not None and listing.source.kind == "live"  # demo/sandbox and live data never merge
    q = (select(Listing).join(Source, Source.id == Listing.source_id)
         .where(Listing.source_id != listing.source_id, Listing.active.is_(True), Listing.product_id.is_not(None),
                Listing.product_id != listing.product_id, (Source.kind == "live") if live else (Source.kind != "live")))
    if listing.category:
        q = q.where((Listing.category == listing.category) | Listing.category.is_(None))
    rejected = {(c.listing_id, c.product_id) for c in s.scalars(select(MatchCandidate).where(MatchCandidate.status == "rejected"))}
    scored: list[tuple[MatchResult, Listing]] = []
    for other in s.scalars(q).all():
        if (listing.id, other.product_id) in rejected or (other.id, listing.product_id) in rejected:
            continue
        r = match_score(listing, other)
        if r.decision != "none":
            scored.append((r, other))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0].score)
    r, other = scored[0]
    if r.decision == "auto":
        tied = [(rr, o) for rr, o in scored if rr.decision == "auto" and abs(rr.score - r.score) < 0.02 and o.product_id != other.product_id]
        problem = None
        if tied:
            problem = f"ambiguous: {len(tied) + 1} equally good candidates ({', '.join(sorted({o.title[:40] for _, o in [(r, other)] + tied}))})"
        else:
            members = _members(s, other.product_id)
            if any(m.source_id == listing.source_id and m.id != listing.id for m in members):
                problem = "the target product already has a listing from this merchant (identical names for different variants?)"
            else:
                bad = next((m for m in members if m.source_id != listing.source_id and match_score(listing, m).decision == "none"), None)
                if bad is not None:
                    problem = f"conflicts with another listing of the target product ('{bad.title[:50]}')"
        if problem:
            reasons = [problem] + r.reasons
            listing.match_status, listing.match_confidence, listing.match_reasons = "review", min(r.score, 0.6), reasons
            for rr, o in ([(r, other)] + tied):
                _queue(s, listing, o.product_id, min(rr.score, 0.6), reasons)
            return MatchResult(min(r.score, 0.6), "review", reasons)
        old = listing.product_id
        listing.product_id = other.product_id
        listing.match_status, listing.match_confidence, listing.match_reasons = "auto", r.score, r.reasons
        # keep BOTH sides consistent: the target listing now has a confirmed counterpart, so its own status/evidence
        # must not still show an older, different candidate (found in the UI: stale 'review' reasons beside an auto match)
        if other.match_status in ("self", "review"):
            other.match_status, other.match_confidence, other.match_reasons = "auto", r.score, r.reasons
            for c in s.scalars(select(MatchCandidate).where(MatchCandidate.listing_id == other.id, MatchCandidate.status == "pending",
                                                            MatchCandidate.product_id != other.product_id)).all():
                s.delete(c)
        _drop_product_if_empty(s, old, listing.id)
    else:
        listing.match_status, listing.match_confidence, listing.match_reasons = "review", r.score, r.reasons
        seen_products = set()
        for rr, o in scored:  # queue every near-tie (up to 3 products), not just the first
            if rr.decision == "review" and rr.score >= r.score - 0.05 and o.product_id not in seen_products and len(seen_products) < 3:
                seen_products.add(o.product_id)
                _queue(s, listing, o.product_id, rr.score, rr.reasons)
    return r


def resolve_candidate(s: Session, cand_id: int, accept: bool) -> None:
    """Human review decision. Accepting merges the listing into the candidate product (overriding the automatic guards).
    The decision also answers the mirrored question (a member of the target product proposed for this listing's
    product), so that candidate is closed the same way: a rejected pair can never be accepted from the other side."""
    c = s.get(MatchCandidate, cand_id)
    if not c or c.status != "pending":
        return
    l, target = c.listing, c.product_id
    mirrors = _reverse_pending(s, l, target)
    # everyone whose 'review' status may hinge on this question: the listing, the target product's members, and the
    # listings of mirrored candidates (collected BEFORE the move)
    asking_old = list(s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending",
                                                              MatchCandidate.product_id == l.product_id, MatchCandidate.id != c.id)))
    target_members = [x for x in _members(s, target) if x.id != l.id]
    affected = {x.id: x for x in [l] + target_members + [m.listing for m in mirrors + asking_old]}
    c.status = "accepted" if accept else "rejected"
    for m in mirrors:
        m.status = c.status
    if accept:
        old = l.product_id
        l.product_id, l.match_status = target, "confirmed"
        s.flush()
        _drop_product_if_empty(s, old, l.id)  # also closes other pending candidates pointing at the vanished product
    s.flush()
    if accept:  # a lone counterpart now shares a human-confirmed product
        for x in target_members:
            if x.match_status == "self" and x.source_id != l.source_id:
                x.match_status, x.match_reasons = "confirmed", []
    for x in affected.values():
        if x.match_status != "review":
            continue  # human-confirmed and automatic matches are never downgraded by an unrelated decision
        if _under_review(s, x):
            continue
        x.match_status = "confirmed" if accept and x.product_id == target else "self"
        if x.match_status == "self":
            x.match_reasons = []


def demote_ambiguous(s: Session, source_id: int) -> int:
    """If a merchant lists several items with the SAME normalised name (e.g. one title for cables of different lengths),
    no cross-merchant auto-match involving that name can be trusted: detach those listings into their own products and
    send the old pairing to review. Returns the number demoted."""
    rows = s.scalars(select(Listing).where(Listing.source_id == source_id, Listing.active.is_(True))).all()
    groups: dict[tuple, list[Listing]] = {}
    for l in rows:
        sig = name_signature(l.title, l.brand)
        if sig:  # unit values are part of identity here: '2 GB' and '4 GB' are different items, identical cable titles are not
            units = tuple(sorted((k, tuple(sorted(v))) for k, v in unit_values(l.title).items()))
            groups.setdefault((canon_brand(l.brand), sig, units), []).append(l)
    n = 0
    for (_, sig, _units), items in groups.items():
        if len(items) < 2:
            continue
        for l in items:
            if l.match_status != "auto":
                continue
            reasons = [f"this merchant lists {len(items)} identically named items {sorted(sig)}: cannot tell which one matches"] + (l.match_reasons or [])
            old = l.product_id
            newp = Product(title=l.title, brand=l.brand, category=l.category)
            s.add(newp)
            s.flush()
            l.product_id, l.match_status, l.match_reasons = newp.id, "review", reasons
            l.match_confidence = min(l.match_confidence or 0.6, 0.6)
            _queue(s, l, old, l.match_confidence, reasons)
            n += 1
    return n


def revalidate_auto(s: Session) -> int:
    """Re-check every existing automatic cross-merchant pairing against the CURRENT matcher; demote any that would no
    longer auto-merge (e.g. made by an older, weaker matcher). Returns the number demoted."""
    n = 0
    for l in s.scalars(select(Listing).where(Listing.match_status == "auto", Listing.active.is_(True))).all():
        others = [m for m in _members(s, l.product_id) if m.id != l.id and m.source_id != l.source_id]
        bad = next(((m, r) for m in others if (r := match_score(l, m)).decision != "auto"), None)
        if bad is None:
            continue
        m, r = bad
        reasons = [f"re-validation: no longer an automatic match for '{m.title[:50]}' ({r.decision}: {'; '.join(r.reasons)[:90]})"]
        old = l.product_id
        newp = Product(title=l.title, brand=l.brand, category=l.category)
        s.add(newp)
        s.flush()
        l.product_id, l.match_status, l.match_reasons, l.match_confidence = newp.id, "review", reasons, min(r.score, 0.6)
        _queue(s, l, old, l.match_confidence, reasons)
        n += 1
    return n


def rematch_all(s: Session) -> dict:
    """Re-run matching for every listing not yet matched or confirmed (after matcher/identifier changes). Rejected
    pairs are never re-queued or auto-merged (enforced inside attach_or_queue). Returns counts."""
    out = {"revalidated_demoted": revalidate_auto(s), "checked": 0, "auto": 0, "review": 0, "demoted": 0}
    out.update(prune_candidates(s))
    for l in s.scalars(select(Listing).where(Listing.match_status.in_(("self", "review")), Listing.active.is_(True)).order_by(Listing.id)).all():
        out["checked"] += 1
        r = attach_or_queue(s, l)
        out["auto"] += int(bool(r and r.decision == "auto"))
        out["review"] += int(bool(r and r.decision == "review"))
    for sid in {x for (x,) in s.execute(select(Listing.source_id).distinct())}:
        out["demoted"] += demote_ambiguous(s, sid)
    out["pruned_after"] = prune_candidates(s)
    return out


def prune_candidates(s: Session) -> dict:
    """Clean the review queue and keep match state consistent: drop pending candidates that point at the listing's own
    product, that have no cross-merchant member to compare with, or that the CURRENT matcher scores 'none'; refresh
    scores; listings left in 'review' without any pending candidate become 'self'; a lone 'auto' without a partner
    becomes 'self'."""
    out = {"candidates_dropped": 0, "review_to_self": 0, "auto_to_self": 0}
    for c in s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending")).all():
        l = c.listing
        members = [m for m in _members(s, c.product_id) if m.source_id != l.source_id]
        if c.product_id == l.product_id or not members:
            s.delete(c); out["candidates_dropped"] += 1
            continue
        best = max((match_score(l, m) for m in members), key=lambda r: r.score)
        if best.decision == "none":
            s.delete(c); out["candidates_dropped"] += 1
        else:
            c.score, c.reasons = best.score, best.reasons
    s.flush()
    # one question per pair: of mirrored pending candidates keep the best-scored (then the oldest), drop the others
    pending = sorted(s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending")).all(), key=lambda c: (-c.score, c.id))
    kept: set[tuple[int, int]] = set()  # (from product, to product) of kept candidates
    for c in pending:
        if (c.product_id, c.listing.product_id) in kept:  # exact mirror of a kept question
            s.delete(c); out["candidates_dropped"] += 1
        else:
            kept.add((c.listing.product_id, c.product_id))
    s.flush()
    for l in s.scalars(select(Listing).where(Listing.match_status == "review", Listing.active.is_(True))).all():
        if not _under_review(s, l):
            l.match_status, l.match_reasons = "self", []
            out["review_to_self"] += 1
    for l in s.scalars(select(Listing).where(Listing.match_status == "auto", Listing.active.is_(True))).all():
        if not any(m.id != l.id and m.source_id != l.source_id for m in _members(s, l.product_id)):
            l.match_status = "self"
            out["auto_to_self"] += 1
    return out
