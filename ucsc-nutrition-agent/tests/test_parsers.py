"""Parsers against real FoodPro pages captured 2026-11-10 (JRL & College Nine)."""
from app import foodpro


def test_short_menu_counts(html):
    s = foodpro.parse_short_menu(html("short40"))
    assert s["meals"] == {"Breakfast": 14, "Lunch": 30, "Dinner": 25, "Late Night": 19}
    assert "November 10, 2026" in s["title"]


def test_unpublished_day_has_meals_but_no_items(html):
    s = foodpro.parse_short_menu(html("s1201"))
    assert set(s["meals"]) == {"Breakfast", "Lunch", "Dinner", "Late Night"}
    assert sum(s["meals"].values()) == 0


def test_long_menu_items(html):
    items = foodpro.parse_long_menu(html("long"))
    assert len(items) == 196
    egg = next(i for i in items if i["rec"] == "061003*1")
    assert egg == {"rec": "061003*1", "name": "Hard Boiled Cage-Free Egg", "category": "Breakfast",
                   "portion": "1 ea", "icons": ["veggie", "eggs", "gluten"]}


def test_nut_report_matches_label(html):
    rpt = foodpro.parse_nut_report(html("rpt"))
    label = foodpro.parse_label(html("label"))
    egg = rpt["061003*1"]
    assert round(egg["kcal"]) == label["kcal"] == 86
    assert round(egg["protein"], 1) == label["protein"] == 7.6
    assert round(egg["fat"], 1) == label["fat"] == 5.8
    assert len(rpt) == 196


def test_label_fields(html):
    label = foodpro.parse_label(html("label"))
    assert label["serving"] == "1 ea"
    assert label["allergens"] == "Egg"
    assert label["ingredients"] == "Cage-Free Eggs"
    assert label["sodium_mg"] == 85.9


def test_gluten_icon_means_gluten_free():
    assert foodpro.ICONS["gluten"] == ("diet", "Gluten-free")
