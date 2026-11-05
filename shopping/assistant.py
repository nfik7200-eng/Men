"""Shopping research assistant: retrieval first, rules second, LLM optional.

Every product claim comes from a Listing row (cited by id + observation time). Things the dataset cannot
know (build quality, upgradeability, benchmarks, battery...) are reported as gaps, never guessed.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from watchtower.deals import DealScore, PeerContext, score_listing
from watchtower.models import Listing, utcnow
from watchtower.money import fmt_money
from watchtower.quality import price_status
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
    price_status: str = "verified"
    import_estimate: dict | None = None


def parse_query(q: str) -> dict:
    low = q.lower()
    out: dict = {"assumptions": [], "gaps": []}
    m = re.search(r"(?:under|below|less than|max(?:imum)?|up to|within|<)\s*([$£€])?\s*(\d[\d,]*(?:\.\d+)?)", low) \
        or re.search(r"([$£€])\s*(\d[\d,]*(?:\.\d+)?)", low)
    if m:
        sym = m.group(1) if m.lastindex and m.group(1) else None
        out["budget_minor"] = round(float(m.group(2).replace(",", "")) * 100)
        out["currency"] = {"$": "USD", "£": "GBP", "€": "EUR"}.get(sym or "")
        if not out["currency"]:
            out["currency"] = "USD"
            out["assumptions"].append("Budget has no currency symbol; assumed USD.")
    out["category"] = next((CATEGORY_WORDS[w] for w in re.findall(r"[a-z]+", low) if w in CATEGORY_WORDS), None)
    out["programming"] = bool(re.search(r"programm|coding|developer|software dev|\bcode\b", low))
    out["want_import"] = bool(re.search(r"import|shipping|ship |customs|duty|duties|landed|total cost", low))
    out["destination"] = next((c for name, c in COUNTRIES.items() if re.search(rf"\b{name}\b", low)), None)
    out["gaps"] = sorted({v for k, v in GAP_ASPECTS.items() if k in low})
    out["keywords"] = [w for w in re.findall(r"[a-z0-9]+", low)
                       if len(w) > 2 and w not in STOP and w not in CATEGORY_WORDS and not w.isdigit()
                       and not re.search(r"programm|coding|develop|import|shipping|customs|uganda|kenya|tanzania", w)
                       and not any(w.startswith(k) for k in ("upgradeab", "reliab"))]
    return out


def _fit(l: Listing, intent: dict) -> Pick:
    p = Pick(l, 0.0)
    sp = l.specs or {}
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


def answer(s: Session, question: str, now: datetime | None = None, use_llm: bool = True,
           shipping_minor: int | None = None) -> dict:
    now = now or utcnow()
    intent = parse_query(question)
    caveats: list[str] = list(intent["assumptions"])
    stmt = select(Listing).options(joinedload(Listing.source)).where(
        Listing.active.is_(True), Listing.price_minor.is_not(None), Listing.availability != "out_of_stock")
    if intent["category"]:
        stmt = stmt.where(Listing.category == intent["category"])
    if intent.get("currency"):
        stmt = stmt.where(Listing.currency == intent["currency"])
    cands = list(s.scalars(stmt).unique())
    # keywords are soft filters: applied only if they leave at least one candidate
    kws = intent["keywords"]
    if kws:
        narrowed = [l for l in cands if all(k in f"{l.title} {l.description} {l.brand or ''}".lower() for k in kws)]
        if narrowed:
            cands = narrowed
        elif intent["category"]:  # category known: keywords are soft preferences
            caveats.append(f"No listing matched the keyword(s) {kws}; showing the whole {intent['category']} category instead.")
        else:  # keywords ARE the product: do not substitute unrelated items
            cands = []
            caveats.append(f"No collected listing matches {kws}.")
    budget = intent.get("budget_minor")
    over = [l for l in cands if budget and l.price_minor > budget]
    cands = [l for l in cands if not budget or l.price_minor <= budget]

    ctx = PeerContext(s)
    picks = [_fit(l, intent) for l in cands]
    for p in picks:
        p.deal = score_listing(s, p.listing, ctx)
        p.price_status = price_status(p.listing, p.listing.source, now)
    ranked = sorted(picks, key=lambda p: (-p.fit, p.listing.price_minor))
    qualified = [p for p in ranked if not p.misses] or []
    pool = qualified or ranked
    if intent["programming"] and not qualified and ranked:
        caveats.append("No listing fully meets the programming requirements; showing the closest matches with their shortfalls.")
    top = pool[:3]
    alternatives: list[Pick] = []
    if top:
        best = top[0]
        alternatives = [p for p in pool[3:] if p.listing.price_minor <= best.listing.price_minor * 0.9 and p.fit >= best.fit - 2][:2]
        if intent["want_import"]:
            for p in top[:1] + alternatives[:1]:
                p.import_estimate = importcost.estimate(p.listing.price_minor, p.listing.currency, intent["destination"],
                                                        shipping_minor)
            if not intent["destination"]:
                caveats.append("No destination country recognised; used DEFAULT assumed import rates.")
    if cands and all(p.listing.source.kind == "sandbox" for p in picks):
        caveats.append("All matching listings come from DEMO/sandbox catalogues, not real retailers — treat prices as test data.")
    if any(p.price_status == "stale" for p in top):
        caveats.append("Some prices are stale (older than the source's freshness window).")
    gaps = [f"Our data has no {g}; those aspects cannot be assessed here." for g in intent["gaps"]]
    if not cands and (over or not any(c.startswith('No collected listing') for c in caveats)):
        why = (f"{len(over)} matching listing(s) exist but all are above your budget "
               f"(cheapest {fmt_money(min(l.price_minor for l in over), over[0].currency)})." if over else
               "No collected listing matches this request.")
        caveats.append(why)

    result = {
        "question": question, "interpretation": {k: v for k, v in intent.items() if k not in ("assumptions", "gaps")},
        "picks": top, "alternatives": alternatives, "gaps": gaps, "caveats": caveats,
        "retrieved_listing_ids": [p.listing.id for p in top + alternatives], "candidate_count": len(cands),
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
    if not r["picks"]:
        lines.append("I could not find a suitable listing in the collected data.")
    for i, p in enumerate(r["picks"], 1):
        l = p.listing
        lines.append(f"{i}. {l.title} — {fmt_money(l.price_minor, l.currency)} at {l.source.name} "
                     f"[listing #{l.id}, observed {l.last_observed_at:%Y-%m-%d %H:%M} UTC, price {p.price_status}]")
        if p.meets: lines.append("   Meets: " + "; ".join(p.meets))
        if p.misses: lines.append("   Falls short: " + "; ".join(p.misses))
        if p.unknown: lines.append("   Not stated in source: " + "; ".join(p.unknown))
        if p.deal: lines.append(f"   Deal assessment: {p.deal.label} (confidence {p.deal.confidence})")
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
