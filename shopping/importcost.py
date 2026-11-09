"""Landed-cost estimator (item + shipping + insurance + duty + tax + handling). Always an ESTIMATE.

Every line carries an evidence label:  verified (stated by the source page) · quoted (a shipping quote on the
listing) · user-provided · assumed (a rule we have not verified) · missing (unknown — NOT zero).
Rules live in data/import_rules.json (schema in that file). The shipped DEFAULT row is a PLACEHOLDER ASSUMPTION,
not tax law; a row is only presented as verified when it says verified=true AND names its source + retrieval date.
Amounts stay in the listing currency. An optional destination-currency conversion is shown ONLY if a sourced,
dated exchange rate was supplied (see shopping/fx.py); otherwise nothing is converted.
Out of scope on purpose: product-specific tariff classification, excise, de-minimis edge cases, local delivery.
"""
import json
from pathlib import Path

from watchtower import config


def load_rules(path: str | None = None) -> dict:
    p = Path(path or config.IMPORT_RULES)
    return json.loads(p.read_text()) if p.exists() else {}


def _line(key, label, amount, evidence, basis=""):
    return {"key": key, "label": label, "amount_minor": amount, "evidence": evidence, "basis": basis}


def estimate(price_minor: int | None, currency: str, destination: str | None = None, shipping_minor: int | None = None,
             rules: dict | None = None, insurance_minor: int | None = None, handling_minor: int | None = None,
             shipping_evidence: str = "user-provided", fx=None) -> dict:
    if price_minor is None:
        return {"available": False, "reason": "Item price unavailable — cannot estimate."}
    rules = load_rules() if rules is None else rules
    code = (destination or "").upper()
    rule = rules.get(code) or rules.get("DEFAULT")
    if not rule or code.startswith("_"):
        return {"available": False, "reason": "No import rules configured."}
    rule_name = code if code in rules else "DEFAULT"
    verified = bool(rule.get("verified")) and bool(rule.get("source")) and bool(rule.get("retrieved"))
    rate_ev = "verified" if verified else "assumed"
    missing, assumptions = [], []

    lines = [_line("item", "Item price (as listed)", price_minor, "verified", "stated by the source page")]
    if shipping_minor is None:
        lines.append(_line("shipping", "International shipping", None, "missing", "no quote; none supplied"))
        missing.append("shipping")
    else:
        lines.append(_line("shipping", "International shipping", shipping_minor, shipping_evidence))
    ship = shipping_minor or 0

    if insurance_minor is not None:
        ins, ins_line = insurance_minor, _line("insurance", "Insurance", insurance_minor, "user-provided")
    elif rule.get("insurance_pct") is not None:
        ins = round((price_minor + ship) * rule["insurance_pct"] / 100)
        ins_line = _line("insurance", f"Insurance @ {rule['insurance_pct']}%", ins, rate_ev, "of item+shipping")
    else:
        ins, ins_line = 0, _line("insurance", "Insurance", None, "missing", "not included (optional)")
    lines.append(ins_line)

    cif = price_minor + ship + ins
    lines.append(_line("customs_value", "Customs value (item + shipping + insurance)", cif, "assumed",
                       "usual CIF basis; confirm locally"))
    de_min = rule.get("de_minimis_minor")
    if de_min is not None and cif <= de_min:
        duty = vat = 0
        lines.append(_line("duty", "Import duty", 0, rate_ev, f"under de-minimis threshold {de_min/100:.2f}"))
        lines.append(_line("vat", "VAT / sales tax", 0, rate_ev, "under de-minimis threshold"))
    else:
        duty = round(cif * rule["duty_pct"] / 100)
        vat = round((cif + duty) * rule["vat_pct"] / 100)
        lines.append(_line("duty", f"Import duty @ {rule['duty_pct']}%", duty, rate_ev, "of customs value"))
        lines.append(_line("vat", f"VAT / sales tax @ {rule['vat_pct']}%", vat, rate_ev, "of customs value + duty"))
    if handling_minor is not None:
        hnd, h_line = handling_minor, _line("handling", "Handling / clearance fees", handling_minor, "user-provided")
    elif rule.get("handling_pct") is not None:
        hnd = round(cif * rule["handling_pct"] / 100)
        h_line = _line("handling", f"Handling / clearance @ {rule['handling_pct']}%", hnd, rate_ev, "of customs value")
    else:
        hnd, h_line = 0, _line("handling", "Handling / clearance fees", None, "missing", "courier/broker fees not included")
        missing.append("handling/clearance fees")
    lines.append(h_line)

    total = price_minor + ship + ins + duty + vat + hnd
    if not verified:
        assumptions.append("Duty and tax rates are ASSUMED (unverified placeholder), not the destination's actual rates.")
    assumptions += ["Duty is applied to item + shipping + insurance; VAT/sales tax to that plus duty.",
                    "Amounts stay in the listing currency; no exchange rate was applied unless shown below."]
    out = {
        "available": True, "currency": currency, "lines": lines, "total_minor": total, "partial": shipping_minor is None,
        "rule": rule_name, "rule_verified": verified, "missing_inputs": missing, "assumptions": assumptions,
        "notes": [n for n in (rule.get("note"),) if n] + ["Excludes excise, product-specific tariffs and local delivery."]
                 + (["Shipping unknown: total is a lower bound."] if shipping_minor is None else []),
        "converted": None,
    }
    if fx is not None:
        out["converted"] = {"currency": fx.quote, "total_minor": int((fx.rate * total).to_integral_value()), "rate": str(fx.rate),
                            "as_of": fx.as_of, "source": fx.source, "stale": fx.stale}
        if fx.stale:
            out["assumptions"].append(f"The exchange rate is stale (as of {fx.as_of:%Y-%m-%d}).")
    return out
