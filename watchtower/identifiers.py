"""Identifier hygiene. A wrong identifier is worse than none: it silently merges different products."""
import re


def normalise_gtin(value) -> str | None:
    """Digits only, GS1 mod-10 check digit validated, zero-padded to 14. Rejects placeholders and malformed codes."""
    if value is None:
        return None
    d = re.sub(r"\D", "", str(value))
    if len(d) not in (8, 12, 13, 14):
        return None
    if len(set(d)) == 1:  # 00000000, 11111111... placeholder codes
        return None
    body, check = d[:-1], int(d[-1])
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(body)))
    if (10 - total % 10) % 10 != check:
        return None
    g = d.zfill(14)
    ean13 = g[1:] if g[0] == "0" else None
    if ean13 and (ean13[0] == "2" or ean13[:2] in ("02", "04")):
        # GS1 restricted-circulation prefixes (EAN 2x, UPC-A 2/4 in-store & variable-weight) name a merchant's own
        # item, not a globally unique product
        return None
    return g


def clean_mpn(mpn, sku=None) -> str | None:
    """A manufacturer part number must differ from the merchant's own SKU and look like a code."""
    if mpn is None:
        return None
    m = str(mpn).strip()
    if len(re.sub(r"[^0-9A-Za-z]", "", m)) < 4:
        return None
    if sku is not None and re.sub(r"[^0-9a-z]", "", m.lower()) == re.sub(r"[^0-9a-z]", "", str(sku).lower()):
        return None  # merchant copies its SKU into the mpn field (seen on sparkfun.com): not a manufacturer identifier
    return m
