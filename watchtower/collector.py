"""Runs one source adapter through the polite fetcher into the database, with run bookkeeping.

Integrity rules (see docs/AUDIT_REPORT.md): a run is 'complete' only if EVERY start URL parsed >=1 item and no
page failed; removals are inferred only after a complete run and never in bulk (removal guard); any unexpected
exception marks the run failed; disabled sources are never fetched; only one live run per source.
"""
import logging
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select

from .db import Database
from .fetcher import FetchBlocked, FetchError, Fetcher
from .ingest import ingest_listing, mark_removed
from .models import CollectionRun, Event, Source, utcnow
from .sources import ADAPTERS

log = logging.getLogger("watchtower.collector")
ABANDON_AFTER = timedelta(minutes=45)


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
    with db.session() as s:
        src = s.query(Source).filter_by(key=key).one()
        if not src.enabled:
            raise SourceDisabled(f"source {key} is disabled")
        ready, missing = adapter.configured()
        if not ready:
            raise SourceNotConfigured(f"{key} is not configured; missing: {', '.join(missing)}")
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
    fetcher = fetcher or Fetcher(allowed_hosts=adapter.allowed_hosts(), respect_robots=not adapter.official_api)
    try:
        _crawl(db, adapter, fetcher, cap, run_id, src_id, now_fn)
    except Exception as e:  # unexpected: never leave a run stuck in 'running'
        with db.session() as s:
            run, src = s.get(CollectionRun, run_id), s.get(Source, src_id)
            run.status, run.complete, run.finished_at = "failed", False, now_fn()
            run.error = f"{type(e).__name__}: {e}"[:500]
            src.last_run_at, src.last_status, src.last_error = run.finished_at, "failed", run.error
        log.exception("run %s crashed", key)
        raise
    return run_id


def _crawl(db, adapter, fetcher, cap, run_id, src_id, now_fn):
    with db.session() as s:
        run, src = s.get(CollectionRun, run_id), s.get(Source, src_id)
        complete, errors, status = True, [], "ok"
        hosts = adapter.listing_hosts()
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
                items, nxt = adapter.parse_page(html, url)
                for msg in adapter.parse_errors:
                    run.items_seen += 1
                    run.items_invalid += 1
                    errors.append(msg)
                now = now_fn()
                for raw in items:
                    run.items_seen += 1
                    if not _listing_url_ok(raw.url, hosts):
                        run.items_invalid += 1
                        errors.append(f"{raw.external_id or '?'}: listing URL outside allowed hosts")
                        continue
                    listing, is_new, problems = ingest_listing(s, src, run, raw, now)
                    if listing is None:
                        run.items_invalid += 1
                        errors.append(f"{raw.external_id or '?'}: {'; '.join(problems)}")
                        continue
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
        if run.items_seen == 0 and status != "blocked":
            status = "failed"
        elif errors and status == "ok":
            status = "partial"
        run.status, run.complete, run.finished_at = status, complete and status == "ok", now_fn()
        if run.complete:
            _, guard = mark_removed(s, src, run, run.finished_at)
            if guard:
                errors.append(guard)
                run.status, run.complete = "partial", False
        run.price_changes = s.scalar(select(func.count()).select_from(Event).where(
            Event.run_id == run.id, Event.kind.in_(("price_drop", "price_rise")))) or 0
        run.error = "; ".join(errors[:5]) or None
        src.last_run_at, src.last_status, src.last_error = run.finished_at, run.status, run.error
        if run.status in ("ok", "partial"):
            src.last_success_at = run.finished_at
        s.commit()
        log.info("run %s %s: %s items, %s new, %s pages", adapter.key, run.status, run.items_seen, run.items_new, run.pages_fetched)
