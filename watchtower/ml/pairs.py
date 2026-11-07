"""Candidate pair generation and feature extraction for cross-merchant product matching.

Pure and deterministic: same listings in -> same pairs/features out (no randomness, no clock, no I/O, stable ordering).
Features reuse the primitives of `watchtower.matching` so the ML view and the rule view cannot silently drift apart.
Features are numeric only; a missing value is encoded as 0.0 PLUS an explicit `has_*` indicator (never as a fake value).
Nothing here is evidence FOR a merge on its own: scores derived from these features are advisory.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from types import SimpleNamespace
from typing import cast

from watchtower import matching as M
from watchtower.models import Listing
from watchtower.specs import canon_brand

SPEC_KEYS = ("screen_in", "ram_gb", "storage_gb", "cpu_family")
SCREEN_TOLERANCE_IN = 0.15  # screen sizes differing by more than this many inches conflict (same tolerance as the rule matcher)
# candidate_pairs skips any name-signature token shared by MORE than this many listings (stop-word-like tokens such as a brand or
# "board" would make blocking quadratic). Consequence: two listings whose ONLY shared token is such a token are never candidates;
# they are still found when they share any rarer token. Documented in docs/ML.md.
MAX_BLOCK_SIZE = 400

# Features that merely restate the rule matcher's own vetoes. Kept for the rule view, excluded from the "independent" model
# feature set so a model cannot trivially learn "veto => negative" and look good (see docs/ML.md, circularity).
RULE_ECHO_FEATURES = ("title_conflict", "hard_conflict", "gtin_shared", "mpn_shared")
PAIR_FEATURE_NAMES = (
    "name_jaccard", "name_overlap_min", "name_equal", "name_sig_len_min",
    "brand_equal", "brand_missing_any", "brand_conflict",
    "category_equal", "category_conflict",
    "unit_kinds_agree", "unit_kinds_conflict",
    "spec_agree", "spec_conflict", "spec_comparable",
    "model_code_jaccard", "has_model_codes",
    "seq_ratio",
    "title_conflict", "hard_conflict", "gtin_shared", "mpn_shared",
    "price_ratio", "has_price_pair",
    "same_source",
)
MODEL_FEATURE_NAMES = tuple(f for f in PAIR_FEATURE_NAMES if f not in RULE_ECHO_FEATURES and f != "same_source")


def _L(v: SimpleNamespace) -> Listing:
    """The rule matcher is typed on `Listing` but only reads attributes. `view()` provides the same attribute surface, so the
    duck typing is deliberate; this single cast documents it instead of scattering ignores."""
    return cast(Listing, v)


def view(l) -> SimpleNamespace:
    """Normalise a Listing (or any duck-typed row) so the matching helpers never see None where they expect a dict/str."""
    g = lambda k, d=None: getattr(l, k, d)
    return SimpleNamespace(
        id=g("id"), source_id=g("source_id"), product_id=g("product_id"), title=g("title") or "", brand=g("brand"),
        category=g("category"), specs=dict(g("specs") or {}), identifiers=dict(g("identifiers") or {}), gtin=g("gtin"),
        mpn=g("mpn"), sku=g("sku"), condition=g("condition"), price_minor=g("price_minor"), currency=g("currency"),
        match_status=g("match_status"))


def _sim_tokens(t: str) -> str:
    return " ".join(sorted(re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", t.lower())))


def pair_features(a, b) -> dict[str, float]:
    """Feature dict for an (a, b) listing pair. Symmetric in a and b."""
    a, b = view(a), view(b)
    ba, bb = canon_brand(a.brand), canon_brand(b.brand)
    sa, sb = M.name_signature(a.title, a.brand), M.name_signature(b.title, b.brand)
    union, inter = len(sa | sb), len(sa & sb)
    ua, ub = M.unit_values(a.title), M.unit_values(b.title)
    shared_kinds = ua.keys() & ub.keys()
    kinds_conflict = sum(1 for k in shared_kinds if not (ua[k] <= ub[k] or ub[k] <= ua[k]))
    both_spec = [k for k in SPEC_KEYS if a.specs.get(k) is not None and b.specs.get(k) is not None]
    spec_conf = sum(1 for k in both_spec if (abs(a.specs[k] - b.specs[k]) > SCREEN_TOLERANCE_IN if k == "screen_in" else a.specs[k] != b.specs[k]))
    ma, mb = M.model_tokens(a.title), M.model_tokens(b.title)
    cat_known = bool(a.category and b.category and a.category not in M._GENERIC_CATEGORIES and b.category not in M._GENERIC_CATEGORIES)
    pa, pb = a.price_minor, b.price_minor
    price_ok = bool(pa and pb and pa > 0 and pb > 0 and a.currency and a.currency == b.currency)
    ga, gb = M.gtin_of(_L(a)), M.gtin_of(_L(b))
    xa, xb = M.mpn_of(_L(a)), M.mpn_of(_L(b))
    f = {
        "name_jaccard": inter / union if union else 0.0,
        "name_overlap_min": inter / min(len(sa), len(sb)) if sa and sb else 0.0,
        "name_equal": float(bool(sa) and sa == sb),
        "name_sig_len_min": float(min(len(sa), len(sb))),
        "brand_equal": float(bool(ba) and ba == bb),
        "brand_missing_any": float(not ba or not bb),
        "brand_conflict": float(bool(ba and bb and ba != bb)),
        "category_equal": float(cat_known and a.category == b.category),
        "category_conflict": float(cat_known and a.category != b.category),
        "unit_kinds_agree": float(len(shared_kinds) - kinds_conflict),
        "unit_kinds_conflict": float(kinds_conflict),
        "spec_agree": float(len(both_spec) - spec_conf),
        "spec_conflict": float(spec_conf),
        "spec_comparable": float(len(both_spec)),
        "model_code_jaccard": len(ma & mb) / len(ma | mb) if ma and mb else 0.0,
        "has_model_codes": float(bool(ma and mb)),
        "seq_ratio": SequenceMatcher(None, _sim_tokens(a.title), _sim_tokens(b.title)).ratio(),
        "title_conflict": float(M.title_conflict(a.title, b.title) is not None),
        "hard_conflict": float(M.hard_conflict(_L(a), _L(b)) is not None),
        "gtin_shared": float(bool(ga and ga == gb)),
        "mpn_shared": float(bool(xa and xa == xb and ba and ba == bb)),
        "price_ratio": min(pa, pb) / max(pa, pb) if price_ok else 0.0,
        "has_price_pair": float(price_ok),
        "same_source": float(a.source_id is not None and a.source_id == b.source_id),
    }
    assert tuple(f) == PAIR_FEATURE_NAMES
    return f


def feature_vector(a, b, names=PAIR_FEATURE_NAMES) -> list[float]:
    f = pair_features(a, b)
    return [f[n] for n in names]


def candidate_pairs(listings, cross_source_only: bool = True) -> list[tuple[int, int]]:
    """Blocked candidate pairs of listing ids (a_id < b_id), sorted. Blocking keeps the pair count near-linear:
    two listings are candidates iff they come from different sources (default), have compatible canonical brands
    (equal, or one missing) and share at least one distinguishing name-signature token. Tokens shared by more than MAX_BLOCK_SIZE listings are skipped
    (see the constant). Pure/deterministic."""
    rows = sorted((view(l) for l in listings), key=lambda r: r.id)
    sig = {r.id: M.name_signature(r.title, r.brand) for r in rows}
    brand = {r.id: canon_brand(r.brand) for r in rows}
    by_id = {r.id: r for r in rows}
    index: dict[str, list[int]] = {}
    for r in rows:
        for tok in sorted(sig[r.id]):
            index.setdefault(tok, []).append(r.id)
    out: set[tuple[int, int]] = set()
    for ids in index.values():
        if len(ids) > MAX_BLOCK_SIZE:  # stop-word-like token: blocking would explode; such pairs are found via rarer tokens
            continue
        for i, x in enumerate(ids):
            for y in ids[i + 1:]:
                if cross_source_only and by_id[x].source_id == by_id[y].source_id:
                    continue
                if brand[x] and brand[y] and brand[x] != brand[y]:
                    continue
                out.add((x, y))
    return sorted(out)


def rule_decision(a, b) -> tuple[str, float]:
    """The existing rule-based matcher's decision for the pair: ('auto'|'review'|'none', score). The baseline to beat."""
    r = M.match_score(_L(view(a)), _L(view(b)))
    return r.decision, r.score
