"""Background automation (APScheduler, runs inside the web process).

- 8:00 PM Pacific: fetch tomorrow's menu for your default hall and build the plan.
  If the menu isn't published yet, retry every 2 hours until 2 AM.
- 6:30 AM Pacific: refresh today's menu; regenerate the plan if the menu changed
  and you haven't edited it, otherwise flag items that disappeared.
- 1:00 PM Pacific: same refresh check (menus are sometimes revised mid-day).

Run the app with a single worker so jobs don't run twice. Alternatively disable
the in-process scheduler (ENABLE_SCHEDULER=0) and call `python -m app.cli prepare-tomorrow`
from an external cron.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from . import config, db, menus, service

log = logging.getLogger(__name__)
TZ = ZoneInfo(config.TZ)
_job_lock = threading.Lock()
scheduler: BackgroundScheduler | None = None


def local_today():
    return datetime.now(TZ).date()


def prepare_tomorrow(attempt: int = 1) -> str:
    with _job_lock:
        d = local_today() + timedelta(days=1)
        loc = db.get_profile()["default_hall"]
        fetch = menus.sync_menu(loc, d)
        if fetch["status"] == "ok":
            existing = service.load_plan(d)
            if existing and existing.get("user_edited") and existing.get("loc") == loc:
                msg = f"{d}: menu refreshed; kept your edited plan ({service.reconcile_with_menu(d)})"
            else:
                plan = service.generate_plan(d, loc)
                msg = f"{d}: plan ready ({len(plan['items'])} items)"
        else:
            msg = f"{d}: menu {fetch['status']} (attempt {attempt})"
            existing = service.load_plan(d)
            if not existing or existing.get("status") != "ok":  # never clobber a good plan
                service.save_plan(service.planner.generate(menus.get_menu(loc, d), db.get_profile(), d))
            now = datetime.now(TZ)
            if scheduler and attempt < 4 and now.hour >= 12:  # retry 10pm, midnight, 2am
                scheduler.add_job(prepare_tomorrow, DateTrigger(now + timedelta(hours=2)), kwargs={"attempt": attempt + 1},
                                  id="prepare_retry", replace_existing=True)
                msg += "; retrying in 2h"
        db.log_job("prepare_tomorrow", msg)
        log.info(msg)
        return msg


def refresh_today() -> str:
    with _job_lock:
        d = local_today()
        plan = service.load_plan(d)
        loc = plan["loc"] if plan else db.get_profile()["default_hall"]
        fetch = menus.sync_menu(loc, d)
        if fetch["status"] == "ok" and not plan:
            service.generate_plan(d, loc)
            msg = f"{d}: no plan existed, generated one"
        elif plan and plan.get("status") != "ok" and fetch["status"] == "ok":
            service.generate_plan(d, loc)
            msg = f"{d}: menu now published, plan generated"
        else:
            msg = f"{d}: {service.reconcile_with_menu(d)}"
        db.log_job("refresh_today", msg)
        log.info(msg)
        return msg


def _safe(fn):
    def run(**kw):
        try:
            fn(**kw)
        except Exception as exc:  # never let a job crash the scheduler
            log.exception("job %s failed", fn.__name__)
            db.log_job(fn.__name__, f"failed: {exc}")
    run.__name__ = fn.__name__
    return run


def start() -> None:
    global scheduler
    if scheduler or not config.ENABLE_SCHEDULER:
        return
    scheduler = BackgroundScheduler(timezone=TZ, job_defaults={"coalesce": True, "misfire_grace_time": 3600})
    scheduler.add_job(_safe(prepare_tomorrow), CronTrigger(hour=config.PLAN_HOUR, minute=0, timezone=TZ), id="prepare_tomorrow")
    scheduler.add_job(_safe(refresh_today), CronTrigger(hour=6, minute=30, timezone=TZ), id="refresh_morning")
    scheduler.add_job(_safe(refresh_today), CronTrigger(hour=13, minute=0, timezone=TZ), id="refresh_midday")
    # Catch up after a restart/deploy.
    now = datetime.now(TZ)
    if now.hour >= config.PLAN_HOUR and service.load_plan(local_today() + timedelta(days=1)) is None:
        scheduler.add_job(_safe(prepare_tomorrow), DateTrigger(now + timedelta(seconds=20)), id="catchup")
    scheduler.start()
    log.info("scheduler started")


def shutdown() -> None:
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None


def next_runs() -> list[dict]:
    if not scheduler:
        return []
    return [{"id": j.id, "next": j.next_run_time.isoformat() if j.next_run_time else None} for j in scheduler.get_jobs()]
