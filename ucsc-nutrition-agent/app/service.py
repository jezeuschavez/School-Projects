"""Plan persistence + user actions (edit, swap, eaten, feedback, logging).
Totals are always recomputed from items, never stored."""
from __future__ import annotations

import json
import logging
from datetime import date as Date

from . import db, llm, menus, planner
from .portions import describe

log = logging.getLogger(__name__)

SLOT_ORDER = {"breakfast": 0, "brunch": 0, "lunch": 1, "dinner": 2, "snack": 3}


# ---------------------------------------------------------------- storage
def load_plan(d: Date) -> dict | None:
    row = db.query_one("SELECT * FROM plans WHERE date=?", (d.isoformat(),))
    if not row:
        return None
    plan = json.loads(row["data"])
    plan["user_edited"] = bool(row["user_edited"])
    return plan


def save_plan(plan: dict, user_edited: bool | None = None) -> dict:
    existing = db.query_one("SELECT user_edited, created_at FROM plans WHERE date=?", (plan["date"],))
    edited = user_edited if user_edited is not None else bool(existing and existing["user_edited"])
    plan["items"].sort(key=lambda i: SLOT_ORDER.get(i["slot"], 9))
    data = {k: v for k, v in plan.items() if k != "user_edited"}
    db.execute(
        """INSERT INTO plans(date,loc,data,user_edited,menu_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET loc=excluded.loc, data=excluded.data, user_edited=excluded.user_edited,
           menu_hash=excluded.menu_hash, updated_at=excluded.updated_at""",
        (plan["date"], plan["loc"], json.dumps(data), int(edited), plan.get("menu_hash"),
         existing["created_at"] if existing else db.now_iso(), db.now_iso()),
    )
    plan["user_edited"] = edited
    return plan


# ---------------------------------------------------------------- generation
def generate_plan(d: Date, loc: str | None = None, day_type: str | None = None,
                  refresh_menu: bool = False, with_summary: bool = True) -> dict:
    profile = db.get_profile()
    loc = loc or profile["default_hall"]
    if refresh_menu:
        menus.sync_menu(loc, d)
    else:
        menus.ensure_menu(loc, d)
    menu = menus.get_menu(loc, d)
    plan = planner.generate(menu, profile, d, day_type=day_type)
    plan["generated_at"] = db.now_iso()
    if plan["status"] == "ok" and with_summary:
        plan["summary"] = llm.summarize_plan(plan, planner.totals(plan["items"]),
                                             planner.daily_targets(profile, plan["day_type"]))
    # Keep eaten state if regenerating the same day.
    old = load_plan(d)
    if old:
        eaten_ids = {i["id"] for i in old.get("items", []) if i.get("eaten")}
        keep = [i for i in old["items"] if i["id"] in eaten_ids]
        plan["items"] = keep + [i for i in plan["items"] if i["name"] not in {k["name"] for k in keep}]
    return save_plan(plan, user_edited=False)


def reconcile_with_menu(d: Date) -> str:
    """After a menu refresh: regenerate untouched plans, flag removed items on edited ones."""
    plan = load_plan(d)
    if not plan:
        return "no plan"
    menu = menus.get_menu(plan["loc"], d)
    if menu["status"] != "ok" or menu["content_hash"] == plan.get("menu_hash"):
        return "unchanged"
    if not plan.get("user_edited") and not any(i.get("eaten") for i in plan["items"]):
        generate_plan(d, plan["loc"], plan["day_type"])
        return "regenerated"
    available = {(m, it["rec"]) for m, its in menu["meals"].items() for it in its}
    changed = 0
    for it in plan["items"]:
        if it.get("source") == "custom":
            continue
        gone = (it["meal"], it["rec"]) not in available
        if gone != it.get("unavailable", False):
            it["unavailable"] = gone
            changed += 1
    plan["menu_hash"] = menu["content_hash"]
    if changed:
        plan["notes"] = [n for n in plan["notes"] if not n.startswith("The menu changed")] + [
            "The menu changed: items marked 'no longer listed' should be swapped."
        ]
    save_plan(plan)
    return f"flagged {changed}"


# ---------------------------------------------------------------- views
def consumed(d: Date) -> list[dict]:
    return db.query("SELECT * FROM food_log WHERE date=? ORDER BY id", (d.isoformat(),))


def day_view(d: Date) -> dict:
    profile = db.get_profile()
    plan = load_plan(d)
    day_type = plan["day_type"] if plan else planner.day_type_for(profile, d)
    targets = planner.daily_targets(profile, day_type)
    log_rows = consumed(d)
    eaten = {k: round(sum((r[k] or 0) for r in log_rows), 1) for k in planner.MACROS}
    planned = planner.totals(plan["items"]) if plan else {k: 0 for k in planner.MACROS}
    if plan:
        for it in plan["items"]:
            it["totals"] = planner.totals([it])
    return {
        "date": d.isoformat(),
        "plan": plan,
        "targets": targets,
        "planned": planned,
        "eaten": eaten,
        "remaining": {k: round(targets[k] - eaten[k], 1) for k in planner.MACROS},
        "log": [r for r in log_rows if r["source"] != "plan"],
        "day_type": day_type,
        "menu": menus.fetch_record(plan["loc"], d) if plan else None,
    }


# ---------------------------------------------------------------- edits
def _find(plan: dict, item_id: str) -> dict:
    for it in plan["items"]:
        if it["id"] == item_id:
            return it
    raise KeyError(item_id)


def _sync_log_entry(d: Date, it: dict) -> None:
    """Keep the food_log row for an eaten plan item in step with its qty."""
    t = planner.totals([it])
    db.execute(
        "UPDATE food_log SET qty=?, kcal=?, protein=?, carbs=?, fat=? WHERE date=? AND plan_item_id=?",
        (it["qty"], t["kcal"], t["protein"], t["carbs"], t["fat"], d.isoformat(), it["id"]),
    )


def set_qty(d: Date, item_id: str, qty: float) -> dict:
    plan = load_plan(d)
    it = _find(plan, item_id)
    it["qty"] = max(0.25, round(qty * 4) / 4)
    it["amount_label"] = describe(it.get("portion"), it["qty"])
    if it.get("eaten"):
        _sync_log_entry(d, it)
    return save_plan(plan, user_edited=True)


def remove_item(d: Date, item_id: str) -> dict:
    plan = load_plan(d)
    it = _find(plan, item_id)
    if it.get("eaten"):
        set_eaten(d, item_id, False)
        plan = load_plan(d)
    plan["items"] = [i for i in plan["items"] if i["id"] != item_id]
    return save_plan(plan, user_edited=True)


def add_menu_item(d: Date, slot: str, meal: str, rec: str, qty: float = 1) -> dict:
    plan = load_plan(d)
    item = menus.get_item(plan["loc"], d, meal, rec)
    if not item:
        raise KeyError(rec)
    plan["items"].append(planner.make_item(item, meal, qty, slot, planner.classify(item), "Added by you"))
    return save_plan(plan, user_edited=True)


def swap_item(d: Date, item_id: str, rec: str, qty: float, dislike: bool = False) -> dict:
    plan = load_plan(d)
    old = _find(plan, item_id)
    item = menus.get_item(plan["loc"], d, old["meal"], rec)
    if not item:
        raise KeyError(rec)
    new = planner.make_item(item, old["meal"], qty, old["slot"], planner.classify(item), None)
    plan["items"] = [new if i["id"] == item_id else i for i in plan["items"]]
    record_feedback(old["name"], rating=-1 if dislike else None, swapped_out=1)
    return save_plan(plan, user_edited=True)


def set_eaten(d: Date, item_id: str, eaten: bool) -> dict:
    plan = load_plan(d)
    it = _find(plan, item_id)
    db.execute("DELETE FROM food_log WHERE date=? AND plan_item_id=?", (d.isoformat(), item_id))
    if eaten:
        t = planner.totals([it])
        db.execute(
            """INSERT INTO food_log(date,slot,plan_item_id,source,name,rec,portion,qty,kcal,protein,carbs,fat,verified,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d.isoformat(), it["slot"], item_id, "plan", it["name"], it["rec"], it.get("portion"), it["qty"],
             t["kcal"], t["protein"], t["carbs"], t["fat"], int(it.get("verified", True)), db.now_iso()),
        )
        if not it.get("eaten"):
            record_feedback(it["name"], eaten=1)
    it["eaten"] = eaten
    return save_plan(plan)


def set_day_type(d: Date, day_type: str) -> dict:
    plan = load_plan(d)
    if plan is None:
        return generate_plan(d, day_type=day_type)
    plan["day_type"] = day_type
    return save_plan(plan)


# ---------------------------------------------------------------- feedback / learning
def record_feedback(name: str, rating: int | None = None, eaten: int = 0, swapped_out: int = 0) -> None:
    key = planner.name_key(name)
    row = db.query_one("SELECT * FROM feedback WHERE name_key=?", (key,))
    if row is None:
        db.execute(
            "INSERT INTO feedback(name_key,name,rating,eaten_count,swapped_out,updated_at) VALUES(?,?,?,?,?,?)",
            (key, name, rating or 0, eaten, swapped_out, db.now_iso()),
        )
    else:
        db.execute(
            "UPDATE feedback SET rating=?, eaten_count=eaten_count+?, swapped_out=swapped_out+?, updated_at=? WHERE name_key=?",
            (row["rating"] if rating is None else rating, eaten, swapped_out, db.now_iso(), key),
        )


# ---------------------------------------------------------------- custom food log
def log_custom(d: Date, name: str, kcal: float, protein: float, carbs: float, fat: float,
               qty: float = 1, slot: str | None = None, save_food: bool = False, serving: str | None = None) -> int:
    if save_food:
        db.execute(
            """INSERT INTO custom_foods(name,serving,kcal,protein,carbs,fat,created_at) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET serving=excluded.serving,kcal=excluded.kcal,protein=excluded.protein,
               carbs=excluded.carbs,fat=excluded.fat""",
            (name, serving, kcal, protein, carbs, fat, db.now_iso()),
        )
    return db.execute(
        """INSERT INTO food_log(date,slot,source,name,portion,qty,kcal,protein,carbs,fat,verified,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,0,?)""",
        (d.isoformat(), slot, "custom", name, serving, qty, kcal * qty, protein * qty, carbs * qty, fat * qty, db.now_iso()),
    )


def log_menu_item(d: Date, loc: str, meal: str, rec: str, qty: float, slot: str | None = None) -> int:
    item = menus.get_item(loc, d, meal, rec)
    if not item or not planner.has_nutrition(item):
        raise KeyError(rec)
    t = planner.scaled(item, qty)
    record_feedback(item["name"], eaten=1)
    return db.execute(
        """INSERT INTO food_log(date,slot,source,name,rec,portion,qty,kcal,protein,carbs,fat,verified,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (d.isoformat(), slot, "dining", item["name"], rec, item["portion"], qty,
         t["kcal"], t["protein"], t["carbs"], t["fat"], db.now_iso()),
    )
