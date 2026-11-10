"""Client + parsers for UCSC's FoodPro site (nutrition.sa.ucsc.edu).

Data flow for one (dining hall, date):
  1. GET /                    -> sets the session cookies FoodPro needs
  2. GET shortmenu.aspx       -> which meals have items that day (or "not published")
  3. GET longmenu.aspx        -> items, station/category, serving size, diet/allergen icons
  4. POST nutRpt.aspx         -> calories/protein/carbs/sugar/fat for every item, one request
  5. GET label.aspx (lazy)    -> full label: ingredients + official allergen statement

All numbers come from FoodPro. Nothing is estimated here.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import date as Date
from urllib.parse import urlencode

import httpx2 as httpx
from bs4 import BeautifulSoup

from . import config

log = logging.getLogger(__name__)

SNAME = "UC Santa Cruz Dining"
WEEKS_MENUS = "UCSC - This Week's Menus"

# FoodPro icon file name -> (kind, label). NB: "gluten.gif" is a "GF" badge,
# i.e. it means *gluten-free*, not "contains gluten".
ICONS: dict[str, tuple[str, str]] = {
    "veggie": ("diet", "Vegetarian"),
    "vegan": ("diet", "Vegan"),
    "gluten": ("diet", "Gluten-free"),
    "halal": ("diet", "Halal"),
    "eggs": ("allergen", "Egg"),
    "milk": ("allergen", "Milk"),
    "soy": ("allergen", "Soy"),
    "wheat": ("allergen", "Wheat"),
    "nuts": ("allergen", "Peanut"),
    "treenut": ("allergen", "Tree nut"),
    "sesame": ("allergen", "Sesame"),
    "fish": ("allergen", "Fish"),
    "shellfish": ("allergen", "Shellfish"),
    "pork": ("contains", "Pork"),
    "beef": ("contains", "Beef"),
    "alcohol": ("contains", "Alcohol"),
}


class FoodProError(Exception):
    pass


# ----------------------------------------------------------------------------- parsing
def _num(text: str | None) -> float | None:
    if text is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(text).replace("\xa0", " ")).strip()


def _rec_from_href(href: str) -> str | None:
    m = re.search(r"RecNumAndPort=([^&'\"]+)", href)
    return htmllib.unescape(m.group(1)).replace("%2a", "*").replace("%2A", "*") if m else None


def parse_short_menu(page: str) -> dict:
    """Return {"title": str|None, "meals": {meal_name: item_count}} in page order."""
    soup = BeautifulSoup(page, "html.parser")
    if "Runtime Error" in (soup.title.string if soup.title and soup.title.string else ""):
        raise FoodProError("FoodPro returned its error page")
    meals: dict[str, int] = {}
    current = None
    for el in soup.find_all(class_=["shortmenumeals", "shortmenurecipes"]):
        classes = el.get("class") or []
        if "shortmenumeals" in classes:
            current = _clean(el.get_text())
            meals.setdefault(current, 0)
        elif current is not None:
            meals[current] += 1
    title_el = soup.find(string=re.compile(r"Menus for"))
    return {"title": _clean(title_el) if title_el else None, "meals": meals}


def parse_long_menu(page: str) -> list[dict]:
    """Items for one meal: rec id, name, category (station), portion text, icons."""
    soup = BeautifulSoup(page, "html.parser")
    items: list[dict] = []
    category = None
    for el in soup.find_all(class_=["longmenucolmenucat", "longmenucoldispname"]):
        classes = el.get("class") or []
        if "longmenucolmenucat" in classes:
            category = _clean(el.get_text()).strip("- ").strip()
            continue
        a = el.find("a")
        if not a or not a.get("href"):
            continue
        rec = _rec_from_href(a["href"])
        if not rec:
            continue
        row = el.find_parent("tr")
        # walk up to the outer row that also holds the portion cell
        outer = row
        portion = None
        while outer is not None:
            p = outer.find(class_="longmenucolportions")
            if p is not None:
                portion = _clean(p.get_text())
                break
            outer = outer.find_parent("tr")
        icon_scope = row if row is not None else el
        icons = []
        for img in icon_scope.find_all("img"):
            m = re.search(r"LegendImages/(\w+)\.gif", img.get("src", ""))
            if m and m.group(1) not in icons:
                icons.append(m.group(1))
        items.append(
            {
                "rec": rec,
                "name": _clean(a.get_text()),
                "category": category or "Other",
                "portion": portion or "",
                "icons": icons,
            }
        )
    return items


_NUT_COLS = {"cals": "kcal", "prot": "protein", "carb": "carbs", "sugar": "sugar", "fat-t": "fat"}


def parse_nut_report(page: str) -> dict[str, dict]:
    """Nutrition Calculator report -> {rec: {portion, kcal, protein, carbs, sugar, fat}}.

    Column order is read from the header row so a reordered report still parses.
    Values are per 1 serving because we always request Qty=1.
    """
    soup = BeautifulSoup(page, "html.parser")
    raw = [_clean(el.get_text()).lower() for el in soup.find_all(class_="nutrptnutheadcolumns")]
    # each header cell is emitted twice in the markup; keep first occurrences
    headers: list[str] = []
    for h in raw:
        if h not in headers:
            headers.append(h)
    if not headers:  # fall back to the header text sequence
        m = re.search(r"Qty\s+(.*?)\s+kcal", soup.get_text(" "), re.S)
        headers = m.group(1).lower().split() if m else []
    value_keys = [_NUT_COLS.get(h) for h in headers] if headers else ["kcal", "protein", "carbs", "sugar", "fat"]
    out: dict[str, dict] = {}
    for name_div in soup.find_all(class_="nutrptnames"):
        a = name_div.find("a")
        if not a:
            continue
        rec = _rec_from_href(a.get("href", ""))
        tr = name_div.find_parent("tr")
        if not rec or tr is None:
            continue
        values = [_num(v.get_text()) for v in tr.find_all(class_="nutrptvalues")]
        portion_el = tr.find(class_="nutrptportions")
        row = {"portion": _clean(portion_el.get_text()) if portion_el else "", "name": _clean(a.get_text())}
        for key, val in zip(value_keys, values):
            if key:
                row[key] = val
        out[rec] = row
    return out


_LABEL_FIELDS = {
    "fat": r"Total Fat",
    "sat_fat": r"Sat\. Fat",
    "trans_fat": r"Trans Fat",
    "cholesterol_mg": r"Cholesterol",
    "sodium_mg": r"Sodium",
    "carbs": r"Tot\. Carb\.",
    "fiber": r"Dietary Fiber",
    "sugar": r"Sugars",
    "protein": r"Protein",
}


def parse_label(page: str) -> dict:
    soup = BeautifulSoup(page, "html.parser")
    text = _clean(soup.get_text(" "))
    if "Nutrition Facts" not in text:
        raise FoodProError("No nutrition label on page")
    out: dict = {}
    name_el = soup.find(class_="labelrecipe")
    out["name"] = _clean(name_el.get_text()) if name_el else None
    m = re.search(r"Serving Size\s+(.+?)\s+Calories", text)
    out["serving"] = m.group(1).strip() if m else None
    m = re.search(r"Calories\s+([\d.]+)", text)
    out["kcal"] = float(m.group(1)) if m else None
    for key, label in _LABEL_FIELDS.items():
        m = re.search(label + r"\s+([\d.]+)\s*(?:g|mg)", text)
        out[key] = float(m.group(1)) if m else None
    m = re.search(r"INGREDIENTS:\s*(.*?)\s*(?:ALLERGENS:|The nutrient composition)", text, re.S)
    out["ingredients"] = m.group(1).strip() if m else None
    m = re.search(r"ALLERGENS:\s*(.*?)\s*The nutrient composition", text, re.S)
    out["allergens"] = m.group(1).strip() if m else None
    return out


# ----------------------------------------------------------------------------- client
def build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        try:
            import certifi

            ctx.load_verify_locations(cafile=certifi.where())
        except ImportError:  # pragma: no cover
            pass
    if config.FOODPRO_EXTRA_CA.exists():
        ctx.load_verify_locations(cafile=str(config.FOODPRO_EXTRA_CA))
    return ctx


def fp_date(d: Date) -> str:
    return f"{d.month}/{d.day}/{d.year}"


@dataclass
class MealMenu:
    meal: str
    items: list[dict] = field(default_factory=list)


class FoodProClient:
    def __init__(self, base_url: str = config.FOODPRO_BASE, delay: float = config.REQUEST_DELAY_S,
                 retries: int = 3, transport: httpx.BaseTransport | None = None):
        kwargs = dict(
            base_url=base_url,
            headers={"User-Agent": config.USER_AGENT},
            timeout=httpx.Timeout(30.0, connect=15.0),
            follow_redirects=True,
        )
        if transport is not None:
            kwargs["transport"] = transport
        else:
            kwargs["verify"] = build_ssl_context()
        self._http = httpx.Client(**kwargs)
        self.delay = delay
        self.retries = retries
        self._last = 0.0
        self._primed = False

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- low level
    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _prime(self) -> None:
        self._throttle()
        self._http.get("/").raise_for_status()
        self._primed = True

    def _request(self, method: str, path: str, **kw) -> str:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                if not self._primed:
                    self._prime()
                self._throttle()
                r = self._http.request(method, path, **kw)
                if r.status_code >= 500 or "Runtime Error" in r.text[:2000]:
                    # FoodPro answers with an ASP.NET error page when its session
                    # cookies are missing/expired: re-prime and retry.
                    self._primed = False
                    raise FoodProError(f"FoodPro error page (HTTP {r.status_code})")
                r.raise_for_status()
                return r.text
            except (httpx.TransportError, httpx.HTTPStatusError, FoodProError) as exc:
                last_exc = exc
                log.warning("FoodPro %s %s failed (attempt %d): %s", method, path, attempt + 1, exc)
                time.sleep(min(2 ** attempt * 2, 20))
        raise FoodProError(f"FoodPro request failed after {self.retries} attempts: {last_exc}")

    @staticmethod
    def _loc_params(loc: str) -> dict:
        return {
            "sName": SNAME,
            "locationNum": loc,
            "locationName": config.DINING_HALLS.get(loc, loc),
            "naFlag": "1",
        }

    # -- high level
    def fetch_meals(self, loc: str, d: Date) -> dict:
        params = self._loc_params(loc) | {"WeeksMenus": WEEKS_MENUS, "myaction": "read", "dtdate": fp_date(d)}
        return parse_short_menu(self._request("GET", "shortmenu.aspx", params=params))

    def fetch_meal(self, loc: str, d: Date, meal: str) -> list[dict]:
        params = self._loc_params(loc) | {"WeeksMenus": WEEKS_MENUS, "dtdate": fp_date(d), "mealName": meal}
        items = parse_long_menu(self._request("GET", "longmenu.aspx", params=params))
        if not items:
            return []
        # One POST returns macros for every item in the meal (the site's own
        # "Nutrition Calculator"), instead of one request per item.
        form: list[tuple[str, str]] = [("Action", "")]
        seen = set()
        for it in items:
            if it["rec"] in seen:
                continue
            seen.add(it["rec"])
            form.append(("recipe", f"{it['rec']}*01"))
            form.append(("QTY", "1"))
        rpt_params = {
            "locationNum": loc,
            "locationName": config.DINING_HALLS.get(loc, loc),
            "dtdate": fp_date(d),
            "mealName": meal,
            "sName": SNAME,
        }
        body = urlencode(form).encode()
        nut = parse_nut_report(self._request(
            "POST", "nutRpt.aspx", params=rpt_params, content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ))
        for it in items:
            n = nut.get(it["rec"])
            for k in ("kcal", "protein", "carbs", "sugar", "fat"):
                it[k] = n.get(k) if n else None
        return items

    def fetch_day(self, loc: str, d: Date) -> dict:
        """Returns {"status": "ok"|"unpublished", "meals": {meal: [items]}}."""
        summary = self.fetch_meals(loc, d)
        meals: dict[str, list[dict]] = {}
        for meal, count in summary["meals"].items():
            if count == 0:
                continue
            meals[meal] = self.fetch_meal(loc, d, meal)
        status = "ok" if any(meals.values()) else "unpublished"
        return {"status": status, "meals": meals}

    def fetch_label(self, loc: str, d: Date, rec: str) -> dict:
        params = {
            "locationNum": loc,
            "locationName": config.DINING_HALLS.get(loc, loc),
            "dtdate": fp_date(d),
            "RecNumAndPort": rec,
        }
        return parse_label(self._request("GET", "label.aspx", params=params))
