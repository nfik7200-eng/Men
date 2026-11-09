"""Server-side SVG price-history chart (no JS dependency). Step line: a price holds until it changes."""
from datetime import datetime
from html import escape


def price_chart(points: list[tuple[datetime, int]], currency: str, w: int = 640, h: int = 220) -> str:
    if len(points) < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 58, 14, 14, 30
    t0, t1 = points[0][0], points[-1][0]
    span = max((t1 - t0).total_seconds(), 1)
    lo, hi = min(p for _, p in points), max(p for _, p in points)
    lo_p, hi_p = lo, hi
    flat = lo == hi
    if flat:  # geometry only: the labels below never show an invented range
        lo, hi = lo - 1, hi + 1
    X = lambda t: pad_l + (t - t0).total_seconds() / span * (w - pad_l - pad_r)
    Y = lambda p: pad_t + (1 - (p - lo) / (hi - lo)) * (h - pad_t - pad_b)
    path, prev = [], None
    for t, p in points:
        if prev is None:
            path.append(f"M{X(t):.1f},{Y(p):.1f}")
        else:  # hold the previous price until this observation, then step
            path.append(f"L{X(t):.1f},{Y(prev):.1f} L{X(t):.1f},{Y(p):.1f}")
        prev = p
    summary = (f"Price history: constant {lo_p/100:,.2f} {currency} across {len(points)} observations" if flat else
               f"Price history: from {points[0][1]/100:,.2f} to {points[-1][1]/100:,.2f} {currency}, low {lo_p/100:,.2f}, high {hi_p/100:,.2f}, {len(points)} observations")
    dots = "".join(f'<circle cx="{X(t):.1f}" cy="{Y(p):.1f}" r="3.5" class="dot"><title>{t:%Y-%m-%d %H:%M} UTC: {p/100:,.2f} {escape(currency)}</title></circle>' for t, p in points)
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{escape(summary)}" class="chart">'
            f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{h-pad_b}" class="axis"/>'
            f'<line x1="{pad_l}" y1="{h-pad_b}" x2="{w-pad_r}" y2="{h-pad_b}" class="axis"/>'
            + ((f'<text x="{pad_l-6}" y="{(h-pad_b+pad_t)/2+4:.0f}" class="lbl" text-anchor="end">{lo_p/100:,.2f}</text>'
                f'<text x="{pad_l+6}" y="{pad_t+12}" class="lbl">unchanged</text>') if flat else
               (f'<text x="{pad_l-6}" y="{pad_t+4}" class="lbl" text-anchor="end">{hi_p/100:,.2f}</text>'
                f'<text x="{pad_l-6}" y="{h-pad_b}" class="lbl" text-anchor="end">{lo_p/100:,.2f}</text>')) +
            f'<text x="{pad_l}" y="{h-8}" class="lbl">{t0:%Y-%m-%d}</text>'
            f'<text x="{w-pad_r}" y="{h-8}" class="lbl" text-anchor="end">{t1:%Y-%m-%d}</text>'
            f'<path d="{" ".join(path)}" class="line"/>{dots}</svg>')
