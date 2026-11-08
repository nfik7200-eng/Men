"""ISO 4217 minor-unit exponents. Money is stored as integer minor units; converting a user's major-unit amount
(e.g. a price bound typed as "1000") must use the currency's own exponent, never a global x100."""
from decimal import ROUND_HALF_EVEN, Decimal

# Exponent 0: the amount is already whole units. Exponent 3: thousandths. Anything not listed is 2.
ZERO_DECIMAL = frozenset({"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"})
THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})


def minor_digits(code: str) -> int:
    code = (code or "").upper()
    if code in ZERO_DECIMAL:
        return 0
    if code in THREE_DECIMAL:
        return 3
    return 2


def to_minor(amount: float, code: str) -> int:
    """Major units -> integer minor units, rounded half-to-even on the exact decimal value."""
    scaled = Decimal(str(amount)) * (Decimal(10) ** minor_digits(code))
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
