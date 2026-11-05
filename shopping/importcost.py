"""Import / landed-cost estimator. Always an ESTIMATE.

Rates come from data/import_rules.json. The shipped DEFAULT row is a PLACEHOLDER ASSUMPTION, not verified
tax law — replace/extend per country after checking the destination's customs authority.
Shipping is never guessed: if the user does not supply it the total is marked partial.
No FX conversion is performed (no sourced rate feed yet); everything stays in the listing currency.
"""
import json
from pathlib import Path

from watchtower import config


def load_rules(path: str | None = None) -> dict:
    p = Path(path or config.IMPORT_RULES)
    return json.loads(p.read_text()) if p.exists() else {}


def estimate(price_minor: int | None, currency: str, destination: str | None = None,
             shipping_minor: int | None = None, rules: dict | None = None) -> dict:
    if price_minor is None:
        return {"available": False, "reason": "Item price unavailable — cannot estimate."}
    rules = load_rules() if rules is None else rules
    code = (destination or "").upper()
    rule = rules.get(code) or rules.get("DEFAULT")
    if not rule:
        return {"available": False, "reason": "No import rules configured."}
    rule_name = code if code in rules else "DEFAULT"
    lines = [{"label": "Item price (as listed)", "amount_minor": price_minor, "evidence": "verified"}]
    if shipping_minor is None:
        lines.append({"label": "International shipping", "amount_minor": None, "evidence": "missing"})
    else:
        lines.append({"label": "International shipping (you supplied)", "amount_minor": shipping_minor, "evidence": "user-provided"})
    cif = price_minor + (shipping_minor or 0)
    duty = round(cif * rule["duty_pct"] / 100)
    vat = round((cif + duty) * rule["vat_pct"] / 100)
    verified = bool(rule.get("verified", False))
    ev = "estimated" if verified else "estimated (assumed rate, unverified)"
    lines.append({"label": f"Import duty @ {rule['duty_pct']}% of price+shipping", "amount_minor": duty, "evidence": ev})
    lines.append({"label": f"VAT/sales tax @ {rule['vat_pct']}% of price+shipping+duty", "amount_minor": vat, "evidence": ev})
    total = cif + duty + vat
    return {
        "available": True, "currency": currency, "lines": lines, "total_minor": total,
        "partial": shipping_minor is None, "rule": rule_name, "rule_verified": verified,
        "notes": [rule.get("note", ""), "Excludes courier/handling fees, insurance, excise and local delivery.",
                  "No currency conversion applied."] + (["Shipping unknown: total is a lower bound."] if shipping_minor is None else []),
    }
