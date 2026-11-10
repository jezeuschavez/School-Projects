"""API flow with a menu loaded from fixtures (no network)."""
import json
from datetime import date

from fastapi.testclient import TestClient

from app import db, foodpro, main, progress

D = "2026-11-10"


def _load_menu(html):
    items = foodpro.parse_long_menu(html("long"))
    nut = foodpro.parse_nut_report(html("rpt"))
    with db.tx() as conn:
        conn.execute("DELETE FROM menu_items")
        for meal in ("Breakfast", "Lunch", "Dinner"):
            for pos, it in enumerate(items):
                n = nut[it["rec"]]
                conn.execute(
                    "INSERT OR IGNORE INTO menu_items(loc,date,meal,rec,name,category,portion,kcal,protein,carbs,sugar,fat,nutrition_source,icons,position) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("40", D, meal, it["rec"], it["name"], it["category"], it["portion"], n.get("kcal"), n.get("protein"),
                     n.get("carbs"), n.get("sugar"), n.get("fat"), "foodpro_report", json.dumps(it["icons"]), pos))
        conn.execute("INSERT OR REPLACE INTO menu_fetches(loc,date,status,content_hash,error,fetched_at) VALUES('40',?,'ok','h',NULL,?)",
                     (D, db.now_iso()))


def test_day_flow(html, monkeypatch):
    _load_menu(html)
    db.save_profile({"default_hall": "40"})  # the fixture menu is hall 40

    def no_network(*a, **k):
        raise AssertionError("test tried to reach FoodPro")
    monkeypatch.setattr("app.menus.sync_menu", no_network)
    c = TestClient(main.app)
    day = c.get(f"/api/day/{D}").json()
    items = day["plan"]["items"]
    assert items and day["targets"]["kcal"] == 2150
    egg = next(i for i in items if "Hard Boiled" in i["name"])
    day = c.post(f"/api/day/{D}/items/{egg['id']}/eaten", json={"eaten": True}).json()
    assert round(day["eaten"]["kcal"]) == 86
    day = c.patch(f"/api/day/{D}/items/{egg['id']}", json={"qty": 2}).json()
    assert round(day["eaten"]["kcal"]) == 173  # eaten log follows portion change
    alts = c.get(f"/api/day/{D}/items/{egg['id']}/alternatives").json()
    assert alts and all(a["rec"] != egg["rec"] for a in alts)
    day = c.post(f"/api/day/{D}/log", json={"name": "Protein shake", "kcal": 160, "protein": 30}).json()
    assert day["log"][0]["verified"] == 0
    assert round(day["eaten"]["protein"], 1) == round(7.596 * 2 + 30, 1)


def test_no_adjustment_without_enough_data():
    db.execute("DELETE FROM weigh_ins")
    db.execute("INSERT INTO weigh_ins(date,weight,created_at) VALUES('2026-11-01',184,'x')")
    res = progress.analyze(db.get_profile(), date(2026, 11, 10))
    assert res["enough_data"] is False and res["pending_adjustment"] is None


def test_stall_with_good_adherence_suggests_cut_but_does_not_apply():
    db.execute("DELETE FROM weigh_ins"); db.execute("DELETE FROM food_log"); db.execute("DELETE FROM adjustments")
    today = date(2026, 11, 30)
    for i in range(21):
        d = date(2026, 11, 10 + i - 1) if False else date.fromordinal(today.toordinal() - 20 + i)
        db.execute("INSERT INTO weigh_ins(date,weight,created_at) VALUES(?,?,'x')", (d.isoformat(), 182.0 + (0.3 if i % 2 else -0.3)))
        db.execute("INSERT INTO food_log(date,source,name,qty,kcal,protein,carbs,fat,verified,created_at) VALUES(?,?,?,?,?,?,?,?,1,'x')",
                   (d.isoformat(), "custom", "day", 1, 2100, 160, 220, 65))
    before = db.get_profile()["calories"]
    res = progress.analyze(db.get_profile(), today)
    adj = res["pending_adjustment"]
    assert adj and adj["proposed_kcal"] == before - 150
    assert db.get_profile()["calories"] == before  # never automatic
    progress.resolve(adj["id"], approve=True)
    assert db.get_profile()["calories"] == before - 150
