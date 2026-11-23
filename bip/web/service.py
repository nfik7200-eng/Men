"""Fetch the owner's configured RSS/Atom feeds and store new items with provenance.

One refresh fetches each configured feed once through :class:`~bip.acquisition.fetcher.Fetcher`
(robots.txt, per-domain rate, conditional GET with validators kept in ``web_feed_state``),
parses it, and inserts items not stored before (keyed by guid, else link, else title). Failures
are recorded per feed and never stored as data. A refresh holds a lock so two never overlap.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, update

from bip.acquisition.feeds import FeedItem, FeedParseError, parse_feed
from bip.acquisition.fetcher import USER_AGENT, Fetcher, Transport
from bip.config.settings import WebSettings
from bip.db import schema
from bip.db.repository import Repository

EVIDENCE_CLASS = "verified_observation"
"""That the feed published the item is observed; its content is not verified."""


def _cut(value: str | None, n: int) -> str | None:
    return value[:n] if value else value


def _item_key(item: FeedItem) -> str | None:
    basis = item.guid or item.link or item.title
    return hashlib.sha256(basis.encode()).hexdigest() if basis else None


class FeedBusyError(RuntimeError):
    pass


class FeedService:
    def __init__(self, repo: Repository, cfg: WebSettings, transport: Transport) -> None:
        self.repo = repo
        self.cfg = cfg
        ua = (
            USER_AGENT
            if not cfg.contact
            else f"{USER_AGENT.split(' (')[0]} (+robots-respecting; contact: {cfg.contact})"
        )
        self._fetcher = Fetcher(
            transport, default_rate=cfg.per_domain_rps, timeout=cfg.timeout_s, user_agent=ua
        )
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ refresh

    def refresh(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise FeedBusyError("a feed refresh is already running")
        try:
            return {"feeds": [self._refresh_one(url) for url in self.cfg.feeds]}
        finally:
            self._lock.release()

    def _state(self, url: str) -> dict[str, Any] | None:
        st = schema.web_feed_state
        with self.repo.transaction() as conn:
            row = conn.execute(select(st).where(st.c.url == url)).mappings().first()
        return dict(row) if row else None

    def _refresh_one(self, url: str) -> dict[str, Any]:
        state = self._state(url)
        validators = (state["etag"], state["last_modified"]) if state else None
        res = self._fetcher.fetch(url, provider_id="web_feed", validators=validators)
        now = datetime.now(UTC)
        outcome: str = res.outcome
        error = res.reason
        new = 0
        if res.outcome == "ok" and res.body is not None:
            try:
                items = parse_feed(res.body, url, res.provenance.request_id)
            except FeedParseError as exc:
                outcome, error = "invalid_feed", str(exc)[:200]
            else:
                new = self._store(url, items[: self.cfg.max_items_per_feed], res.provenance, now)
        self._save_state(url, state, res, outcome, error, now)
        return {"url": url, "outcome": outcome, "new_items": new, "error": error}

    def _store(self, url: str, items: list[FeedItem], prov: Any, now: datetime) -> int:
        t = schema.web_feed_items
        new = 0
        with self.repo.transaction() as conn:
            for item in items:
                key = _item_key(item)
                if key is None:
                    continue  # nothing identifies it: skipped, never invented
                exists = conn.execute(
                    select(t.c.id).where(t.c.feed_url == url, t.c.item_key == key)
                ).first()
                if exists:
                    continue
                conn.execute(
                    insert(t).values(
                        feed_url=url,
                        item_key=key,
                        guid=_cut(item.guid, 500),
                        title=_cut(item.title, 500),
                        link=_cut(item.link, 1000),
                        published=_cut(item.published, 64),
                        author=_cut(item.author, 200),
                        request_id=prov.request_id,
                        content_sha256=prov.content_sha256 or "",
                        fetched_at=now,
                    )
                )
                new += 1
        return new

    def _save_state(
        self,
        url: str,
        state: dict[str, Any] | None,
        res: Any,
        outcome: str,
        error: str | None,
        now: datetime,
    ) -> None:
        st, t = schema.web_feed_state, schema.web_feed_items
        prov = res.provenance
        values: dict[str, Any] = {
            "last_fetch_at": now,
            "last_outcome": outcome,
            "last_error": _cut(error, 200),
            "last_request_id": prov.request_id,
        }
        if outcome == "ok":  # keep the validators of the version we actually stored
            values["etag"] = _cut(prov.etag, 200)
            values["last_modified"] = _cut(prov.last_modified, 100)
        with self.repo.transaction() as conn:
            values["items_stored"] = conn.execute(
                select(func.count()).select_from(t).where(t.c.feed_url == url)
            ).scalar_one()
            if state is None:
                conn.execute(insert(st).values(url=url, **values))
            else:
                conn.execute(update(st).where(st.c.url == url).values(**values))

    # ------------------------------------------------------------------ reads

    def feeds(self) -> dict[str, Any]:
        st = schema.web_feed_state
        with self.repo.transaction() as conn:
            rows = {r["url"]: dict(r) for r in conn.execute(select(st)).mappings()}
        items: list[dict[str, Any]] = []
        for url in self.cfg.feeds:
            r = rows.get(url, {})
            items.append(
                {
                    "url": url,
                    "last_fetch_at": r.get("last_fetch_at"),
                    "last_outcome": r.get("last_outcome"),
                    "last_error": r.get("last_error"),
                    "items_stored": r.get("items_stored", 0),
                }
            )
        return {"items": items, "contact_set": self.cfg.contact is not None}

    def items(self, *, feed: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        t = schema.web_feed_items
        where = [t.c.feed_url == feed] if feed else []
        with self.repo.transaction() as conn:
            total = conn.execute(select(func.count()).select_from(t).where(*where)).scalar_one()
            rows = conn.execute(
                select(t).where(*where).order_by(t.c.id.desc()).limit(limit).offset(offset)
            ).mappings()
            out = [
                {
                    "id": r["id"],
                    "feed_url": r["feed_url"],
                    "guid": r["guid"],
                    "title": r["title"],
                    "link": r["link"],
                    "published": r["published"],
                    "author": r["author"],
                    "request_id": r["request_id"],
                    "content_sha256": r["content_sha256"],
                    "fetched_at": r["fetched_at"],
                    "evidence_class": EVIDENCE_CLASS,
                }
                for r in rows
            ]
        return {"items": out, "total": total, "limit": limit, "offset": offset}
