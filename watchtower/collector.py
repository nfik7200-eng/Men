"""Runs one source adapter through the polite fetcher into the database, with run bookkeeping.

Integrity rules (docs/AUDIT_REPORT.md, docs/SOURCE_DISCOVERY.md):
  * non-demo/sandbox sources run only through the compliance gate (policy approved, robots still allows, real contact);
  * listing-page crawl is 'complete' only if EVERY start URL parsed >=1 item and no page failed; removals are inferred
    only after a complete run and never in bulk (removal guard);
  * product-page crawl (one page per product) only SAMPLES the catalogue: it is never complete, never infers removals
    from absence, and records a removal only when a listing's OWN url answers 404/410;
  * unchanged pages (HTTP 304) are re-confirmed as observations so history, staleness and removals keep working;
  * any unexpected exception marks the run failed; disabled sources are never fetched; one live run per source.
"""
import logging
import time
from typing import Any
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select

from . import config, policy
from .db import Database
from . import acquisition
from .fetcher import FetchBlocked, FetchDisallowed, FetchError, FetchGone, Fetcher
from .ingest import _event, ingest_listing, mark_removed, reconfirm
from .matching import demote_ambiguous
from .models import CollectionRun, CrawlUrl, Event, Listing, Source, utcnow
from .policy import ensure_policies
from .sources import ADAPTERS
from .structured import normalise_url

log = logging.getLogger("watchtower.collector")
ABANDON_AFTER = timedelta(minutes=45)
NON_PRODUCT_RECHECK = timedelta(days=30)
RECHECK_SHARE = 0.6
DISALLOWED_RECHECK = timedelta(days=7)  # robots.txt is re-read every run; a disallow may be lifted


class SourceDisabled(Exception):
    pass


class SourceBusy(Exception):
    pass


class SourceNotConfigured(Exception):
    """An API source without credentials/approval. Nothing is fetched; the message lists what is missing."""


def ensure_sources(db: Database) -> None:
    with db.session() as s:
        for a in ADAPTERS.values():
            src = s.query(Source).filter_by(key=a.key).one_or_none()
            if src is None:
                src = Source(key=a.key)
                s.add(src)
            src.name, src.base_url, src.country, src.currency = a.name, a.base_url, a.country, a.currency
            src.kind, src.terms_note, src.stale_after_hours = a.kind, a.terms_note, a.stale_after_hours


def set_enabled(db: Database, key: str, enabled: bool) -> None:
    ensure_sources(db)
    with db.session() as s:
        s.query(Source).filter_by(key=key).one().enabled = enabled


def _listing_url_ok(url: str, hosts: set[str]) -> bool:
    p = urlparse(url or "")
    h = (p.hostname or "").lower()
    return p.scheme in ("http", "https") and any(h == x or h.endswith("." + x) for x in hosts if x)


def run_source(db: Database, key: str, fetcher: Fetcher | None = None, max_pages: int | None = None,
               now_fn=utcnow) -> int:
    adapter = ADAPTERS[key]
    cap = min(max_pages, config.MAX_PAGE_BUDGET) if max_pages is not None else config.page_budget(adapter.key, adapter.default_max_pages)
    ensure_sources(db)
    ensure_policies(db)
    with db.session() as s:
        src = s.query(Source).filter_by(key=key).one()
        if not src.enabled:
            raise SourceDisabled(f"source {key} is disabled")
    ready, missing = adapter.configured()
    if not ready:
        raise SourceNotConfigured(f"{key} is not configured; missing: {', '.join(missing)}")
    fetcher = fetcher or Fetcher(allowed_hosts=adapter.allowed_hosts(), respect_robots=not adapter.official_api,
                                 min_delay=max(config.MIN_REQUEST_DELAY_S, config.MIN_LIVE_DELAY_S) if adapter.kind == "live" else None)
    policy.gate(db, adapter, fetcher, now_fn())  # raises SourceNotApproved
    with db.session() as s:
        src = s.query(Source).filter_by(key=key).one()
        now = now_fn()
        for r in s.scalars(select(CollectionRun).where(CollectionRun.source_id == src.id, CollectionRun.status == "running")):
            if now - r.started_at > ABANDON_AFTER:
                r.status, r.error, r.finished_at = "failed", "abandoned (process died or hung)", now
            else:
                raise SourceBusy(f"a run for {key} is already in progress (run {r.id})")
        run = CollectionRun(source_id=src.id, started_at=now)
        s.add(run)
        s.commit()
        run_id, src_id = run.id, src.id
    try:
        crawl = _crawl_products if getattr(adapter, "page_per_product", False) else _crawl
        crawl(db, adapter, fetcher, cap, run_id, src_id, now_fn)
    except Exception as e:  # unexpected: never leave a run stuck in 'running'
        with db.session() as s:
            run, src = s.get(CollectionRun, run_id), s.get(Source, src_id)
            run.status, run.complete, run.finished_at = "failed", False, now_fn()
            run.error = f"{type(e).__name__}: {e}"[:500]
            src.last_run_at, src.last_status, src.last_error = run.finished_at, "failed", run.error
        log.exception("run %s crashed", key)
        raise
    return run_id


def _finish(s, run, src, status, complete, errors, now_fn, t0, extraction):
    run.status, run.complete, run.finished_at = status, complete and status == "ok", now_fn()
    run.duration_s = round(time.monotonic() - t0, 2)
    run.extraction = extraction
    run.price_changes = s.scalar(select(func.count()).select_from(Event).where(
        Event.run_id == run.id, Event.kind.in_(("price_drop", "price_rise")))) or 0
    run.error = "; ".join(errors[:5]) or None
    src.last_run_at, src.last_status, src.last_error = run.finished_at, run.status, run.error
    if run.status in ("ok", "partial"):
        src.last_success_at = run.finished_at
    s.commit()


# --------------------------------------------------------------------------- listing-page crawl (paginated catalogues)
def _crawl(db, adapter, fetcher, cap, run_id, src_id, now_fn):
    t0 = time.monotonic()
    with db.session() as s:
        run, src = s.get(CollectionRun, run_id), s.get(Source, src_id)
        complete, errors, status = True, [], "ok"
        hosts = adapter.listing_hosts()
        nbytes, ex = 0, {"jsonld": 0, "css": 0, "failed": 0}
        for start in adapter.start_urls():
            url, pages, got = start, 0, 0
            while url:
                if pages >= cap:
                    complete = False  # capped: cannot infer removals
                    break
                try:
                    hdrs = adapter.request_headers(fetcher)
                    html = fetcher.get(url, headers=hdrs) if hdrs else fetcher.get(url)
                except FetchBlocked as e:
                    status, complete = "blocked", False
                    errors.append(str(e))
                    break
                except FetchError as e:
                    errors.append(str(e))
                    complete = False
                    break
                pages += 1
                run.pages_fetched += 1
                nbytes += len(html)
                items, nxt = adapter.parse_page(html, url)
                for msg in adapter.parse_errors:
                    run.items_seen += 1
                    run.items_invalid += 1
                    ex["failed"] += 1
                    errors.append(msg)
                now = now_fn()
                for raw in items:
                    run.items_seen += 1
                    if not _listing_url_ok(raw.url, hosts) or (raw.affiliate_url and not _listing_url_ok(raw.affiliate_url, hosts)):
                        run.items_invalid += 1
                        errors.append(f"{raw.external_id or '?'}: listing URL outside allowed hosts")
                        continue
                    raw.extraction_method = raw.extraction_method or "css"
                    listing, is_new, problems = ingest_listing(s, src, run, raw, now)
                    if listing is None:
                        run.items_invalid += 1
                        errors.append(f"{raw.external_id or '?'}: {'; '.join(problems)}")
                        continue
                    ex[raw.extraction_method] = ex.get(raw.extraction_method, 0) + 1
                    got += 1
                    run.items_new += int(is_new)
                    if problems:
                        run.items_invalid += 1
                s.commit()
                url = nxt
            if status == "blocked":
                break
            if got == 0 and pages > 0 and status == "ok":
                # 200 OK but nothing parsed: soft block, CAPTCHA, or layout change — never evidence of absence
                errors.append(f"no items parsed from {start}")
                complete = False
        run.bytes_fetched, run.pages_failed = nbytes, ex["failed"]
        demote_ambiguous(s, src.id)
        if run.items_seen == 0 and status != "blocked":
            status = "failed"
        elif errors and status == "ok":
            status = "partial"
        if status == "ok" and complete:
            _, guard = mark_removed(s, src, run, now_fn())
            if guard:
                errors.append(guard)
                status, complete = "partial", False
        _finish(s, run, src, status, complete, errors, now_fn, t0, ex)
        log.info("run %s %s: %s items, %s new, %s pages", adapter.key, run.status, run.items_seen, run.items_new, run.pages_fetched)


# --------------------------------------------------------------------------- product-page crawl (sitemap/category discovery)
def _backoff(fail_count: int) -> timedelta:
    """6 h, 12 h, 24 h, 48 h, then (from acquisition.MAX_FAILS) a flat 7-day cooldown: bounded, never hammering, never forever."""
    if fail_count >= acquisition.MAX_FAILS:
        return timedelta(days=7)
    return timedelta(hours=6 * 2 ** (max(fail_count, 1) - 1))


def _crawl_products(db, adapter, fetcher, cap, run_id, src_id, now_fn):
    t0 = time.monotonic()
    with db.session() as s:
        run, src = s.get(CollectionRun, run_id), s.get(Source, src_id)
        errors, status, nbytes = [], "ok", 0
        ex: dict[str, Any] = {"jsonld": 0, "microdata": 0, "css": 0, "not_modified": 0, "no_product": 0, "failed": 0}
        hosts = adapter.listing_hosts()
        now = now_fn()
        # 1. discovery -> crawl_urls (state is kept per URL, so unchanged/failed/non-product pages are remembered)
        try:
            entries = adapter.discover(fetcher)
        except FetchBlocked as e:
            run.bytes_fetched = 0
            _finish(s, run, src, "blocked", False, [str(e)], now_fn, t0, ex)
            return
        except FetchError as e:
            _finish(s, run, src, "failed", False, [f"discovery failed: {e}"], now_fn, t0, ex)
            return
        known = {c.url: c for c in s.scalars(select(CrawlUrl).where(CrawlUrl.source_id == src.id))}
        for e in entries:
            u = normalise_url(e.loc)
            if not _listing_url_ok(u, hosts):
                continue
            row = known.get(u)
            if row is None:
                row = CrawlUrl(source_id=src.id, url=u, first_seen_at=now)
                s.add(row)
                known[u] = row
            if e.lastmod and row.lastmod_seen and e.lastmod > row.lastmod_seen:
                row.next_check_at = now  # sitemap says the page changed: due immediately
            row.lastmod_seen = e.lastmod or row.lastmod_seen
        s.commit()
        discovered = {normalise_url(e.loc) for e in entries}
        prio = {normalise_url(e.loc): e.priority for e in entries}
        hints = {normalise_url(e.loc): (e.hint or acquisition.hint_from_url(e.loc)) for e in entries}
        # 2. explainable selection: re-checks keep most of the budget; never-fetched URLs are ranked (acquisition.py) and
        # every decision is recorded on the crawl_urls row
        allowed = getattr(fetcher, "allowed", None)
        if allowed is not None:  # robots is cached after the gate: label disallowed URLs WITHOUT spending a fetch slot on them
            for u in discovered & known.keys():
                c = known[u]
                if not c.gone and c.last_outcome != "disallowed" and not allowed(u):
                    c.last_outcome, c.last_outcome_detail = "disallowed", "robots.txt disallows this URL"
                    c.next_check_at = now + DISALLOWED_RECHECK
                    ex["disallowed"] = ex.get("disallowed", 0) + 1
        plan = acquisition.plan_run(s, src, known, discovered, prio, hints, cap, now, RECHECK_SHARE)
        recheck, fresh = plan.recheck, plan.fresh
        ex["plan"] = plan.counts
        s.commit()
        for crawl in recheck + fresh:
            try:
                res = fetcher.get_ex(crawl.url, etag=crawl.etag, last_modified=crawl.last_modified)
            except FetchDisallowed as e:  # this URL only (robots/allowlist): record it, never hammer it, keep going
                crawl.last_outcome, crawl.last_outcome_detail = "disallowed", str(e)[:200]
                crawl.next_check_at = now_fn() + DISALLOWED_RECHECK
                ex["disallowed"] = ex.get("disallowed", 0) + 1
                s.commit()
                continue
            except FetchBlocked as e:  # 401/403/451 from the server: STOP the whole run, never work around it
                crawl.last_outcome, crawl.last_outcome_detail = "blocked", str(e)[:200]
                status = "blocked"
                errors.append(str(e))
                s.commit()
                break
            except FetchGone:
                # Two-step removal: a single 404/410 may be a transient CDN/maintenance error. Only a SECOND consecutive
                # gone answer (from a later run) marks the URL gone, and only the listing's own canonical URL can remove it.
                was_gone = crawl.last_status in (404, 410)
                crawl.last_status, crawl.last_fetched_at = 404, now_fn()
                crawl.last_outcome, crawl.last_outcome_detail = "gone", "404/410 again: confirmed" if was_gone else "404/410: first answer, re-checked soon"
                if was_gone:
                    crawl.gone = True
                    lst = s.get(Listing, crawl.listing_id) if crawl.listing_id else None
                    if lst and lst.active and normalise_url(lst.url) == crawl.url:
                        lst.active = False
                        _event(s, lst, run, "removed", now=now_fn())
                else:
                    crawl.next_check_at = now_fn() + timedelta(hours=2)
                s.commit()
                continue
            except FetchError as e:
                crawl.last_outcome, crawl.last_outcome_detail = "fetch_error", str(e)[:200]
                crawl.fail_count += 1
                crawl.next_check_at = now_fn() + _backoff(crawl.fail_count)
                run.pages_failed = (run.pages_failed or 0) + 1
                ex["failed"] += 1
                errors.append(str(e)[:160])
                s.commit()
                continue
            now = now_fn()
            run.pages_fetched += 1
            nbytes += res.nbytes
            crawl.last_fetched_at, crawl.last_status, crawl.fail_count = now, res.status, 0
            crawl.next_check_at = now + timedelta(hours=adapter.check_interval_hours)
            lst = s.get(Listing, crawl.listing_id) if crawl.listing_id else None
            if res.status == 304:
                crawl.last_outcome, crawl.last_outcome_detail = "not_modified", None
                run.items_seen += 1
                if lst is not None and reconfirm(s, lst, run, now):
                    ex["not_modified"] += 1
                    run.pages_unchanged = (run.pages_unchanged or 0) + 1
                s.commit()
                continue
            try:
                raw = adapter.parse_product(res.text, crawl.url, now)
            except Exception as e:  # odd markup must not kill the run
                crawl.last_outcome, crawl.last_outcome_detail = "parse_error", type(e).__name__
                ex["failed"] += 1
                run.pages_failed = (run.pages_failed or 0) + 1
                errors.append(f"{crawl.url}: parse error {type(e).__name__}"[:160])
                s.commit()
                continue
            if raw is None:  # not a product page (category, info page, soft-block): remember, do not refetch for a month
                ex["no_product"] += 1
                crawl.last_outcome, crawl.last_outcome_detail = "no_product", "no schema.org Product data on the page"
                crawl.next_check_at = now + NON_PRODUCT_RECHECK
                s.commit()
                continue
            run.items_seen += 1
            raw.http_status = res.status
            if not _listing_url_ok(raw.url, hosts):
                run.items_invalid += 1
                crawl.last_outcome, crawl.last_outcome_detail = "ingest_invalid", "listing URL outside allowed hosts"
                errors.append(f"{raw.external_id}: listing URL outside allowed hosts")
                s.commit()
                continue
            listing, is_new, problems = ingest_listing(s, src, run, raw, now)
            if listing is None:
                run.items_invalid += 1
                crawl.last_outcome, crawl.last_outcome_detail = "ingest_invalid", "; ".join(problems)[:200]
                errors.append(f"{raw.external_id or '?'}: {'; '.join(problems)}"[:160])
            else:
                # Alias URL (a second URL for a listing we already track under another URL): keep the first URL as
                # the listing's address, and never let the alias's own 404 remove the listing.
                other = s.scalar(select(CrawlUrl).where(CrawlUrl.source_id == src.id, CrawlUrl.listing_id == listing.id,
                                                        CrawlUrl.id != crawl.id, CrawlUrl.gone.is_(False)))
                if other is not None and other.url != crawl.url:
                    listing.url = other.url
                    crawl.listing_id = None
                    crawl.next_check_at = now + NON_PRODUCT_RECHECK
                    crawl.last_outcome, crawl.last_outcome_detail = "alias", f"second URL of listing #{listing.id}"
                else:
                    crawl.listing_id = listing.id
                    crawl.last_outcome, crawl.last_outcome_detail = "ok", raw.extraction_method
                crawl.content_hash = raw.content_hash
                crawl.etag, crawl.last_modified = res.etag or crawl.etag, res.last_modified or crawl.last_modified
                run.items_new += int(is_new)
                ex[raw.extraction_method or "jsonld"] += 1
                if problems:
                    run.items_invalid += 1
            s.commit()
        run.bytes_fetched = nbytes
        demote_ambiguous(s, src.id)
        if run.pages_fetched == 0 and status != "blocked":
            status = "ok" if not (recheck or fresh) else ("failed" if errors else "ok")  # nothing due is a healthy no-op
        elif errors and status == "ok":
            status = "partial"
        if ex.get("disallowed") and run.pages_fetched == 0:  # a run that read nothing because robots said no is not "healthy"
            errors.append(f"{ex['disallowed']} URL(s) disallowed by robots.txt; nothing fetched")
            if status == "ok":
                status = "failed"
        # A sampled/prioritised crawl is NEVER complete, so absence never implies removal.
        _finish(s, run, src, status, False, errors, now_fn, t0, ex)
        log.info("run %s %s: %s pages, %s new listings, %s unchanged", adapter.key, run.status, run.pages_fetched,
                 run.items_new, run.pages_unchanged or 0)
