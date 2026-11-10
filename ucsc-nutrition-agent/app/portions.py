"""Serving-size parsing and scaling. FoodPro portions look like '3 1/2 oz',
'1/2 ozl', '2 ea', '1 slc', '1/10 pie', '8 ozl', '2 tbsp'."""
from __future__ import annotations

import re
from fractions import Fraction

UNIT_MAP = {
    "oz": "oz", "ozl": "oz", "ounce": "oz", "ounces": "oz",
    "ea": "ea", "each": "ea", "slc": "ea", "slice": "ea", "pc": "ea", "piece": "ea", "pcs": "ea",
    "cup": "cup", "cups": "cup", "tbsp": "tbsp", "tsp": "tsp", "pie": "pie",
}


def parse_portion(text: str | None) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    t = text.strip().lower().replace("\xa0", " ")
    m = re.match(r"^(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*([a-z]+)?", t)
    if not m:
        return None, None
    try:
        amount = float(sum(Fraction(part) for part in m.group(1).split()))
    except (ValueError, ZeroDivisionError):
        return None, None
    unit = UNIT_MAP.get(m.group(2) or "", None)
    return amount, unit


def round_qty(q: float, step: float = 0.25) -> float:
    return max(step, round(q / step) * step)


def qty_for_amount(portion: str | None, amount: float, unit: str) -> tuple[float, bool]:
    """Servings needed to get `amount` `unit`. Returns (qty, exact_unit_match)."""
    serving, sunit = parse_portion(portion)
    if serving and sunit == unit:
        return round_qty(amount / serving), True
    return 1.0, False


def describe(portion: str | None, qty: float) -> str:
    """'2 × 2 oz (4 oz)' style label for the UI."""
    serving, unit = parse_portion(portion)
    q = f"{qty:g}"
    if qty == 1:
        return portion or "1 serving"
    if serving is None or unit is None:
        return f"{q} × {portion or 'serving'}"
    total = serving * qty
    return f"{q} × {portion} ({total:g} {unit})"
