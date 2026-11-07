"""Acquisition intelligence: which discovered product URLs are worth fetching next, and why.

Discovery is cheap (a sitemap or category page); fetching is the expensive, politely rate-limited step. With a per-run
page budget, WHICH never-fetched URLs get the slots decides what the platform learns. The old order was (adapter tier,
alphabetical URL), which starved the tail and could not see that a URL names a product another merchant already sells.

Score (points, all recorded on the crawl_urls row so "why was this deferred?" is answerable):
  tier           adapter hint of how central the page is (core boards > same-brand accessories > rest)          0-40
  cross_merchant the URL's NAME (slug / card text) matches an active listing at ANOTHER live merchant, after the
                 matcher's hard vetoes (variant, product type, kit/bundle, generation, colour, units)            0-40
  new_coverage   adds a product the merchant's own collected listings do not already name                       0-10
  retry          a previously failed, never-acquired URL whose backoff elapsed (bounded by MAX_FAILS)           0-5
  aging          +AGING_STEP per run the URL lost to the budget, capped (nothing is starved forever)            0-AGING_CAP

Safety: a name hint is PRIORITY-ONLY evidence. It never writes product_id / match_status and never creates review
candidates; matching still happens after the page is fetched and its real data is extracted. Nothing here reads
affiliate data, so affiliate links cannot influence order. Everything is deterministic (ties: score, then URL).
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .matching import name_signature, title_conflict
from .models import CrawlUrl, Listing, Source

TIER_POINTS = {0: 40, 1: 20, 2: 5}
CROSS_EXACT, CROSS_STRONG, CROSS_FAMILY = 40, 28, 10
NEW_COVERAGE_POINTS = 10
RETRY_POINTS = 5
AGING_STEP, AGING_CAP = 3, 15
MAX_FAILS = 5  # a never-acquired URL that failed this many times is rejected until its sitemap entry changes


@dataclass
class Component:
    name: str
    points: float
    why: str


@dataclass
class Scored:
    crawl: CrawlUrl
    components: list[Component] = field(default_factory=list)

    @property
    def score(self) -> float:
        return round(sum(c.points for c in self.components), 2)


def hint_from_url(url: str) -> str | None:
    """Words in a descriptive slug ('/raspberry-pi-zero-2-w.html' -> 'raspberry pi zero 2 w'); None for numeric/opaque paths."""
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.html?$", "", slug)
    words = re.findall(r"[a-z0-9]+", slug.lower())
    if len([w for w in words if w.isalpha()]) < 2:
        return None  # '/product/5812': an id, not a name
    return " ".join(words)


def counterpart_pool(s: Session, own_source_id: int) -> list[Listing]:
    """Active listings of OTHER live merchants: the products this merchant could be compared with."""
    return list(s.scalars(select(Listing).join(Source, Source.id == Listing.source_id).where(
        Source.kind == "live", Listing.source_id != own_source_id, Listing.active.is_(True))))


def _cross_merchant(hint: str | None, pool: list[Listing]) -> Component:
    if not hint:
        return Component("cross_merchant", 0, "no name hint for this URL (opaque id): cannot compare before fetching")
    best: tuple[float, str] | None = None
    veto: str | None = None
    for l in pool:
        sh, sl = name_signature(hint, l.brand), name_signature(l.title, l.brand)
        if not sh or not sl:
            continue
        overlap = len(sh & sl) / len(sh | sl)
        if overlap < 0.5:
            continue
        conflict = title_conflict(hint, l.title)
        if conflict:
            veto = veto or f"vetoed vs '{l.title[:50]}' ({l.source.name}): {conflict}"
            continue
        if sh == sl:
            pts, level = CROSS_EXACT, "exact"
        elif (sh < sl or sl < sh) and len(sh ^ sl) <= 1 and all(w.isalpha() and len(w) >= 3 for w in sh ^ sl):
            # one extra real WORD ('Zero 2 W' vs 'Zero 2 W Mini'). A differing number ('Pico W' vs 'Pico 2 W') or a
            # one/two-letter suffix ('Pico' vs 'Pico W', 'Zero' vs 'Zero WH') is a different model: family evidence only
            pts, level = CROSS_STRONG, "strong"
        else:
            pts, level = CROSS_FAMILY, "family"
        if best is None or pts > best[0]:
            best = (pts, f"{level} name match with '{l.title[:60]}' at {l.source.name} (listing #{l.id})")
    if best:
        return Component("cross_merchant", best[0], best[1])
    return Component("cross_merchant", 0, veto or "no similar product at another merchant yet")


def rank_fresh(s: Session, src: Source, fresh: list[CrawlUrl], tiers: dict[str, int], hints: dict[str, str | None],
               now: datetime) -> list[Scored]:
    """Score never-fetched URLs; best first. Pure with respect to the database (writes nothing)."""
    pool = counterpart_pool(s, src.id)
    own = {name_signature(l.title, l.brand) for l in s.scalars(select(Listing).where(Listing.source_id == src.id, Listing.active.is_(True)))}
    out = []
    for c in fresh:
        sc = Scored(c)
        tier = tiers.get(c.url, 9)
        sc.components.append(Component("tier", TIER_POINTS.get(tier, 0), f"adapter tier {tier}"))
        hint = hints.get(c.url)
        sc.components.append(_cross_merchant(hint, pool))
        dup = bool(hint) and name_signature(hint, None) in own
        sc.components.append(Component("new_coverage", 0 if dup else NEW_COVERAGE_POINTS,
                                       "same name as a listing already collected here" if dup else "no listing of that name collected yet"))
        if (c.fail_count or 0) > 0:
            sc.components.append(Component("retry", RETRY_POINTS, f"retry after {c.fail_count} failed attempt(s), backoff elapsed"))
        wait = min(AGING_CAP, (c.defer_count or 0) * AGING_STEP)
        if wait:
            sc.components.append(Component("aging", wait, f"lost the budget in {c.defer_count} earlier run(s)"))
        out.append(sc)
    out.sort(key=lambda x: (-x.score, x.crawl.url))
    return out


@dataclass
class Plan:
    recheck: list[CrawlUrl]
    fresh: list[CrawlUrl]
    ranked: list[Scored]
    counts: dict


def plan_run(s: Session, src: Source, known: dict[str, CrawlUrl], discovered: set[str], tiers: dict[str, int],
             hints: dict[str, str | None], cap: int, now: datetime, recheck_share: float = 0.6) -> Plan:
    """Decide this run's fetch list and RECORD the decision for every known URL (selection_state / selection_reason /
    priority_score / priority_reasons / defer_count / last_considered_at).

    Re-checks of known listings keep most of the budget (price history is the core asset): `recheck_share` of `cap`,
    oldest-fetched first; leftovers flow to discovery and vice versa. Never-fetched URLs are ranked by `rank_fresh`."""
    recheck_pool, fresh_pool = [], []
    counts = {"selected": 0, "deferred": 0, "rejected": 0, "skipped": 0}

    def mark(c: CrawlUrl, state: str, reason: str) -> None:
        c.selection_state, c.selection_reason, c.last_considered_at = state, reason[:120], now
        counts[state] += 1

    for url, c in known.items():
        hint = hints.get(url)
        if hint:
            c.name_hint = hint[:200]
        if url not in discovered:
            mark(c, "skipped", "not_in_discovery")  # absence from a sitemap is never evidence of removal
        elif c.gone:
            mark(c, "rejected", "gone")
        elif c.last_outcome == "disallowed" and c.next_check_at and c.next_check_at > now:
            mark(c, "rejected", "disallowed")
        elif c.listing_id is None and c.last_fetched_at is None and (c.fail_count or 0) >= MAX_FAILS:
            mark(c, "rejected", "retry_limit")
        elif c.next_check_at is not None and c.next_check_at > now:
            mark(c, "deferred", "not_due")
        elif c.last_fetched_at is None:
            fresh_pool.append(c)
        else:
            recheck_pool.append(c)
    recheck_pool.sort(key=lambda c: (c.last_fetched_at, c.url))
    ranked = rank_fresh(s, src, fresh_pool, tiers, hints, now)
    n_re = min(len(recheck_pool), max(1, round(cap * recheck_share)) if recheck_pool else 0)
    n_new = min(len(ranked), max(0, cap - n_re))
    n_re = min(len(recheck_pool), max(0, cap - n_new))
    for i, c in enumerate(recheck_pool):
        if i < n_re:
            mark(c, "selected", f"recheck {i + 1} of {len(recheck_pool)} due (oldest first)")
        else:
            mark(c, "deferred", f"budget: recheck rank {i + 1} of {len(recheck_pool)} due, {n_re} slot(s)")
    cutoff = ranked[n_new - 1].score if n_new and n_new <= len(ranked) else None
    for i, sc in enumerate(ranked):
        c = sc.crawl
        c.priority_score = sc.score
        c.priority_reasons = [{"name": k.name, "points": k.points, "why": k.why} for k in sc.components]
        if i < n_new:
            mark(c, "selected", f"discovery rank {i + 1} of {len(ranked)} (score {sc.score})")
            c.defer_count = 0
        else:
            mark(c, "deferred", f"budget: rank {i + 1} of {len(ranked)}, {n_new} slot(s), cut-off score {cutoff}")
            c.defer_count = (c.defer_count or 0) + 1
    return Plan(recheck_pool[:n_re], [x.crawl for x in ranked[:n_new]], ranked, counts)
