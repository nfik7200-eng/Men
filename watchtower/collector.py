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
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select

from . import policy
from .db import Database
from .fetcher import FetchBlocked, FetchError, FetchGone, Fetcher
from .ingest import _event, ingest_listing, mark_removed, reconfirm
from .models import CollectionRun, CrawlUrl, Event, Listing, Source, utcnow
from .policy import SourceNotApproved, ensure_policies
from .sources import ADAPTERS
from .structured import normalise_url

log = logging.getLogger("watchtower.collector")
ABANDON_AFTER = timedelta(minutes=45)
NON_PRODUCT_RECHECK = timedelta(days=30)


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
    cap = max_pages if max_pages is not None else adapter.default_max_pages
    ensure_sources(db)
    ensure_policies(db)
    with db.session() as s:
        src = s.query(Source).filter_by(key=key).one()
        if not src.enabled:
            raise SourceDisabled(f"source {key} is disabled")
    ready, missing = adapter.configured()
    if not ready:
        raise SourceNotConfigured(f"{key} is not configured; missing: {', '.join(missing)}")
    fetcher = fetcher or Fetcher(allowed_hosts=adapter.allowed_hosts(), respect_robots=not adapter.official_api)
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
    return timedelta(hours=min(6 * 2 ** max(fail_count - 1, 0), 24 * 7))


def _crawl_products(db, adapter, fetcher, cap, run_id, src_id, now_fn):
    t0 = time.monotonic()
    with db.session() as s:
        run, src = s.get(CollectionRun, run_id), s.get(Source, src_id)
        errors, status, nbytes = [], "ok", 0
        ex = {"jsonld": 0, "microdata": 0, "css": 0, "not_modified": 0, "no_product": 0, "failed": 0}
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
        # 2. due URLs: never-fetched first, then oldest-checked; this run's budget is `cap` product pages
        due = [c for c in known.values() if c.url in discovered and not c.gone and (c.next_check_at is None or c.next_check_at <= now)]
        due.sort(key=lambda c: (c.last_fetched_at is not None, c.last_fetched_at or now))
        for crawl in due[:cap]:
            try:
                res = fetcher.get_ex(crawl.url, etag=crawl.etag, last_modified=crawl.last_modified)
            except FetchBlocked as e:
                status = "blocked"
                errors.append(str(e))
                break
            except FetchGone:
                crawl.gone, crawl.last_status, crawl.last_fetched_at = True, 404, now_fn()
                lst = s.get(Listing, crawl.listing_id) if crawl.listing_id else None
                if lst and lst.active:
                    lst.active = False
                    _event(s, lst, run, "removed", now=now_fn())
                s.commit()
                continue
            except FetchError as e:
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
                run.items_seen += 1
                if lst is not None and reconfirm(s, lst, run, now):
                    ex["not_modified"] += 1
                    run.pages_unchanged = (run.pages_unchanged or 0) + 1
                s.commit()
                continue
            crawl.etag, crawl.last_modified = res.etag or crawl.etag, res.last_modified or crawl.last_modified
            try:
                raw = adapter.parse_product(res.text, crawl.url, now)
            except Exception as e:  # odd markup must not kill the run
                ex["failed"] += 1
                run.pages_failed = (run.pages_failed or 0) + 1
                errors.append(f"{crawl.url}: parse error {type(e).__name__}"[:160])
                s.commit()
                continue
            if raw is None:  # not a product page (category, info page, soft-block): remember, do not refetch for a month
                ex["no_product"] += 1
                crawl.next_check_at = now + NON_PRODUCT_RECHECK
                s.commit()
                continue
            run.items_seen += 1
            raw.http_status = res.status
            if not _listing_url_ok(raw.url, hosts):
                run.items_invalid += 1
                errors.append(f"{raw.external_id}: listing URL outside allowed hosts")
                s.commit()
                continue
            listing, is_new, problems = ingest_listing(s, src, run, raw, now)
            if listing is None:
                run.items_invalid += 1
                errors.append(f"{raw.external_id or '?'}: {'; '.join(problems)}"[:160])
            else:
                crawl.listing_id, crawl.content_hash = listing.id, raw.content_hash
                run.items_new += int(is_new)
                ex[raw.extraction_method or "jsonld"] += 1
                if problems:
                    run.items_invalid += 1
            s.commit()
        run.bytes_fetched = nbytes
        if run.pages_fetched == 0 and status != "blocked":
            status = "ok" if not due else ("failed" if errors else "ok")  # nothing due is a healthy no-op
        elif errors and status == "ok":
            status = "partial"
        # A sampled/prioritised crawl is NEVER complete, so absence never implies removal.
        _finish(s, run, src, status, False, errors, now_fn, t0, ex)
        log.info("run %s %s: %s pages, %s new listings, %s unchanged", adapter.key, run.status, run.pages_fetched,
                 run.items_new, run.pages_unchanged or 0)
