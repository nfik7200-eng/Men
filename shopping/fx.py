"""Sourced exchange rates. We never invent a rate and never fetch one silently: every rate is recorded with who
published it and the date it is valid for. No triangulation (USD->X->Y) — a missing pair is reported as missing.
Rates older than `max_age_days` are still returned but flagged stale so the UI can say so."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchtower.models import FxRate, utcnow


@dataclass
class FxQuote:
    base: str
    quote: str
    rate: Decimal  # 1 base = rate quote
    as_of: datetime
    source: str
    source_url: str | None
    stale: bool
    inverted: bool = False


def record_rate(s: Session, base: str, quote: str, rate, as_of: datetime, source: str, source_url: str | None = None) -> FxRate:
    base, quote = base.upper().strip(), quote.upper().strip()
    if len(base) != 3 or len(quote) != 3 or base == quote:
        raise ValueError("base/quote must be two different 3-letter currency codes")
    try:
        r = Decimal(str(rate))
    except InvalidOperation as e:
        raise ValueError("rate is not a number") from e
    if not r.is_finite() or r <= 0:
        raise ValueError("rate must be positive and finite")
    if not (source or "").strip():
        raise ValueError("a rate without a named source is not accepted")
    row = FxRate(base=base, quote=quote, rate=str(r), as_of=as_of, source=source.strip()[:200], source_url=source_url)
    s.add(row)
    s.flush()
    return row


def get_rate(s: Session, base: str, quote: str, now: datetime | None = None, max_age_days: int = 7) -> FxQuote | None:
    base, quote, now = base.upper(), quote.upper(), now or utcnow()
    if base == quote:
        return FxQuote(base, quote, Decimal(1), now, "identity", None, False)
    for b, q, inv in ((base, quote, False), (quote, base, True)):
        row = s.scalar(select(FxRate).where(FxRate.base == b, FxRate.quote == q, FxRate.as_of <= now)
                       .order_by(FxRate.as_of.desc(), FxRate.id.desc()).limit(1))
        if row:
            rate = Decimal(row.rate)
            return FxQuote(base, quote, (Decimal(1) / rate) if inv else rate, row.as_of, row.source, row.source_url,
                           now - row.as_of > timedelta(days=max_age_days), inv)
    return None


def convert(s: Session, minor: int, base: str, quote: str, now: datetime | None = None, max_age_days: int = 7) -> dict | None:
    q = get_rate(s, base, quote, now, max_age_days)
    if q is None:
        return None
    return {"minor": int((Decimal(minor) * q.rate).to_integral_value()), "currency": quote.upper(), "rate": str(q.rate),
            "as_of": q.as_of, "source": q.source, "source_url": q.source_url, "stale": q.stale, "inverted": q.inverted}
