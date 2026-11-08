import re
from decimal import Decimal, InvalidOperation
from .currency import minor_digits

SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR"}
_SYM = {"USD": "$", "GBP": "£", "EUR": "€"}


def parse_money(text: str | None, default_currency: str | None = None) -> tuple[int, str] | None:
    """'$1,299.00' -> (129900, 'USD'). Returns None if no positive amount can be read."""
    if not text:
        return None
    m = re.search(r"([$£€]|[A-Z]{3})?\s*([0-9][0-9,]*(?:\.[0-9]{1,3})?)", text.replace("\xa0", " ").replace("Â", ""))
    if not m:
        return None
    cur = SYMBOLS.get(m.group(1) or "", m.group(1)) or default_currency
    if not cur:
        return None
    amount = m.group(2).replace(",", "")
    # More decimals than the currency has (e.g. "USD 12.345"): refuse rather than silently drop or round a digit.
    if len(amount.partition(".")[2]) > minor_digits(cur):
        return None
    try:
        minor = int((Decimal(amount) * (Decimal(10) ** minor_digits(cur))).to_integral_value())
    except InvalidOperation:
        return None
    return (minor, cur) if minor > 0 else None


def fmt_money(minor: int | None, currency: str | None) -> str:
    if minor is None:
        return "Price unavailable"
    sym = _SYM.get(currency or "", (currency or "") + " ")
    digits = minor_digits(currency or "")
    return f"{sym}{minor / 10 ** digits:,.{digits}f}"
