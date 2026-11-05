"""Runs one source adapter through the polite fetcher into the database, with run bookkeeping."""
import logging
from datetime import datetime

from .db import Database
from .fetcher import FetchBlocked, FetchError, Fetcher
from .ingest import ingest_listing, mark_removed
from .models import CollectionRun, Source, utcnow
from .sources import ADAPTERS

log = logging.getLogger("watchtower.collector")


def ensure_sources(db: Database) -> None:
    with db.session() as s:
        for a in ADAPTERS.values():
            src = s.query(Source).filter_by(key=a.key).one_or_none()
            if src is None:
                src = Source(key=a.key)
                s.add(src)
            src.name, src.base_url, src.country, src.currency = a.name, a.base_url, a.country, a.currency
            src.kind, src.terms_note, src.stale_after_hours = a.kind, a.terms_note, a.stale_after_hours


def run_source(db: Database, key: str, fetcher: Fetcher | None = None, max_pages: int | None = None,
               now_fn=utcnow) -> int:
    adapter = ADAPTERS[key]
    fetcher = fetcher or Fetcher()
    cap = max_pages if max_pages is not None else adapter.default_max_pages
    ensure_sources(db)
    with db.session() as s:
        src = s.query(Source).filter_by(key=key).one()
        run = CollectionRun(source_id=src.id, started_at=now_fn())
        s.add(run)
        s.commit()
        complete, errors, status = True, [], "ok"
        for start in adapter.start_urls():
            url, pages = start, 0
            while url:
                if pages >= cap:
                    complete = False  # capped: cannot infer removals
                    break
                try:
                    html = fetcher.get(url)
                except FetchBlocked as e:
                    status, complete = "blocked", False
                    errors.append(str(e))
                    url = None
                    break
                except FetchError as e:
                    errors.append(str(e))
                    complete = False
                    break
                pages += 1
                run.pages_fetched += 1
                items, nxt = adapter.parse_page(html, url)
                now = now_fn()
                for raw in items:
                    run.items_seen += 1
                    listing, is_new, problems = ingest_listing(s, src, run, raw, now)
                    if listing is None:
                        run.items_invalid += 1
                        errors.append(f"{raw.external_id or '?'}: {'; '.join(problems)}")
                        continue
                    run.items_new += int(is_new)
                    if problems:
                        run.items_invalid += 1
                s.commit()
                url = nxt
            if status == "blocked":
                break
        if run.items_seen == 0 and status != "blocked":
            status = "failed"
        elif errors and status == "ok":
            status = "partial"
        run.status, run.complete, run.finished_at = status, complete and status == "ok", now_fn()
        if run.complete:
            mark_removed(s, src, run, run.finished_at)
        from sqlalchemy import func, select
        from .models import Event
        run.price_changes = s.scalar(select(func.count()).select_from(Event).where(
            Event.run_id == run.id, Event.kind.in_(("price_drop", "price_rise")))) or 0
        run.error = "; ".join(errors[:5]) or None
        src.last_run_at, src.last_status, src.last_error = run.finished_at, status, run.error
        if status in ("ok", "partial"):
            src.last_success_at = run.finished_at
        s.commit()
        log.info("run %s %s: %s items, %s new, %s pages", key, status, run.items_seen, run.items_new, run.pages_fetched)
        return run.id
