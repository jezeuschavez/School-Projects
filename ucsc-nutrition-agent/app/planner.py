"""Deterministic meal planner.

Every number in a plan is FoodPro's per-serving value × a chosen quantity.
The planner only *chooses* items and quantities; it never estimates nutrition.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date as Date, timedelta

from . import db
from .portions import describe, parse_portion, qty_for_amount

MACROS = ("kcal", "protein", "carbs", "fat")

NON_MEAL_CATEGORIES = ("condiment", "beverage", "cereal", "bread & bagel", "salad bar", "toppings", "sauces", "all day")
TREAT_CATEGORIES = ("bakery", "sweet", "dessert", "treat")
SNACK_CATEGORIES = ("all day",)
VEG_WORDS = (
    "broccoli", "brussels", "vegetable", "veggie", "green bean", "carrot", "spinach", "kale", "zucchini",
    "squash", "asparagus", "cauliflower", "cabbage", "bok choy", "salad", "greens", "peppers", "eggplant",
    "mushroom", "chard", "collard", "snap pea", "peas", "beets", "medley",
)

STARCH_WORDS = (
    "rice", "potato", "potatoes", "pasta", "orzo", "noodle", "noodles", "quinoa", "bread", "tortilla", "roll",
    "couscous", "polenta", "bun", "pita", "naan", "fries", "spaghetti", "penne", "macaroni", "farro",
    "barley", "yam", "sweet potato", "grits", "lo mein", "udon", "oatmeal", "beans", "lentils", "biscuit",
    "sourdough", "bagel", "toast", "english muffin", "waffle", "waffles", "pancake", "pancakes", "oats", "granola", "hash browns",
)
NOT_STARCH_WORDS = ("chips", "broth", "sauce", "dressing", "gravy", "crouton", "soup", "cookie", "cake")
MAIN_STATIONS = ("entree", "grill", "clean plate", "hot bar", "global", "wok", "pasta", "home", "deli", "pizza", "taqueria")

# ------------------------------------------------------------------------- targets
def day_type_for(profile: dict, d: Date) -> str:
    return "training" if d.weekday() in (profile.get("training_weekdays") or []) else "rest"


def daily_targets(profile: dict, day_type: str = "rest") -> dict:
    kcal = float(profile["calories"]) + (float(profile.get("training_day_extra_kcal", 0)) if day_type == "training" else 0)
    protein = float(profile["protein_g"])
    fat = float(profile["fat_g"])
    carbs = max(0.0, (kcal - protein * 4 - fat * 9) / 4)
    return {"kcal": round(kcal), "protein": round(protein), "carbs": round(carbs), "fat": round(fat)}


# ------------------------------------------------------------------------- helpers
_FILLER = {"cage", "free", "organic", "natural", "fresh", "large", "local", "the", "and", "with"}


def name_key(name: str) -> str:
    """Normalised identity for variety checks and learned preferences:
    'Hard Boiled Cage-Free Egg' and 'Hard Boiled Egg' are the same food."""
    words = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words if w not in _FILLER]
    return " ".join(words)


def is_avoided(name: str, rules: list[list[str]]) -> bool:
    n = name.lower()
    return any(rule and all(w.lower() in n for w in rule) for rule in rules or [])


def scaled(item: dict, qty: float) -> dict:
    return {k: (item.get(k) or 0.0) * qty for k in MACROS}


def add(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0.0) + b.get(k, 0.0) for k in MACROS}


def sub(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0.0) - b.get(k, 0.0) for k in MACROS}


ZERO = {k: 0.0 for k in MACROS}


def totals(items: list[dict]) -> dict:
    t = dict(ZERO)
    for it in items:
        t = add(t, scaled(it["per"], it["qty"]))
    return {k: round(v, 1) for k, v in t.items()}


def has_nutrition(item: dict) -> bool:
    return item.get("kcal") is not None and item.get("protein") is not None


def classify(item: dict) -> str:
    cat = (item.get("category") or "").lower()
    name = item["name"].lower()
    kcal = item.get("kcal") or 0
    p = item.get("protein") or 0
    c = item.get("carbs") or 0
    if any(w in cat for w in TREAT_CATEGORIES):
        return "treat"
    if kcal <= 0:
        return "other"
    if p >= 15 or (p >= 9 and p * 4 / kcal >= 0.25):
        return "protein"
    if (kcal <= 130 and any(w in name for w in VEG_WORDS)
            and not any(_has_word(name, w) for w in ("tortilla", "tortillas", "noodle", "noodles", "pasta", "rice", "potato", "potatoes"))
            and not any(w in name for w in ("pickled", "pepperoncini", "jalapeno", "olive", "relish", "sauerkraut", "kimchi"))):
        return "veg"
    if (c * 4 / kcal >= 0.5 and p < 9 and any(_has_word(name, w) for w in STARCH_WORDS)
            and not any(w in name for w in NOT_STARCH_WORDS)):
        return "starch"
    return "other"


def qty_options(item: dict, role: str) -> list[float]:
    amount, unit = parse_portion(item.get("portion"))
    kcal = item.get("kcal") or 0
    if unit == "oz" and amount:
        max_oz = {"protein": 8, "starch": 8, "veg": 6}.get(role, 6)
        min_oz = 2 if role in ("protein", "veg") else 1
        opts = [q for q in (0.5, 1, 1.5, 2, 2.5, 3) if min_oz <= amount * q <= max_oz]
    elif role == "protein":
        opts = [q for q in (1, 2, 3) if kcal * q <= 800]
    elif role == "starch":
        opts = [1, 2] if kcal <= 250 else [1]
    else:
        opts = [1]
    return opts or [1]


@dataclass
class Prefs:
    feedback: dict[str, dict] = field(default_factory=dict)
    recent: set[str] = field(default_factory=set)
    avoid_rules: list[list[str]] = field(default_factory=list)

    @classmethod
    def load(cls, profile: dict, d: Date) -> "Prefs":
        fb = {r["name_key"]: r for r in db.query("SELECT * FROM feedback")}
        since = (d - timedelta(days=2)).isoformat()
        recent = {
            name_key(r["name"])
            for r in db.query("SELECT name FROM food_log WHERE date>=? AND date<?", (since, d.isoformat()))
        }
        return cls(feedback=fb, recent=recent, avoid_rules=profile.get("avoid_rules") or [])

    def rating(self, name: str) -> int:
        return (self.feedback.get(name_key(name)) or {}).get("rating", 0)

    def allowed(self, item: dict) -> bool:
        return has_nutrition(item) and not is_avoided(item["name"], self.avoid_rules) and self.rating(item["name"]) >= 0

    def penalty(self, item: dict, used: set[str]) -> float:
        fb = self.feedback.get(name_key(item["name"])) or {}
        k = name_key(item["name"])
        p = -0.08 * fb.get("rating", 0) - 0.015 * min(fb.get("eaten_count", 0), 6) + 0.04 * min(fb.get("swapped_out", 0), 3)
        if k in self.recent:
            p += 0.08
        if k in used:
            p += 0.2
        cat = (item.get("category") or "").lower()
        if any(st in cat for st in MAIN_STATIONS):
            p -= 0.03
        if "soup" in cat or "broth" in item["name"].lower():
            p += 0.1
        return p


def unique_items(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        k = name_key(it["name"])
        if it["rec"] in seen or k in seen:
            continue
        seen.update({it["rec"], k})
        out.append(it)
    return out


def make_item(item: dict, meal: str, qty: float, slot: str, role: str, note: str | None = None) -> dict:
    return {
        "id": uuid.uuid4().hex[:10],
        "slot": slot,
        "meal": meal,
        "rec": item["rec"],
        "name": item["name"],
        "category": item.get("category"),
        "portion": item.get("portion"),
        "qty": qty,
        "amount_label": describe(item.get("portion"), qty),
        "per": {k: item.get(k) for k in MACROS},
        "has_data": has_nutrition(item),
        "icons": item.get("icons", []),
        "role": role,
        "source": item.get("nutrition_source") or "foodpro_report",
        "verified": has_nutrition(item),
        "note": note,
        "unavailable": False,
    }


# ------------------------------------------------------------------- optimisation
def meal_loss(t: dict, target: dict) -> float:
    def rel(k):
        return (t[k] - target[k]) / max(target[k], 1.0)

    p = rel("protein")
    return 5 * rel("kcal") ** 2 + (8 if p < 0 else 1.5) * p * p + 0.6 * rel("carbs") ** 2 + 1.3 * rel("fat") ** 2


def optimise_meal(candidates: list[dict], target: dict, prefs: Prefs, used: set[str]) -> list[tuple[dict, float, str]]:
    """Choose protein + optional starch + optional veg (with quantities) closest to target."""
    roles: dict[str, list[dict]] = {"protein": [], "starch": [], "veg": []}
    for it in candidates:
        r = classify(it)
        if r in roles:
            roles[r].append(it)
    if not roles["protein"]:
        roles["protein"] = sorted(
            [c for c in candidates if classify(c) == "other"], key=lambda c: -(c.get("protein") or 0)
        )[:6]
    proteins = sorted(roles["protein"], key=lambda c: (-(c["protein"] / max(c["kcal"], 1)) + prefs.penalty(c, used), c["name"]))[:20]
    starches = sorted(roles["starch"], key=lambda c: (prefs.penalty(c, used), c["name"]))[:12]
    vegs = sorted(roles["veg"], key=lambda c: (prefs.penalty(c, used), c["name"]))[:8]

    def options(lst, role, optional):
        opts = [(c, q, role, scaled(c, q), prefs.penalty(c, used)) for c in lst for q in qty_options(c, role)]
        return opts + ([(None, 0, role, ZERO, 0.12 if role == "veg" else 0.0)] if optional else [])

    P, S, V = options(proteins, "protein", False), options(starches, "starch", True), options(vegs, "veg", True)
    best, best_score = None, float("inf")
    for p in P:
        for s in S:
            ps = add(p[3], s[3])
            for v in V:
                t = add(ps, v[3])
                score = meal_loss(t, target) + p[4] + s[4] + v[4]
                if score < best_score - 1e-9:
                    best, best_score = (p, s, v), score
    if best is None:
        return []
    return [(c, q, role) for c, q, role, _, _ in best if c is not None]


def best_single(candidates: list[dict], gap: dict, prefs: Prefs, used: set[str], role_hint="protein"):
    """Best single item+qty to close a (protein-focused) gap without overshooting calories much."""
    best, best_score = None, float("inf")
    for c in candidates:
        role = classify(c)
        for q in qty_options(c, role if role in ("protein", "starch", "veg") else role_hint):
            t = scaled(c, q)
            over_k = max(0.0, t["kcal"] - gap["kcal"] - 60)
            score = ((gap["protein"] - t["protein"]) / 20) ** 2 + (over_k / 150) ** 2 + 0.3 * (abs(gap["kcal"] - t["kcal"]) / 400) + prefs.penalty(c, used)
            if score < best_score:
                best, best_score = (c, q, role), score
    return best


# ------------------------------------------------------------------ breakfast template
BREAKFAST_TEMPLATE = [
    {"key": "sourdough", "label": "1 slice sourdough", "alts": [["sourdough"]],
     "exclude": ["english muffin", "bagel"], "amount": 1, "unit": "ea"},
    {"key": "turkey", "label": "~2 oz sliced turkey", "alts": [["sliced", "turkey"], ["deli", "turkey"], ["turkey", "breast"]],
     "exclude": ["sausage", "soup", "burger", "chili", "bacon"], "amount": 2, "unit": "oz", "protein_core": True},
    {"key": "egg", "label": "1 hard-boiled egg", "alts": [["hard", "boiled", "egg"], ["hard-boiled", "egg"]],
     "exclude": [], "amount": 1, "unit": "ea", "protein_core": True},
    {"key": "yogurt", "label": "plain nonfat Greek yogurt", "alts": [["nonfat", "greek", "yogurt"], ["greek", "yogurt"], ["plain", "yogurt"]],
     "exclude": ["soy", "coconut", "cashew", "vanilla", "vegan", "almond", "oat"], "amount": 6, "unit": "oz", "protein_core": True},
    {"key": "strawberries", "label": "strawberries", "alts": [["strawberries"]],
     "exclude": ["preserve", "topping", "jam", "jelly", "syrup"], "amount": 2, "unit": "oz"},
    {"key": "honey", "label": "drizzle of honey", "alts": [["honey"]],
     "exclude": ["mustard", "honey'd", "dressing", "glaze", "bbq", "corn"], "amount": 0.25, "unit": "oz"},
    {"key": "banana", "label": "1 banana", "alts": [["banana"]],
     "exclude": ["bread", "muffin", "joe", "pudding"], "amount": 4, "unit": "oz"},
    {"key": "coffee", "label": "black coffee", "alts": [["coffee"]],
     "exclude": ["creamer", "cake", "decaf"], "amount": 12, "unit": "oz"},
    {"key": "creamer", "label": "~1 tbsp hazelnut creamer", "alts": [["hazelnut", "creamer"]],
     "exclude": [], "amount": 0.5, "unit": "oz"},
]


def _has_word(name: str, word: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", name) is not None


def _match(items: list[dict], words: list[str], exclude: list[str]) -> dict | None:
    # Shortest name first: "Banana" beats "Banana and Coconut Pancakes".
    for it in sorted(items, key=lambda i: (len(i["name"]), i["name"])):
        n = it["name"].lower()
        if all(_has_word(n, w) for w in words) and not any(x in n for x in exclude):
            return it
    return None


def build_breakfast(items: list[dict], meal: str, slot: str, prefs: Prefs, protein_goal: float) -> tuple[list[dict], list[str]]:
    plan_items, notes, used = [], [], set()
    usable = [i for i in items if not is_avoided(i["name"], prefs.avoid_rules)]
    for comp in BREAKFAST_TEMPLATE:
        found, alt_index = None, 0
        for idx, words in enumerate(comp["alts"]):
            found = _match(usable, words, comp["exclude"])
            if found:
                alt_index = idx
                break
        if not found:
            notes.append(f"{comp['label'].capitalize()} isn't on today's {meal.lower()} menu.")
            continue
        if not has_nutrition(found):
            notes.append(f"FoodPro has no usable nutrition data for {found['name']}, so it isn't counted in the totals.")
        qty, exact = qty_for_amount(found.get("portion"), comp["amount"], comp["unit"])
        note = None
        if alt_index > 0:
            note = f"Closest match for {comp['label']}"
        if not exact:
            note = (note + "; " if note else "") + f"serving is {found.get('portion')}, adjust as needed"
        plan_items.append(make_item(found, meal, qty, slot, "template", note))
        used.add(name_key(found["name"]))
        if alt_index > 0:
            notes.append(f"Using {found['name']} instead of {comp['label']}.")
    # If a protein component was missing, top up protein from the same menu.
    current = totals(plan_items)
    gap_p = protein_goal - current["protein"]
    if gap_p >= 8:
        cands = [i for i in unique_items(usable) if prefs.allowed(i) and classify(i) == "protein" and name_key(i["name"]) not in used]
        pick = best_single(cands, {"protein": gap_p, "kcal": gap_p * 9}, prefs, used)
        if pick:
            c, q, role = pick
            plan_items.append(make_item(c, meal, q, slot, "protein", "Added to cover breakfast protein"))
    return plan_items, notes


# ------------------------------------------------------------------------ main entry
def meal_slots(meal_names: list[str]) -> list[tuple[str, str]]:
    slots = []
    if "Breakfast" in meal_names:
        slots.append(("breakfast", "Breakfast"))
    elif "Brunch" in meal_names:
        slots.append(("brunch", "Brunch"))
    if "Lunch" in meal_names:
        slots.append(("lunch", "Lunch"))
    if "Dinner" in meal_names:
        slots.append(("dinner", "Dinner"))
    return slots


def generate(menu: dict, profile: dict, d: Date, day_type: str | None = None, prefs: Prefs | None = None) -> dict:
    day_type = day_type or day_type_for(profile, d)
    targets = daily_targets(profile, day_type)
    plan = {
        "date": d.isoformat(),
        "loc": menu["loc"],
        "day_type": day_type,
        "status": menu["status"],
        "menu_hash": menu.get("content_hash"),
        "items": [],
        "notes": [],
        "summary": None,
    }
    if menu["status"] != "ok":
        plan["notes"].append(
            "No menu is posted for this hall on this date yet. It may not be published, or the hall may be "
            "closed that day. I'll keep checking; you can also pick another dining hall."
            if menu["status"] in ("unpublished", "missing")
            else f"Couldn't reach the UCSC menu site: {menu.get('error')}. Will retry."
        )
        return plan
    prefs = prefs or Prefs.load(profile, d)
    prefs.avoid_rules = profile.get("avoid_rules") or prefs.avoid_rules  # always honour the profile
    meals = menu["meals"]
    slots = meal_slots(list(meals))
    if not slots:
        plan["notes"].append("No breakfast, lunch, or dinner is listed for this hall on this date.")
        return plan

    used: set[str] = set()
    items: list[dict] = []
    remaining = dict(targets)
    first_slot, first_meal = slots[0]
    rest_slots = slots
    if first_slot in ("breakfast", "brunch") and profile.get("use_breakfast_template", True):
        b_items, b_notes = build_breakfast(meals[first_meal], first_meal, first_slot, prefs, protein_goal=targets["protein"] * 0.25)
        if len([i for i in b_items if i["role"] == "template"]) >= 3:
            items += b_items
            plan["notes"] += b_notes
            used |= {name_key(i["name"]) for i in b_items}
            remaining = sub(remaining, totals(b_items))
            rest_slots = slots[1:]
        else:
            plan["notes"].append("Your usual breakfast isn't available here today, so I picked one from the menu.")

    weights = {"breakfast": 0.8, "brunch": 1.2, "lunch": 1.0, "dinner": 1.1}
    snack_share = 0.08 if profile.get("include_snack", True) else 0.0
    reserve = {k: targets[k] * snack_share for k in MACROS}
    for i, (slot, meal) in enumerate(rest_slots):
        w_left = sum(weights[s] for s, _ in rest_slots[i:])
        share = weights[slot] / w_left
        target = {k: max((remaining[k] - reserve[k]) * share, 1.0) for k in MACROS}
        cands = [c for c in unique_items(meals[meal]) if prefs.allowed(c)
                 and not any(x in (c.get("category") or "").lower() for x in NON_MEAL_CATEGORIES)
                 and classify(c) != "treat"]
        chosen = optimise_meal(cands, target, prefs, used)
        if not chosen:
            plan["notes"].append(f"Couldn't find enough items with nutrition data for {meal.lower()}.")
            continue
        new = [make_item(c, meal, q, slot, role) for c, q, role in chosen]
        items += new
        used |= {name_key(n["name"]) for n in new}
        remaining = sub(remaining, totals(new))

    # Snack: close any protein gap with a grab-and-go item from any meal.
    if profile.get("include_snack", True) and (remaining["protein"] >= 7 or remaining["kcal"] >= 150):
        pool = []
        for meal, its in meals.items():
            for c in unique_items(its):
                cat = (c.get("category") or "").lower()
                if prefs.allowed(c) and (any(s in cat for s in SNACK_CATEGORIES) or "hard boiled" in c["name"].lower()
                                         or "milk" == c["name"].lower().split()[-1]):
                    kc = c.get("kcal") or 0
                    if ((c.get("protein") or 0) >= 4 and kc > 0 and c["protein"] * 4 / kc >= 0.3
                ):
                        pool.append((meal, c))
        by_name = {name_key(c["name"]): (m, c) for m, c in pool}
        # Repeating a breakfast food (e.g. yogurt) as a snack is fine: mild penalty only.
        pick = best_single([c for _, c in by_name.values()], {"protein": max(remaining["protein"], 0), "kcal": max(remaining["kcal"], 0)},
                           prefs, used=set())
        if pick:
            c, q, role = pick
            meal = by_name[name_key(c["name"])][0]
            items.append(make_item(c, meal, q, "snack", "snack", f"Available during {meal.lower()}"))

    plan["items"] = items
    return plan


# ----------------------------------------------------------------------- swapping
def alternatives(plan: dict, item_id: str, menu: dict, profile: dict, limit: int = 12) -> list[dict]:
    item = next((i for i in plan["items"] if i["id"] == item_id), None)
    if not item:
        return []
    d = Date.fromisoformat(plan["date"])
    prefs = Prefs.load(profile, d)
    used = {name_key(i["name"]) for i in plan["items"] if i["id"] != item_id}
    orig = scaled(item["per"], item["qty"])
    role = item["role"] if item["role"] in ("protein", "starch", "veg") else classify({**item, **item["per"]})
    out = []
    for c in unique_items(menu["meals"].get(item["meal"], [])):
        if c["rec"] == item["rec"] or not prefs.allowed(c):
            continue
        if any(x in (c.get("category") or "").lower() for x in ("condiment",)):
            continue
        c_role = classify(c)
        best_q, best_err = 1.0, float("inf")
        for q in qty_options(c, c_role if c_role in ("protein", "starch", "veg") else role):
            t = scaled(c, q)
            err = (abs(t["protein"] - orig["protein"]) / 8 if role == "protein" else 0) + abs(t["kcal"] - orig["kcal"]) / 120
            if err < best_err:
                best_q, best_err = q, err
        t = scaled(c, best_q)
        score = best_err + (0 if c_role == role else 1.5) + prefs.penalty(c, used) * 3
        out.append({
            "rec": c["rec"], "meal": item["meal"], "name": c["name"], "category": c.get("category"),
            "portion": c.get("portion"), "qty": best_q, "amount_label": describe(c.get("portion"), best_q),
            "role": c_role, "icons": c.get("icons", []),
            "totals": {k: round(v, 1) for k, v in t.items()},
            "delta": {k: round(t[k] - orig[k], 1) for k in MACROS},
            "score": round(score, 3),
        })
    out.sort(key=lambda x: (x["score"], x["name"]))
    return out[:limit]
