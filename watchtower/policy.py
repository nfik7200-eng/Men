"""Compliance gate. Non-demo, non-sandbox sources may only be collected while their SourcePolicy is 'approved',
the review is current, robots.txt still allows every path class we need, and the crawler identifies itself with a
real contact. 'approved' means "no prohibition found at review time" — it is NOT legal advice or a legal permission.
See docs/SOURCE_DISCOVERY.md for the rubric and each source's findings."""
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import select

from . import config
from .db import Database
from .fetcher import FetchError
from .models import Source, SourcePolicy, utcnow

POLICY_FILE = config.ROOT / "data" / "source_policies.json"
REVIEW_VALID_DAYS = 90


class SourceNotApproved(Exception):
    pass


def text_sha(html: str) -> str:
    """Hash of the visible main text (whitespace-normalised): detects changed terms without false alarms from markup."""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    main = soup.select_one("main") or soup.select_one("#MainContent") or soup.body or soup
    return hashlib.sha256(re.sub(r"\s+", " ", main.get_text(" ")).strip().encode()).hexdigest()


def ensure_policies(db: Database, path: Path | None = None) -> int:
    """Load reviewed policies from the versioned data file. A runtime downgrade (e.g. robots now disallows) made
    AFTER the file's review date is never overwritten by the file."""
    p = path or POLICY_FILE
    if not p.exists():
        return 0
    data = json.loads(p.read_text()).get("policies", {})
    n = 0
    with db.session() as s:
        for key, d in data.items():
            src = s.scalar(select(Source).where(Source.key == key))
            if src is None:
                continue
            row = s.scalar(select(SourcePolicy).where(SourcePolicy.source_id == src.id))
            reviewed = datetime.fromisoformat(d["reviewed_at"]) if d.get("reviewed_at") else None
            if reviewed is None:  # no review date => no approval, and nothing that could "never expire"
                d = {**d, "status": "unverified", "notes": f"{d.get('notes', '')} | policy entry has no reviewed_at: treated as unverified"}
            if row and row.reviewed_at and reviewed and row.reviewed_at > reviewed:
                continue
            row = row or SourcePolicy(source_id=src.id)
            s.add(row)
            for f in ("status", "robots_sha", "required_paths", "tos_url", "tos_sha", "tos_finding", "crawl_delay_s", "notes", "reviewed_by"):
                if f in d:
                    setattr(row, f, d[f])
            row.reviewed_at = reviewed
            row.review_due_at = (reviewed + timedelta(days=REVIEW_VALID_DAYS)) if reviewed else datetime(1970, 1, 1)
            n += 1
    return n


def gate(db: Database, adapter, fetcher, now: datetime | None = None) -> None:
    """Raise SourceNotApproved unless this source may be collected right now."""
    if adapter.kind in ("demo", "sandbox"):
        return  # practice sites / API sandboxes: no commercial data, no gate (still labelled everywhere)
    now = now or utcnow()
    if not config.contact_ok():
        raise SourceNotApproved("WATCHTOWER_CONTACT must be a real contact (email or URL, not a placeholder like example.com) "
                                "before crawling live sources")
    with db.session() as s:
        src = s.scalar(select(Source).where(Source.key == adapter.key))
        pol = s.scalar(select(SourcePolicy).where(SourcePolicy.source_id == src.id)) if src else None
        if pol is None or pol.status != "approved":
            raise SourceNotApproved(f"{adapter.key}: policy status is {pol.status if pol else 'missing'}; only 'approved' sources run")
        if pol.review_due_at and pol.review_due_at < now:
            raise SourceNotApproved(f"{adapter.key}: policy review expired {pol.review_due_at:%Y-%m-%d}; re-review robots.txt and terms")
        info = getattr(fetcher, "robots_info", None)
        if info is None:
            return
        try:
            sha, delay = info(adapter.base_url)
            unreliable = getattr(fetcher, "robots_unreliable", lambda u: False)(adapter.base_url)
        except FetchError as e:  # network trouble is not a policy decision: stop without changing the stored status
            raise SourceNotApproved(f"{adapter.key}: robots.txt could not be read ({e}); not run, policy unchanged") from e
        if unreliable:
            raise SourceNotApproved(f"{adapter.key}: robots.txt is temporarily unreadable (auth error / persistent 5xx); "
                                    "not run, policy unchanged")
        urls = adapter.required_urls() if hasattr(adapter, "required_urls") else []
        blocked = [u for u in urls if not fetcher.allowed(u)]
        if blocked:
            pol.status = "rejected"
            pol.notes = f"{(pol.notes or '')} | AUTO-REJECTED {now:%Y-%m-%d}: robots.txt now disallows {blocked[0]}"[:2000]
            # This is a NEWER decision than the versioned file: the file must never silently re-approve the source.
            pol.reviewed_at, pol.reviewed_by = now, "automatic robots.txt re-check"
            s.commit()
            raise SourceNotApproved(f"{adapter.key}: robots.txt now disallows {blocked[0]}; source rejected")
        if pol.robots_sha and sha != pol.robots_sha:
            # robots.txt changed but still allows what we need: keep running, but shorten the review fuse to a week
            pol.notes = f"{(pol.notes or '')} | robots.txt changed {now:%Y-%m-%d} (still allows required paths); re-review within 7 days"[:2000]
            pol.robots_sha = sha
            pol.review_due_at = min(pol.review_due_at or now + timedelta(days=7), now + timedelta(days=7))
            # an automatic decision is NEWER than the versioned file: only a human re-review (newer reviewed_at in the file) overrides it
            pol.reviewed_at, pol.reviewed_by = now, "automatic robots.txt re-check (needs human re-review)"
        if delay:
            pol.crawl_delay_s = delay
