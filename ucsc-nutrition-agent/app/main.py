"""FastAPI app: JSON API + the mobile dashboard (static files)."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from contextlib import asynccontextmanager
from datetime import date as Date, timedelta

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, menus, planner, progress, scheduler, service
from .foodpro import FoodProError, ICONS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
STATIC = config.BASE_DIR / "static"
COOKIE = "slug_session"
SESSION_DAYS = 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    if not config.APP_PASSCODE:
        log.warning("APP_PASSCODE is not set: the app is open to anyone who can reach it. Fine locally, not for deployment.")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Slug Macros", lifespan=lifespan)


# ------------------------------------------------------------------ auth
def _sign(expires: int) -> str:
    mac = hmac.new(config.SECRET_KEY.encode(), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{mac}"


def _valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, mac = token.split(".", 1)
    try:
        ok = hmac.compare_digest(_sign(int(exp)).split(".", 1)[1], mac)
        return ok and int(exp) > time.time()
    except ValueError:
        return False


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if config.APP_PASSCODE and path.startswith("/api/") and path not in ("/api/login", "/api/session"):
        if not _valid(request.cookies.get(COOKIE)):
            return JSONResponse({"detail": "login required"}, status_code=401)
    return await call_next(request)


class Login(BaseModel):
    passcode: str


@app.post("/api/login")
def login(body: Login, response: Response, request: Request):
    if not config.APP_PASSCODE or hmac.compare_digest(body.passcode, config.APP_PASSCODE):
        exp = int(time.time()) + SESSION_DAYS * 86400
        response.set_cookie(COOKIE, _sign(exp), max_age=SESSION_DAYS * 86400, httponly=True,
                            secure=request.url.scheme == "https", samesite="lax")
        return {"ok": True}
    time.sleep(1)  # slow down guessing
    raise HTTPException(401, "wrong passcode")


@app.get("/api/session")
def session(request: Request):
    return {"auth_required": bool(config.APP_PASSCODE), "logged_in": not config.APP_PASSCODE or _valid(request.cookies.get(COOKIE))}


# ------------------------------------------------------------------ helpers
def parse_date(s: str) -> Date:
    if s == "today":
        return scheduler.local_today()
    if s == "tomorrow":
        return scheduler.local_today() + timedelta(days=1)
    try:
        return Date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD, 'today' or 'tomorrow'")


def check_hall(loc: str) -> str:
    if loc not in config.DINING_HALLS:
        raise HTTPException(400, "unknown dining hall")
    return loc


def foodpro_guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except FoodProError as exc:
        raise HTTPException(502, f"UCSC menu site error: {exc}")


# ------------------------------------------------------------------ meta / menus
@app.get("/api/meta")
def meta():
    return {
        "halls": config.DINING_HALLS,
        "icons": {k: {"kind": v[0], "label": v[1]} for k, v in ICONS.items()},
        "today": scheduler.local_today().isoformat(),
        "tomorrow": (scheduler.local_today() + timedelta(days=1)).isoformat(),
        "llm_enabled": config.LLM_ENABLED,
        "jobs": scheduler.next_runs(),
        "recent_jobs": db.query("SELECT * FROM job_log ORDER BY id DESC LIMIT 8"),
    }


@app.get("/api/menu/{date_s}/{loc}")
def get_menu(date_s: str, loc: str, refresh: bool = False):
    d, loc = parse_date(date_s), check_hall(loc)
    foodpro_guard(menus.sync_menu if refresh else menus.ensure_menu, loc, d)
    return menus.get_menu(loc, d)


@app.get("/api/label/{date_s}/{loc}/{rec}")
def get_label(date_s: str, loc: str, rec: str):
    return foodpro_guard(menus.get_label, check_hall(loc), parse_date(date_s), rec)


# ------------------------------------------------------------------ plans
class GenerateBody(BaseModel):
    loc: str | None = None
    day_type: str | None = Field(None, pattern="^(rest|training)$")
    refresh_menu: bool = False


@app.get("/api/day/{date_s}")
def get_day(date_s: str, create: bool = True):
    d = parse_date(date_s)
    if create and service.load_plan(d) is None:
        foodpro_guard(service.generate_plan, d)
    return service.day_view(d)


@app.post("/api/day/{date_s}/generate")
def generate(date_s: str, body: GenerateBody):
    d = parse_date(date_s)
    loc = check_hall(body.loc) if body.loc else None
    foodpro_guard(service.generate_plan, d, loc, body.day_type, body.refresh_menu)
    return service.day_view(d)


class DayType(BaseModel):
    day_type: str = Field(pattern="^(rest|training)$")


@app.post("/api/day/{date_s}/day-type")
def day_type(date_s: str, body: DayType):
    d = parse_date(date_s)
    foodpro_guard(service.set_day_type, d, body.day_type)
    return service.day_view(d)


class QtyBody(BaseModel):
    qty: float = Field(gt=0, le=10)


def _plan_or_404(d: Date):
    if service.load_plan(d) is None:
        raise HTTPException(404, "no plan for that date")


@app.patch("/api/day/{date_s}/items/{item_id}")
def set_qty(date_s: str, item_id: str, body: QtyBody):
    d = parse_date(date_s)
    _plan_or_404(d)
    try:
        service.set_qty(d, item_id, body.qty)
    except KeyError:
        raise HTTPException(404, "item not found")
    return service.day_view(d)


@app.delete("/api/day/{date_s}/items/{item_id}")
def remove_item(date_s: str, item_id: str):
    d = parse_date(date_s)
    _plan_or_404(d)
    try:
        service.remove_item(d, item_id)
    except KeyError:
        raise HTTPException(404, "item not found")
    return service.day_view(d)


class AddItem(BaseModel):
    slot: str = Field(pattern="^(breakfast|brunch|lunch|dinner|snack)$")
    meal: str
    rec: str
    qty: float = Field(1, gt=0, le=10)


@app.post("/api/day/{date_s}/items")
def add_item(date_s: str, body: AddItem):
    d = parse_date(date_s)
    _plan_or_404(d)
    try:
        service.add_menu_item(d, body.slot, body.meal, body.rec, body.qty)
    except KeyError:
        raise HTTPException(404, "menu item not found")
    return service.day_view(d)


@app.get("/api/day/{date_s}/items/{item_id}/alternatives")
def alternatives(date_s: str, item_id: str):
    d = parse_date(date_s)
    plan = service.load_plan(d)
    if not plan:
        raise HTTPException(404, "no plan")
    return planner.alternatives(plan, item_id, menus.get_menu(plan["loc"], d), db.get_profile())


class SwapBody(BaseModel):
    rec: str
    qty: float = Field(gt=0, le=10)
    dislike: bool = False


@app.post("/api/day/{date_s}/items/{item_id}/swap")
def swap(date_s: str, item_id: str, body: SwapBody):
    d = parse_date(date_s)
    _plan_or_404(d)
    try:
        service.swap_item(d, item_id, body.rec, body.qty, body.dislike)
    except KeyError:
        raise HTTPException(404, "item not found")
    return service.day_view(d)


class EatenBody(BaseModel):
    eaten: bool


@app.post("/api/day/{date_s}/items/{item_id}/eaten")
def eaten(date_s: str, item_id: str, body: EatenBody):
    d = parse_date(date_s)
    _plan_or_404(d)
    try:
        service.set_eaten(d, item_id, body.eaten)
    except KeyError:
        raise HTTPException(404, "item not found")
    return service.day_view(d)


# ------------------------------------------------------------------ food log
class CustomLog(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kcal: float = Field(ge=0, le=5000)
    protein: float = Field(ge=0, le=500)
    carbs: float = Field(0, ge=0, le=1000)
    fat: float = Field(0, ge=0, le=500)
    qty: float = Field(1, gt=0, le=20)
    serving: str | None = None
    slot: str | None = None
    save_food: bool = False


@app.post("/api/day/{date_s}/log")
def log_custom(date_s: str, body: CustomLog):
    d = parse_date(date_s)
    service.log_custom(d, body.name, body.kcal, body.protein, body.carbs, body.fat, body.qty, body.slot,
                       body.save_food, body.serving)
    return service.day_view(d)


class MenuLog(BaseModel):
    loc: str
    meal: str
    rec: str
    qty: float = Field(1, gt=0, le=10)
    slot: str | None = None


@app.post("/api/day/{date_s}/log-menu")
def log_menu(date_s: str, body: MenuLog):
    d = parse_date(date_s)
    try:
        service.log_menu_item(d, check_hall(body.loc), body.meal, body.rec, body.qty, body.slot)
    except KeyError:
        raise HTTPException(404, "menu item not found or has no nutrition data")
    return service.day_view(d)


@app.delete("/api/log/{log_id}")
def delete_log(log_id: int):
    row = db.query_one("SELECT * FROM food_log WHERE id=?", (log_id,))
    if not row:
        raise HTTPException(404)
    if row["source"] == "plan":
        raise HTTPException(400, "untick the plan item instead")
    db.execute("DELETE FROM food_log WHERE id=?", (log_id,))
    return service.day_view(Date.fromisoformat(row["date"]))


@app.get("/api/custom-foods")
def custom_foods():
    return db.query("SELECT * FROM custom_foods ORDER BY name")


# ------------------------------------------------------------------ feedback
class Feedback(BaseModel):
    name: str
    rating: int = Field(ge=-1, le=1)


@app.post("/api/feedback")
def feedback(body: Feedback):
    service.record_feedback(body.name, rating=body.rating)
    return {"ok": True}


@app.get("/api/feedback")
def list_feedback():
    return db.query("SELECT * FROM feedback WHERE rating != 0 ORDER BY rating DESC, name")


# ------------------------------------------------------------------ weight / progress
class WeighIn(BaseModel):
    date: str
    weight: float = Field(gt=60, lt=500)


@app.post("/api/weights")
def add_weight(body: WeighIn):
    d = parse_date(body.date)
    db.execute(
        "INSERT INTO weigh_ins(date,weight,created_at) VALUES(?,?,?) ON CONFLICT(date) DO UPDATE SET weight=excluded.weight",
        (d.isoformat(), body.weight, db.now_iso()),
    )
    return progress.analyze(db.get_profile(), scheduler.local_today())


@app.delete("/api/weights/{date_s}")
def delete_weight(date_s: str):
    db.execute("DELETE FROM weigh_ins WHERE date=?", (parse_date(date_s).isoformat(),))
    return progress.analyze(db.get_profile(), scheduler.local_today())


@app.get("/api/progress")
def get_progress():
    return progress.analyze(db.get_profile(), scheduler.local_today())


class Resolve(BaseModel):
    approve: bool


@app.post("/api/adjustments/{adj_id}")
def resolve_adjustment(adj_id: int, body: Resolve):
    try:
        return progress.resolve(adj_id, body.approve)
    except KeyError:
        raise HTTPException(404)


# ------------------------------------------------------------------ settings
class SettingsBody(BaseModel):
    calories: int | None = Field(None, ge=1200, le=5000)
    protein_g: int | None = Field(None, ge=50, le=350)
    fat_g: int | None = Field(None, ge=30, le=200)
    training_day_extra_kcal: int | None = Field(None, ge=0, le=800)
    training_weekdays: list[int] | None = None
    default_hall: str | None = None
    goal_weight_lb: float | None = Field(None, gt=80, lt=400)
    start_weight_lb: float | None = Field(None, gt=80, lt=400)
    target_rate_lb_per_week: float | None = Field(None, ge=0, le=2.5)
    avoid_rules: list[list[str]] | None = None
    use_breakfast_template: bool | None = None
    include_snack: bool | None = None


@app.get("/api/settings")
def get_settings():
    return db.get_profile()


@app.put("/api/settings")
def put_settings(body: SettingsBody):
    update = body.model_dump(exclude_none=True)
    if "default_hall" in update:
        check_hall(update["default_hall"])
    if "training_weekdays" in update:
        update["training_weekdays"] = sorted({d for d in update["training_weekdays"] if 0 <= d <= 6})
    if "avoid_rules" in update:
        update["avoid_rules"] = [[w.strip().lower() for w in r if w.strip()] for r in update["avoid_rules"] if any(w.strip() for w in r)]
    return db.save_profile(update)


# ------------------------------------------------------------------ static UI
@app.get("/healthz")
def healthz():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")
