"""Shopping research assistant: retrieval first, rules second, LLM optional.

Every product claim comes from a Listing row (cited by id + observation time). Things the dataset cannot
know (build quality, upgradeability, benchmarks, battery...) are reported as gaps, never guessed.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from watchtower import config
from watchtower.currency import to_minor
from watchtower.deals import DealScore, HistoryContext, PeerContext, score_listing
from watchtower.models import CrawlUrl, Listing, MatchCandidate, Source, utcnow
from watchtower.money import fmt_money
from watchtower.quality import price_status
from watchtower.acquisition import hint_from_url
from watchtower.coverage import _classify
from watchtower.matching import TYPE_WORDS, name_signature, title_conflict
from watchtower.specs import CPU_TIER

from . import importcost, llm

CATEGORY_WORDS = {"laptop": "laptop", "laptops": "laptop", "notebook": "laptop", "tablet": "tablet", "tablets": "tablet",
                  "ipad": "tablet", "phone": "phone", "phones": "phone", "smartphone": "phone", "mobile": "phone",
                  "book": "book", "books": "book", "novel": "book"}
GAP_ASPECTS = {"upgradeab": "upgradeability (RAM/SSD slots)", "build quality": "build quality", "build": "build quality",
               "benchmark": "performance benchmarks", "performance": "measured performance/benchmarks",
               "battery": "battery life", "weight": "weight", "keyboard": "keyboard quality", "display": "display quality",
               "reliab": "reliability data", "warranty": "warranty terms", "seller": "seller reputation"}
COUNTRIES = {"uganda": "UG", "kenya": "KE", "tanzania": "TZ", "rwanda": "RW", "nigeria": "NG", "ghana": "GH",
             "south africa": "ZA", "india": "IN", "uk": "GB", "usa": "US"}
STOP = set("the a an and or for with to of in on under over below above best find me my i want need good cheap cheaper "
           "use using new latest top "
           "least minimum at ram ssd hdd storage gb tb inch hours hour days day battery warranty year years free fast "
           "compare comparison include estimate total cost importing import it them please what which is are can you "
           "than less more up max budget price prices memory build quality performance upgradeability alternatives "
           "alternative".split())


@dataclass
class Pick:
    listing: Listing
    fit: float
    meets: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    deal: DealScore | None = None
    extra: int = 0  # product words in the title that the user did not ask for (kit, module, cooler...)
    other_type: bool = False  # title names a product TYPE the user did not ask for (compute module, case, cable...)
    relevant: bool = True  # mentions every product word the user used that appears in any candidate
    price_status: str = "verified"
    import_estimate: dict | None = None
    possible: list[dict] = field(default_factory=list)  # unverified candidates awaiting human review: NOT compared
    history: str = ""  # how much price history backs this price (live listings only)
    also: list[dict] = field(default_factory=list)  # the same product at other shops: source, price, how sure we are


_CUR_SYM = {"$": "USD", "£": "GBP", "€": "EUR"}
_CUR_WORD = {"usd": "USD", "dollar": "USD", "dollars": "USD", "bucks": "USD", "gbp": "GBP", "pound": "GBP", "pounds": "GBP",
             "sterling": "GBP", "eur": "EUR", "euro": "EUR", "euros": "EUR", "ugx": "UGX", "shilling": "UGX", "shillings": "UGX"}
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_AMT = rf"([$£€])?\s*{_NUM}\s*([a-z]{{2,10}})?"
_NOT_MONEY = re.compile(r"^(hours?|hrs?|days?|weeks?|months?|years?|yrs?|minutes?|mins?|gb|tb|mb|ghz|mhz|hz|mah|inch|inches|in|kg|g|"
                        r"cm|mm|pcs|pieces|cores?|mp|fps|nits|w|wh)$")
_NEG = re.compile(r"\b(?:not|no|without|except|excluding|avoid|never)\s+(?:an?\s+)?([a-z0-9]+)")


def _amount(sym, num, word):
    cur = _CUR_SYM.get(sym or "") or _CUR_WORD.get((word or "").lower())
    if word and not cur and _NOT_MONEY.match(word.lower()):
        return None  # "10 hours", "16 GB" are not prices
    return round(float(num.replace(",", "")) * 100), cur


_TYPED_CATEGORIES = {"maker-electronics"}  # where matching.TYPE_WORDS name accessories/parts (not laptop 'display' etc.)
_UNIT_SP = re.compile(r"(\d+(?:\.\d+)?)\s*(gb|tb|mb|ghz|mhz|mah)\b")


def norm_units(text: str) -> str:
    """'8 GB' / '8GB' / '8gb' -> '8gb': unit spelling must never decide relevance (merchants write both)."""
    return _UNIT_SP.sub(r"\1\2", text.lower())


def has_word(k: str, text: str) -> bool:
    """Keyword in (unit-normalised) text. Tokens starting with a digit ('8gb') must match whole ('128gb' is not '8gb');
    other words match as substrings so 'cable' still finds 'cables'."""
    if k[:1].isdigit():
        return re.search(rf"(?<![a-z0-9.]){re.escape(k)}(?![a-z0-9])", text) is not None
    if k in text:
        return True
    # plural asked, singular listed ('cases' / 'case', 'heatsinks' / 'heatsink', 'boxes' / 'box')
    return len(k) > 4 and k.endswith("s") and (k[:-1] in text or (k.endswith("es") and k[:-2] in text))


def parse_query(q: str) -> dict:
    low = q.lower()
    out: dict = {"assumptions": [], "gaps": []}
    # budget: ranges first, then cue+amount, then bare currency-marked amounts. Plain numbers with units are never money.
    rng = re.search(rf"(?:between\s+)?{_AMT}\s*(?:and|to|-|–)\s*{_AMT}", low)
    lo = hi = cur = None
    if rng and (rng.group(1) or rng.group(3) or rng.group(4) or "between" in rng.group(0)):
        a1 = _amount(rng.group(1), rng.group(2), rng.group(3))
        a2 = _amount(rng.group(4) or rng.group(1), rng.group(5), rng.group(6))
        if a1 and a2:
            (lo, c1), (hi, c2) = sorted((a1, a2), key=lambda t: t[0])
            cur = c1 or c2
    if hi is None:
        m = re.search(rf"(?:under|below|less than|max(?:imum)?|up to|within|budget(?: of)?|around|about|<)\s*{_AMT}", low)
        if m:
            got = _amount(m.group(1), m.group(2), m.group(3))
            if got:
                hi, cur = got
    if hi is None:
        m = re.search(rf"([$£€])\s*{_NUM}|{_NUM}\s*(usd|gbp|eur|dollars?|pounds?|euros?)\b", low)
        if m:
            hi = round(float((m.group(2) or m.group(3)).replace(",", "")) * 100)
            cur = _CUR_SYM.get(m.group(1) or "") or _CUR_WORD.get(m.group(4) or "")
    if hi is not None:
        out["budget_max_minor"] = hi
        out["budget_min_minor"] = lo
        out["currency"] = cur  # None = not stated; resolved against the catalogue, never assumed silently
    # category, use-case, import
    out["category"] = next((CATEGORY_WORDS[w] for w in re.findall(r"[a-z]+", low) if w in CATEGORY_WORDS), None)
    out["programming"] = bool(re.search(r"programm|coding|developer|software dev|\bcode\b", low))
    out["want_import"] = bool(re.search(r"import|shipping|ship |customs|duty|duties|landed|total cost", low))
    out["destination"] = next((c for name, c in COUNTRIES.items() if re.search(rf"\b{name}\b", low)), None)
    out["gaps"] = sorted({v for k, v in GAP_ASPECTS.items() if k in low})
    # spec requirements
    m = re.search(r"(\d+)\s*gb\s*(?:of\s*)?(?:ram|memory)|(?:ram|memory)\s*(?:of\s*)?(\d+)\s*gb", low)
    if m:
        out["req_ram_gb"] = int(m.group(1) or m.group(2))
    m = re.search(r"(\d+)\s*(gb|tb)\s*(?:of\s*)?(?:ssd|storage|hdd|disk|nvme)", low)
    if m:
        out["req_storage_gb"] = int(m.group(1)) * (1024 if m.group(2) == "tb" else 1)
    m = re.search(r"(\d{2}(?:\.\d)?)\s*(?:-?inch|\"|in\b)", low)
    if m:
        out["req_screen_in"] = float(m.group(1))
    # model tokens: bare numbers / single letters that name a model ('zero 2 w'), outside budgets and spec phrases
    spec_free = re.sub(r"\d[\d,.]*\s*(gb|tb|mb|ghz|mhz|inch|in\b|\"|hours?|hrs?|days?|years?|yrs?|mah|%|w\b(?=\s*(ac|power|psu|supply)))", " ", low)
    spec_free = re.sub(r"[$£€]\s*\d[\d,.]*|\d[\d,.]*\s*(usd|gbp|eur|dollars?|pounds?|euros?)", " ", spec_free)
    spent = {str(int(v / 100)) for v in (out.get("budget_max_minor"), out.get("budget_min_minor")) if v}
    out["model_tokens"] = [t for t in re.findall(r"[a-z0-9]+", spec_free)
                           if ((t.isdigit() and len(t) <= 2) or (len(t) == 1 and t.isalpha() and t not in "aI")) and t not in spent]
    # negation then keywords
    out["excluded"] = sorted({w for w in _NEG.findall(low) if w not in STOP})
    neg_words = set(out["excluded"])
    # spec requirements are minimums handled by _fit ('8 GB RAM' is met by 16 GB), never literal keywords
    consumed = set()
    if out.get("req_ram_gb"):
        consumed.add(f"{out['req_ram_gb']}gb")
    if out.get("req_storage_gb"):
        v = out["req_storage_gb"]
        consumed |= {f"{v}gb"} | ({f"{v // 1024}tb"} if v % 1024 == 0 else set())
    out["keywords"] = [w for w in re.findall(r"\d+(?:\.\d+)?[a-z]*|[a-z][a-z0-9]*", norm_units(low))  # keep '2.4ghz' whole
                       if w not in consumed and not re.fullmatch(r"\d+(?:\.\d+)?", w)
                       if len(w) > 2 and w not in STOP and w not in neg_words and w not in CATEGORY_WORDS and w not in _CUR_WORD
                       and not w.isdigit() and not _NOT_MONEY.match(w)
                       and not re.search(r"programm|coding|develop|import|shipping|customs|uganda|kenya|tanzania|between", w)
                       and not any(w.startswith(k) for k in ("upgradeab", "reliab"))]
    return out


def _fit(l: Listing, intent: dict) -> Pick:
    p = Pick(l, 0.0)
    sp = l.specs or {}
    for key, label, unit in (("req_ram_gb", "ram_gb", "GB RAM"), ("req_storage_gb", "storage_gb", "GB storage")):
        need, have = intent.get(key), sp.get(label)
        if need is None:
            continue
        if have is None:
            p.unknown.append(f"{unit.split()[1]} size not stated (you asked for {need}{unit[:2]}+)")
        elif have >= need:
            p.fit += 1.5; p.meets.append(f"{have}{unit} (you asked for {need}+)")
        else:
            p.fit -= 4; p.misses.append(f"{have}{unit}, below the {need} you asked for")
    if intent.get("req_screen_in") is not None:
        have = sp.get("screen_in")
        if have is None:
            p.unknown.append("screen size not stated")
        elif abs(have - intent["req_screen_in"]) <= 0.6:
            p.fit += 1; p.meets.append(f'{have}" screen')
        else:
            p.fit -= 2; p.misses.append(f'{have}" screen, not the {intent["req_screen_in"]}" you asked for')
    if intent["programming"] and l.category == "laptop":
        ram = sp.get("ram_gb")
        if ram is None:
            p.unknown.append("RAM not stated")
        elif ram >= 16:
            p.fit += 3; p.meets.append(f"{ram}GB RAM (comfortable for development)")
        elif ram >= 8:
            p.fit += 2; p.meets.append(f"{ram}GB RAM (workable)")
        else:
            p.fit -= 3; p.misses.append(f"only {ram}GB RAM")
        if sp.get("storage_type") == "ssd":
            p.fit += 1.5; p.meets.append("SSD storage")
        elif sp.get("storage_type") in ("hdd", "emmc"):
            p.fit -= 1; p.misses.append(f"{sp['storage_type'].upper()} storage (slower)")
        else:
            p.unknown.append("storage type not stated")
        cpu = sp.get("cpu_family")
        tier = CPU_TIER.get(cpu) if cpu else None
        if tier is None:
            p.unknown.append("CPU not identified")
        elif tier >= 3:
            p.fit += 2; p.meets.append(f"CPU family {cpu} (mid/high tier)")
        elif tier == 2:
            p.fit += 0.5; p.meets.append(f"CPU family {cpu} (entry)")
        else:
            p.fit -= 2; p.misses.append(f"CPU family {cpu} (low-end)")
        if sp.get("screen_in") is not None and sp["screen_in"] >= 13:
            p.fit += 0.5
        if sp.get("storage_gb") is not None and sp["storage_gb"] >= 256:
            p.fit += 0.5; p.meets.append(f"{sp['storage_gb']}GB storage")
    return p


_HINT_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_HINT_FILLER = {"the", "a", "and", "with", "for", "of", "to", "in", "on"}


_META_STOP = {"price", "prices", "dropped", "drop", "drops", "decreased", "decrease", "recently", "recent", "reduced", "fallen", "fell",
              "new", "newly", "appeared", "added", "listed", "products", "product", "listings", "insufficient", "history", "have", "which",
              "what", "has", "the", "enough", "not", "lack", "with", "any", "are", "there", "been", "lately", "latest"}


def _history_for(s: Session, ids: list[int]) -> HistoryContext:
    """Observation series for many listings in one query (a few thousand ids at most; beyond that, all active listings)."""
    if len(ids) <= 5000:
        return HistoryContext(s, ids)
    return HistoryContext(s, select(Listing.id).where(Listing.active.is_(True)))


def _market_kind(q: str) -> str | None:
    low = q.lower()
    if re.search(r"\b(price|prices|cost)\b.*\b(drop|dropped|decreas\w*|reduc\w*|fall|fallen|fell|cheaper)\b|\b(drop|decreas\w*|reduc\w*)\b.*\bprices?\b", low):
        return "price_drops"
    if re.search(r"(insufficient|not enough|lack\w*|no|enough|limited)\s+(price\s+)?history|history\s+(is|are)\s+(insufficient|short|limited)", low):
        return "history_gaps"
    if re.search(r"(recently|newly|just)\s+(appeared|added|listed)|what('s|\s+is|\s+are)?\s+new\b|\bnew\s+(products|listings|arrivals)", low):
        return "new_listings"
    return None


def _market_answer(s: Session, question: str, kind: str, now: datetime) -> dict:
    """Questions about the MARKET (new listings, recorded price drops, history gaps) answered only from stored evidence."""
    from watchtower.deals import collapse
    from watchtower.models import Event, Observation
    intent = parse_query(question)
    words = [w for w in intent["keywords"] if w not in _META_STOP] + list(intent.get("model_tokens", []))
    live = list(s.scalars(select(Listing).options(joinedload(Listing.source)).join(Source, Source.id == Listing.source_id).where(
        Source.kind == "live", Listing.active.is_(True))))
    if words:
        live = [l for l in live if all(has_word(w, norm_units(f"{l.title} {l.brand or ''}")) for w in words)]
    ctx = PeerContext(s, now)
    hist_ctx = _history_for(s, [l.id for l in live])
    caveats: list[str] = []
    picks: list[Pick] = []

    def pick(l: Listing, note: str) -> Pick:
        p = Pick(l, 0.0)
        p.meets.append(note)
        p.deal = score_listing(s, l, ctx, now, hist_ctx)
        p.price_status = price_status(l, l.source, now)
        return p

    ids = [l.id for l in live]
    obs = s.execute(select(Observation.listing_id, Observation.observed_at).where(Observation.listing_id.in_(ids))).all() if ids else []
    n_obs, span = len(obs), ((max(o[1] for o in obs) - min(o[1] for o in obs)).days if obs else 0)
    if kind == "price_drops":
        since = now - timedelta(days=14)
        evs = s.execute(select(Event, Listing).join(Listing, Listing.id == Event.listing_id).where(
            Event.kind == "price_drop", Event.detected_at >= since, Listing.id.in_(ids))).all() if ids else []
        latest = {}
        for e, l in sorted(evs, key=lambda x: x[0].detected_at):
            latest[l.id] = (e, l)
        for e, l in sorted(latest.values(), key=lambda x: (x[0].pct or 0)):
            old, new = int(e.old_value), int(e.new_value)
            picks.append(pick(l, f"price fell from {fmt_money(old, l.currency)} to {fmt_money(new, l.currency)} ({(e.pct or 0):+.1f}%) on {e.detected_at:%Y-%m-%d}"))
        if picks:
            caveats.append("A recorded drop is a change between our own observations. It is not a verified price drop: verification needs "
                           f"{config.MIN_HISTORY_OBS}+ collections over {config.MIN_HISTORY_SPAN_DAYS}+ days of history.")
        else:
            caveats.append(f"No price drop has been recorded in the last 14 days. We hold {n_obs} observation{'s' if n_obs != 1 else ''} for "
                           f"{len(live)} matching live listing(s) spanning {span} day{'s' if span != 1 else ''}, so we cannot say whether prices have fallen.")
    elif kind == "new_listings":
        first = min((l.first_seen_at for l in s.scalars(select(Listing).join(Source, Source.id == Listing.source_id).where(Source.kind == "live"))), default=None)
        fresh = [l for l in live if first and l.first_seen_at > first + timedelta(hours=1) and l.first_seen_at >= now - timedelta(days=14)]
        for l in sorted(fresh, key=lambda l: l.first_seen_at, reverse=True)[:10]:
            picks.append(pick(l, f"first seen {l.first_seen_at:%Y-%m-%d} at {l.source.name}"))
        if first:
            caveats.append(f"Listings first seen at our first collection ({first:%Y-%m-%d}) are the baseline, not new. 'New' means first seen after it.")
        if not picks:
            caveats.append("No listing has first appeared in the last 14 days.")
    else:  # history_gaps
        for l in live:
            h = collapse([o for o in hist_ctx.by_listing.get(l.id, []) if o.currency == l.currency])
            days = (h[-1].observed_at - h[0].observed_at).days if len(h) > 1 else 0
            if len(h) < 3 or days < 7:
                picks.append(pick(l, f"{len(h)} collection{'s' if len(h) != 1 else ''} over {days} day{'s' if days != 1 else ''}"))
        picks.sort(key=lambda p: (p.meets[0], p.listing.title))
        total = len(live)
        caveats.append(f"A verified price drop needs {config.MIN_HISTORY_OBS}+ collections over {config.MIN_HISTORY_SPAN_DAYS}+ days of history. {len(picks)} of {total} matching live listings do not have that yet.")
        picks = picks[:10]
    result = {"question": question, "kind": kind, "interpretation": {k: v for k, v in intent.items() if k not in ("assumptions", "gaps")},
              "picks": picks, "alternatives": [], "gaps": [], "caveats": caveats, "clarification": None,
              "retrieved_listing_ids": [p.listing.id for p in picks], "candidate_count": len(live), "uncollected": [], "llm_summary": None}
    result["text"] = render_text(result)
    return result


def _uncollected(s: Session, question: str, intent: dict, represented: set[int], limit: int = 3) -> list[dict]:
    """Real-shop pages that were DISCOVERED (their address names something like the question) but never collected, at shops
    that have no pick. Disclosure only: such a page has no price, stock or history and is never a pick or a comparison."""
    words = [w for w in intent["keywords"] if len(w) > 1] + list(intent.get("model_tokens", []))
    if len(set(words)) < 2:
        return []
    q = norm_units(question)
    qtok = set(_HINT_TOKEN.findall(q))
    brand_words = {w for (b,) in s.execute(select(Listing.brand).join(Source, Source.id == Listing.source_id).where(Source.kind == "live").distinct())
                   if b for w in re.findall(r"[a-z0-9]+", b.lower())}
    out = []
    # cheap SQL pre-filter on the most distinctive word (every candidate must contain ALL words, so this loses nothing)
    key = max((w for w in set(words) if w.isalnum()), key=lambda w: (len(w), w), default=None)  # decimals ('2.4ghz') have none
    cond = [Source.kind == "live", CrawlUrl.listing_id.is_(None), CrawlUrl.gone.is_(False)]
    if key:
        cond.append(or_(CrawlUrl.url.ilike(f"%{key}%"), CrawlUrl.name_hint.ilike(f"%{key}%")))
    rows = s.execute(select(CrawlUrl, Source).join(Source, Source.id == CrawlUrl.source_id).where(*cond).order_by(Source.key, CrawlUrl.url)).all()
    for c, src in rows:
        if src.id in represented or _classify(c) not in ("never_attempted", "fetch_error"):
            continue
        hint = norm_units(c.name_hint or hint_from_url(c.url) or "")  # the stored hint, else the words in the URL itself
        extra = set(_HINT_TOKEN.findall(hint)) - qtok - brand_words - _HINT_FILLER
        # every word of the page's name must be accounted for by the question: 'pico 2 w' is not 'pico w', a tutorial is not a product
        if hint and all(has_word(w, hint) for w in set(words)) and not extra and not title_conflict(q, hint):
            out.append({"source": src.name, "url": c.url})
    return out[:limit]


def answer(s: Session, question: str, now: datetime | None = None, use_llm: bool = True,
           shipping_major: float | None = None) -> dict:
    """shipping_major: a shipping cost the person typed, in MAJOR units of each pick's own currency (converted per pick)."""
    now = now or utcnow()
    kind = _market_kind(question)
    if kind:
        return _market_answer(s, question, kind, now)
    intent = parse_query(question)
    caveats: list[str] = list(intent["assumptions"])
    stmt = select(Listing).options(joinedload(Listing.source)).where(
        Listing.active.is_(True), Listing.price_minor.is_not(None))
    if intent["category"]:
        stmt = stmt.where(Listing.category == intent["category"])
    cands = list(s.scalars(stmt).unique())
    live = [l for l in cands if l.source.kind == "live"]
    if live:
        cands = live  # real retailer data answers real questions; demo/sandbox rows are not mixed in

    # negation: "not apple" removes matching listings outright
    for bad in intent["excluded"]:
        cands = [l for l in cands if bad not in f"{l.title} {l.brand or ''}".lower()]

    # keywords: brands are hard requirements; other words are soft preferences (reported if nothing matched)
    brands = {(l.brand or "").lower() for l in cands} - {""}
    kwset = set(intent["keywords"])
    brand_kw, brand_words = [], set()
    for b in brands:  # a brand is requested when ALL its words appear ('raspberry pi'), not only for one-word brands
        words = re.findall(r"[a-z0-9:]+", b)
        if words and all(w in kwset for w in words):
            brand_kw.append(b)
            brand_words |= set(words)
    soft_kw = [k for k in intent["keywords"] if k not in brand_words and k not in brands]
    if brand_kw:
        cands = [l for l in cands if (l.brand or "").lower() in brand_kw]
    text_of = lambda l: norm_units(f"{l.title} {l.description} {l.brand or ''}")
    if soft_kw and not intent["category"] and not brand_kw:  # keywords ARE the product when nothing else names it
        cands = [l for l in cands if any(has_word(k, text_of(l)) for k in soft_kw)]
        if not cands:
            caveats.append(f"No collected listing matches: {', '.join(soft_kw)}.")
    elif soft_kw:
        unmatched = [k for k in soft_kw if not any(has_word(k, text_of(l)) for l in cands)]
        generic = {"cheapest", "cheap", "best", "lowest", "price", "prices", "buy", "good", "top"}
        named = [k for k in unmatched if k not in generic]
        for code in re.findall(r"\b(?:[a-z]\d{1,3}|\d{1,3}[a-z])\b", question, flags=re.I):  # model codes such as R4, 5G (too short to be keywords)
            if code.lower() not in named and not any(has_word(code.lower(), text_of(l)) for l in cands):
                named.insert(0, code)
        if named:
            caveats.append(f"No collected listing is named like: {', '.join(named)}. The results below are different products, not that one; "
                           "it may simply not have been collected yet (see /coverage for what we hold).")
        elif unmatched:
            caveats.append(f"Ignored (no listing mentions): {', '.join(unmatched)}.")

    # budget: currency is explicit, inferred only when unambiguous, otherwise we ask
    clarification = None
    budget, budget_min, over = intent.get("budget_max_minor"), intent.get("budget_min_minor"), []
    if budget is not None:
        cur = intent.get("currency")
        present = sorted({l.currency for l in cands})
        if cur is None and len(present) == 1:
            cur = present[0]
            caveats.append(f"No currency stated in your budget; assumed {cur} (the only currency among matching listings). No conversion applied.")
        elif cur is None and len(present) > 1:
            clarification = (f"Your budget has no currency and matching listings are priced in {', '.join(present)}. "
                             "Which currency do you mean? (Prices are never converted without a sourced exchange rate.)")
            cands = []
        if cur is not None and clarification is None:
            cands = [l for l in cands if l.currency == cur]
            over = [l for l in cands if l.price_minor > budget]
            cands = [l for l in cands if l.price_minor <= budget and (budget_min is None or l.price_minor >= budget_min)]

    ctx = PeerContext(s, now)
    hist_ctx = _history_for(s, [l.id for l in cands])  # ONE query for every candidate's observations (was one per candidate)
    picks = [_fit(l, intent) for l in cands]
    for p in picks:
        av = p.listing.availability
        if av in ("out_of_stock", "backorder", "preorder"):  # shown, never hidden — but flagged and ranked below available items
            p.fit -= 5
            p.misses.append({"out_of_stock": "out of stock at last check", "backorder": "on backorder at last check",
                             "preorder": "pre-order only"}[av])
        p.fit += 0.75 * sum(has_word(k, text_of(p.listing)) for k in soft_kw)
        p.deal = score_listing(s, p.listing, ctx, now, hist_ctx)
        p.price_status = price_status(p.listing, p.listing.source, now)
    kw_present = [k for k in soft_kw if any(has_word(k, text_of(l)) for l in cands)]  # words that name something we actually hold
    word_in = lambda t, l: re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", text_of(l)) is not None
    model_present = [t for t in intent.get("model_tokens", []) if any(word_in(t, l) for l in cands)]
    for p in picks:
        p.relevant = all(has_word(k, text_of(p.listing)) for k in kw_present) and all(word_in(t, p.listing) for t in model_present)
    if model_present:  # the user named a specific model: 'Pi 5 Essential Kit' / 'Compute Module 5' are not 'Pi 5'
        qwords = set(re.findall(r"[a-z0-9]+", norm_units(question)))  # '8 GB' and '8GB' are the same word
        # the user asked for a product TYPE ('cases', 'heatsinks', 'power adapter'): then no type is "other"
        asked_type = any(w in TYPE_WORDS or w.rstrip("s") in TYPE_WORDS or w.rstrip("es") in TYPE_WORDS for w in qwords)
        for p in picks:
            p.extra = len(name_signature(p.listing.title, p.listing.brand) - qwords)
            p.other_type = (p.listing.category in _TYPED_CATEGORIES and not asked_type
                            and bool(set(re.findall(r"[a-z0-9]+", p.listing.title.lower())) & TYPE_WORDS))
    ranked = sorted(picks, key=lambda p: (not p.relevant, p.other_type, p.extra, -p.fit, p.listing.price_minor))
    relevant = [p for p in ranked if p.relevant] or ranked  # relevance to the user's words outranks availability
    if model_present and any(not p.other_type for p in relevant):
        # the user named a model: a different product class (a Compute Module for 'Pi 4', a case for 'Pi 5') is not an
        # answer, even when the exact product is out of stock — that is reported, not papered over
        left_out = [p for p in relevant if p.other_type]
        relevant = [p for p in relevant if not p.other_type]
        if left_out:
            caveats.append(f"{len(left_out)} listing(s) of other product types (e.g. '{left_out[0].listing.title[:50]}') "
                           "were left out because you named a specific model.")
    fresh = [p for p in relevant if p.price_status != "stale"]
    if fresh and len(fresh) < len(relevant):  # stale prices never outrank (or sit beside) fresh ones for the same ask
        caveats.append(f"{len(relevant) - len(fresh)} matching listing(s) omitted because their prices are stale.")
        relevant = fresh
    qualified = [p for p in relevant if not p.misses]
    pool = qualified or relevant
    if relevant and not qualified and all(any("stock" in m or "backorder" in m or "pre-order" in m for m in p.misses) for p in relevant):
        caveats.append("Matching listings exist but none is in stock at its last check; shown with their stock status.")
    elif (intent["programming"] or intent.get("req_ram_gb") or intent.get("req_storage_gb")) and not qualified and relevant:
        caveats.append("No listing fully meets your requirements; showing the closest matches with their shortfalls.")
    top = pool[:3]
    alternatives: list[Pick] = []
    if top:
        best = top[0]
        best_price = best.listing.price_minor
        alternatives = [p for p in pool[3:] if best_price is not None and p.listing.price_minor is not None
                        and p.listing.price_minor <= best_price * 0.9 and p.fit >= best.fit - 2][:2]
        if intent["want_import"]:
            for p in top[:1] + alternatives[:1]:
                ship_minor = to_minor(shipping_major, p.listing.currency) if shipping_major is not None else None
                p.import_estimate = importcost.estimate(p.listing.price_minor, p.listing.currency, intent["destination"],
                                                        ship_minor)
            if not intent["destination"]:
                caveats.append("No destination country recognised; used DEFAULT assumed import rates.")
    if cands and all(p.listing.source.kind != "live" for p in picks):
        caveats.append("All matching listings come from DEMO/sandbox catalogues, not real retailers — treat prices as test data.")
    if any(p.price_status == "stale" for p in top):
        caveats.append("Some prices are stale (older than the source's freshness window) — re-check before relying on them.")
    uncollected = _uncollected(s, question, intent, {p.listing.source_id for p in top + alternatives}) if top or cands else []
    for u in uncollected:
        caveats.append(f"Not collected yet: {u['source']} has a page at {u['url']} whose address suggests this product, but we have not "
                       "read it, so its price and stock are unknown. It is not counted as a comparison.")
    for p in top + alternatives:  # unverified possible matches (pending human review): disclosed, never compared
        l = p.listing
        if l.product_id is not None:
            cs = s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending", (MatchCandidate.listing_id == l.id) |
                                                        (MatchCandidate.product_id == l.product_id))).all()
            seen = set()
            for c in cs:
                others = ([m for m in s.scalars(select(Listing).options(joinedload(Listing.source)).where(
                    Listing.product_id == c.product_id, Listing.active.is_(True)))] if c.listing_id == l.id else [c.listing])
                for o in others:
                    if o.source_id != l.source_id and o.id not in seen:
                        seen.add(o.id)
                        p.possible.append({"source": o.source.name, "title": o.title, "listing_id": o.id})
    for p in top + alternatives:  # the same product at other shops, with how sure the match is
        if p.listing.product_id is not None and p.listing.source.kind == "live":
            for o in s.scalars(select(Listing).options(joinedload(Listing.source)).where(
                    Listing.product_id == p.listing.product_id, Listing.source_id != p.listing.source_id,
                    Listing.active.is_(True), Listing.price_minor.is_not(None))):
                p.also.append({"source": o.source.name, "price": fmt_money(o.price_minor, o.currency), "listing_id": o.id,
                               "sure": "confirmed by a person" if o.match_status == "confirmed" else "automatic match, not human-verified"})
    for p in top + alternatives:
        if p.deal and p.listing.source.kind == "live":
            l, n = p.listing, p.deal.n_obs or 0
            days = (l.last_observed_at - l.first_seen_at).days if l.last_observed_at and l.first_seen_at else 0
            p.history = (f"Price history: {n} observation{'s' if n != 1 else ''} over {days} day{'s' if days != 1 else ''}"
                         + ("" if p.deal.label == "Verified price drop" else f"; a verified price drop needs {config.MIN_HISTORY_OBS}+ observations over {config.MIN_HISTORY_SPAN_DAYS}+ days"))
    gaps = [f"Our data has no {g}; those aspects cannot be assessed here." for g in intent["gaps"]]
    if not cands and clarification is None:
        if over:
            caveats.append(f"{len(over)} matching listing(s) exist but all are above your budget "
                           f"(cheapest {fmt_money(min(p for p in (l.price_minor for l in over) if p is not None), over[0].currency)}).")
        elif not any(c.startswith("No collected listing") for c in caveats):
            caveats.append("No collected listing matches this request.")
    if clarification:
        caveats.append(clarification)

    result = {
        "question": question, "interpretation": {k: v for k, v in intent.items() if k not in ("assumptions", "gaps")},
        "kind": "search", "picks": top, "alternatives": alternatives, "gaps": gaps, "caveats": caveats, "clarification": clarification,
        "retrieved_listing_ids": [p.listing.id for p in top + alternatives], "candidate_count": len(cands), "uncollected": uncollected,
        "llm_summary": None,
    }
    result["text"] = render_text(result)
    if use_llm and top:
        facts = {"picks": [_fact(p) for p in top + alternatives], "gaps": gaps, "caveats": caveats}
        result["llm_summary"] = llm.summarise(facts, question)
    return result


def _fact(p: Pick) -> dict:
    l = p.listing
    return {"id": l.id, "title": l.title, "price": fmt_money(l.price_minor, l.currency), "source": l.source.name,
            "specs": l.specs, "meets": p.meets, "misses": p.misses, "unknown": p.unknown,
            "deal_label": p.deal.label if p.deal else None, "observed_at": str(l.last_observed_at)}


def render_text(r: dict) -> str:
    lines = []
    if not r["picks"] and r.get("kind", "search") == "search":
        lines.append("I could not find a suitable listing in the collected data.")
    elif not r["picks"]:
        lines.append("Nothing in the collected data answers this yet.")
    for i, p in enumerate(r["picks"], 1):
        l = p.listing
        lines.append(f"{i}. {l.title} — {fmt_money(l.price_minor, l.currency)} at {l.source.name} "
                     f"[listing #{l.id}, observed {l.last_observed_at:%Y-%m-%d %H:%M} UTC, price {p.price_status}]")
        if p.meets: lines.append("   Meets: " + "; ".join(p.meets))
        if p.misses: lines.append("   Falls short: " + "; ".join(p.misses))
        if p.unknown: lines.append("   Not stated in source: " + "; ".join(p.unknown))
        if p.deal: lines.append(f"   Deal assessment: {p.deal.label} (confidence {p.deal.confidence})")
        if p.history:
            lines.append("   " + p.history)
        if p.also:
            lines.append("   Also sold at: " + "; ".join(f"{a['source']} {a['price']} ({a['sure']})" for a in p.also))
        if p.possible:
            lines.append("   Possible match, awaiting human review (not counted as the same product): "
                         + "; ".join(f"{a['source']} '{a['title'][:50]}'" for a in p.possible))
        elif not p.also and p.listing.source.kind == "live" and p.listing.product_id is not None:
            lines.append("   No other shop in our data sells this product (yet).")
        if p.import_estimate and p.import_estimate.get("available"):
            e = p.import_estimate
            lines.append(f"   Estimated landed cost: {'≥ ' if e['partial'] else ''}{fmt_money(e['total_minor'], e['currency'])} "
                         f"(estimate; {'shipping unknown; ' if e['partial'] else ''}rates {'verified' if e['rule_verified'] else 'ASSUMED, unverified'})")
    if r["alternatives"]:
        lines.append("Cheaper alternatives:")
        for p in r["alternatives"]:
            lines.append(f"- {p.listing.title} — {fmt_money(p.listing.price_minor, p.listing.currency)} [listing #{p.listing.id}]")
    lines += [f"Gap: {g}" for g in r["gaps"]] + [f"Note: {c}" for c in r["caveats"]]
    return "\n".join(lines)
