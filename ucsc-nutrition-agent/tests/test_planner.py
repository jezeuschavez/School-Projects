from datetime import date

from app import config, planner, portions
from app import foodpro


def test_parse_portion():
    assert portions.parse_portion("3 1/2 oz") == (3.5, "oz")
    assert portions.parse_portion("1/2 ozl") == (0.5, "oz")
    assert portions.parse_portion("2 ea") == (2.0, "ea")
    assert portions.parse_portion("1 slc") == (1.0, "ea")
    assert portions.parse_portion("") == (None, None)
    assert portions.qty_for_amount("2 oz", 4, "oz") == (2.0, True)
    assert portions.qty_for_amount("1 ea", 4, "oz") == (1.0, False)


def test_targets():
    prof = dict(config.DEFAULT_PROFILE)
    assert planner.daily_targets(prof, "rest") == {"kcal": 2150, "protein": 160, "carbs": 231, "fat": 65}
    assert planner.daily_targets(prof, "training")["kcal"] == 2300


def test_avoid_scrambled_eggs_not_tofu():
    rules = [["scrambled", "egg"]]
    assert planner.is_avoided("Cage-Free Scrambled Eggs", rules)
    assert not planner.is_avoided("Natural Bridges Tofu Scramble", rules)
    assert not planner.is_avoided("Hard Boiled Cage-Free Egg", rules)


def _menu_from_fixtures(html):
    items = foodpro.parse_long_menu(html("long"))
    nut = foodpro.parse_nut_report(html("rpt"))
    for it in items:
        it.update({k: nut[it["rec"]].get(k) for k in ("kcal", "protein", "carbs", "sugar", "fat")})
        it["nutrition_source"] = "foodpro_report"
    # The fixture is breakfast only; reuse it as lunch/dinner to exercise the optimiser.
    return {"loc": "40", "status": "ok", "content_hash": "x",
            "meals": {"Breakfast": items, "Lunch": items, "Dinner": items}}


def test_breakfast_template_uses_real_items(html):
    menu = _menu_from_fixtures(html)
    plan = planner.generate(menu, dict(config.DEFAULT_PROFILE), date(2026, 11, 10), prefs=planner.Prefs())
    bfast = [i["name"] for i in plan["items"] if i["slot"] == "breakfast"]
    assert "Large Sliced Sourdough" in bfast
    assert "Hard Boiled Cage-Free Egg" in bfast
    assert "Banana" in bfast and "Pure Clover Honey" in bfast and "Strawberries" in bfast
    assert not any("Scrambled" in n for n in bfast)
    banana = next(i for i in plan["items"] if i["name"] == "Banana")
    assert banana["qty"] == 2  # 4 oz from a 2 oz serving
    assert any("turkey" in n.lower() for n in plan["notes"])  # turkey missing is reported, not invented


def test_plan_numbers_are_menu_numbers(html):
    menu = _menu_from_fixtures(html)
    plan = planner.generate(menu, dict(config.DEFAULT_PROFILE), date(2026, 11, 10), prefs=planner.Prefs())
    by_rec = {i["rec"]: i for i in menu["meals"]["Breakfast"]}
    for it in plan["items"]:
        src = by_rec[it["rec"]]
        for k in planner.MACROS:
            assert it["per"][k] == src[k]
    t = planner.totals(plan["items"])
    assert 1700 <= t["kcal"] <= 2600
    assert t["protein"] >= 120


def test_unpublished_menu_gives_note():
    plan = planner.generate({"loc": "40", "status": "unpublished", "meals": {}}, dict(config.DEFAULT_PROFILE), date(2026, 12, 1))
    assert plan["items"] == [] and "No menu is posted" in plan["notes"][0]
