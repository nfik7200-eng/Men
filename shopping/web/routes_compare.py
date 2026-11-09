"""Comparison page: chosen listings side by side. Moved out of the create_app route closure in shopping/web/app.py; the
behaviour is unchanged. Each price stays in its listing's own currency."""
from typing import Annotated

from fastapi import Depends, Query, Request

from watchtower.models import Listing


def register_compare(app, *, get_s, render, banner) -> None:
    # comparison: chosen listings side by side (each price in its own currency)
    MATCH_LABELS = {"self": "Own product", "auto": "Automatic match", "confirmed": "Confirmed match", "review": "Awaiting review"}

    @app.get("/compare")
    def compare_page(request: Request, ids: Annotated[str, Query(max_length=200)] = "", s=Depends(get_s)):
        """Up to eight listings side by side. Prices stay in each listing's own currency: when the listings use more than
        one currency they are shown, never ranked against each other."""
        wanted: list[int] = []
        for part in ids.split(","):
            part = part.strip()
            if part.isdigit() and int(part) not in wanted and len(wanted) < 8:
                wanted.append(int(part))
        rows = [l for l in (s.get(Listing, i) for i in wanted) if l is not None]
        currencies = sorted({l.currency for l in rows})
        spec_keys = sorted({k for l in rows for k in (l.specs or {})})
        return render(request, "compare.html", rows=rows, currencies=currencies, spec_keys=spec_keys,
                      labels=MATCH_LABELS, banner=banner(s))

