"""Fetch menus from FoodPro, cache them in SQLite, and detect changes."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import date as Date, datetime, timedelta, timezone

from . import db
from .foodpro import FoodProClient, FoodProError

log = logging.getLogger(__name__)
_sync_lock = threading.Lock()

MAX_LABEL_FALLBACKS = 40  # per sync – keeps total requests polite


def _hash(meals: dict[str, list[dict]]) -> str:
    sig = sorted((m, it["rec"], it.get("kcal")) for m, items in meals.items() for it in items)
    return hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16]


def _all_zero_but_has_ingredients(label: dict) -> bool:
    """FoodPro sometimes publishes all-zero labels for real foods (e.g. creamer whose
    ingredients list sugar and oil). Treat those as missing rather than 0 kcal."""
    zeros = all((label.get(k) or 0) == 0 for k in ("kcal", "protein", "carbs", "fat"))
    ingredients = (label.get("ingredients") or "").lower()
    return zeros and any(w in ingredients for w in ("sugar", "oil", "flour", "milk", "cream", "syrup"))


def sync_menu(loc: str, d: Date, client: FoodProClient | None = None) -> dict:
    """Download a day's menu for one hall and store it. Returns the fetch record."""
    own = client is None
    client = client or FoodProClient()
    try:
        with _sync_lock:
            try:
                day = client.fetch_day(loc, d)
            except FoodProError as exc:
                log.error("menu sync failed for %s %s: %s", loc, d, exc)
                prev = db.query_one("SELECT * FROM menu_fetches WHERE loc=? AND date=?", (loc, d.isoformat()))
                if prev and prev["status"] == "ok":
                    # keep the last good copy; just note the error
                    db.execute("UPDATE menu_fetches SET error=? WHERE loc=? AND date=?", (str(exc), loc, d.isoformat()))
                    return {**prev, "error": str(exc)}
                db.execute(
                    "INSERT OR REPLACE INTO menu_fetches(loc,date,status,content_hash,error,fetched_at) VALUES(?,?,?,?,?,?)",
                    (loc, d.isoformat(), "error", None, str(exc), db.now_iso()),
                )
                return {"loc": loc, "date": d.isoformat(), "status": "error", "error": str(exc)}

            meals = day["meals"]
            # Label fallback: the bulk report sometimes omits numbers that the
            # item's own label has. Only verified label values are used.
            missing = {}
            for items in meals.values():
                for it in items:
                    it["nutrition_source"] = "foodpro_report" if it.get("kcal") is not None else None
                    if it.get("kcal") is None:
                        missing.setdefault(it["rec"], []).append(it)
            for rec in list(missing)[:MAX_LABEL_FALLBACKS]:
                try:
                    label = get_label(loc, d, rec, client=client)
                except FoodProError:
                    continue
                if label.get("kcal") is None or _all_zero_but_has_ingredients(label):
                    continue
                for it in missing[rec]:
                    for k in ("kcal", "protein", "carbs", "sugar", "fat"):
                        it[k] = label.get(k)
                    it["nutrition_source"] = "foodpro_label"

            content_hash = _hash(meals) if day["status"] == "ok" else None
            with db.tx() as conn:
                conn.execute("DELETE FROM menu_items WHERE loc=? AND date=?", (loc, d.isoformat()))
                for meal, items in meals.items():
                    seen = set()
                    for pos, it in enumerate(items):
                        if it["rec"] in seen:  # FoodPro occasionally lists an item twice
                            continue
                        seen.add(it["rec"])
                        conn.execute(
                            """INSERT INTO menu_items(loc,date,meal,rec,name,category,portion,kcal,protein,carbs,sugar,fat,
                               nutrition_source,icons,position) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (loc, d.isoformat(), meal, it["rec"], it["name"], it["category"], it["portion"],
                             it.get("kcal"), it.get("protein"), it.get("carbs"), it.get("sugar"), it.get("fat"),
                             it.get("nutrition_source"), json.dumps(it["icons"]), pos),
                        )
                conn.execute(
                    "INSERT OR REPLACE INTO menu_fetches(loc,date,status,content_hash,error,fetched_at) VALUES(?,?,?,?,?,?)",
                    (loc, d.isoformat(), day["status"], content_hash, None, db.now_iso()),
                )
            return {"loc": loc, "date": d.isoformat(), "status": day["status"], "content_hash": content_hash}
    finally:
        if own:
            client.close()


def fetch_record(loc: str, d: Date) -> dict | None:
    return db.query_one("SELECT * FROM menu_fetches WHERE loc=? AND date=?", (loc, d.isoformat()))


def ensure_menu(loc: str, d: Date, max_age_hours: float = 12, client: FoodProClient | None = None) -> dict:
    """Return cached fetch info, refreshing it if missing or stale."""
    rec = fetch_record(loc, d)
    today = datetime.now(timezone.utc).date()
    if rec:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(rec["fetched_at"])
        if d < today - timedelta(days=1) and rec["status"] == "ok":
            return rec  # past menus don't change
        limit = max_age_hours if rec["status"] == "ok" else 1  # retry unpublished/error sooner
        if age < timedelta(hours=limit):
            return rec
    return sync_menu(loc, d, client=client)


def get_menu(loc: str, d: Date) -> dict:
    rec = fetch_record(loc, d)
    rows = db.query(
        "SELECT * FROM menu_items WHERE loc=? AND date=? ORDER BY meal, position", (loc, d.isoformat())
    )
    meals: dict[str, list[dict]] = {}
    for r in rows:
        r["icons"] = json.loads(r["icons"])
        meals.setdefault(r["meal"], []).append(r)
    return {
        "loc": loc,
        "date": d.isoformat(),
        "status": rec["status"] if rec else "missing",
        "fetched_at": rec["fetched_at"] if rec else None,
        "error": rec["error"] if rec else None,
        "content_hash": rec["content_hash"] if rec else None,
        "meals": meals,
    }


def get_item(loc: str, d: Date, meal: str, rec: str) -> dict | None:
    r = db.query_one(
        "SELECT * FROM menu_items WHERE loc=? AND date=? AND meal=? AND rec=?", (loc, d.isoformat(), meal, rec)
    )
    if r:
        r["icons"] = json.loads(r["icons"])
    return r


def get_label(loc: str, d: Date, rec: str, client: FoodProClient | None = None) -> dict:
    row = db.query_one("SELECT data FROM labels WHERE loc=? AND date=? AND rec=?", (loc, d.isoformat(), rec))
    if row:
        return json.loads(row["data"])
    own = client is None
    client = client or FoodProClient()
    try:
        label = client.fetch_label(loc, d, rec)
    finally:
        if own:
            client.close()
    db.execute(
        "INSERT OR REPLACE INTO labels(loc,date,rec,data,fetched_at) VALUES(?,?,?,?,?)",
        (loc, d.isoformat(), rec, json.dumps(label), db.now_iso()),
    )
    return label
