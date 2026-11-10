"""Weight trend analysis and *suggested* calorie adjustments (never automatic).

Rules:
- Need >= 10 weigh-ins spanning >= 14 days before suggesting anything.
- Trend = least-squares slope over the last 21 days (robust to daily water swings).
- Only suggest if food logging shows reasonable adherence (otherwise the fix is
  logging/adherence, not a lower target).
- At most one pending suggestion; at least 14 days between suggestions.
"""
from __future__ import annotations

from datetime import date as Date, datetime, timedelta, timezone

from . import db

MIN_KCAL = 1800  # don't suggest going below this for an active 19-year-old male


def weigh_ins() -> list[dict]:
    return db.query("SELECT date, weight FROM weigh_ins ORDER BY date")


def _slope_per_week(points: list[tuple[Date, float]]) -> float | None:
    if len(points) < 2:
        return None
    x0 = points[0][0]
    xs = [(d - x0).days for d, _ in points]
    ys = [w for _, w in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den * 7


def rolling_avg(rows: list[dict], window: int = 7) -> list[dict]:
    out = []
    pts = [(Date.fromisoformat(r["date"]), r["weight"]) for r in rows]
    for d, w in pts:
        win = [x for dd, x in pts if d - timedelta(days=window - 1) <= dd <= d]
        out.append({"date": d.isoformat(), "weight": w, "avg7": round(sum(win) / len(win), 1)})
    return out


def weekly_averages(rows: list[dict]) -> list[dict]:
    weeks: dict[str, list[float]] = {}
    for r in rows:
        d = Date.fromisoformat(r["date"])
        monday = (d - timedelta(days=d.weekday())).isoformat()
        weeks.setdefault(monday, []).append(r["weight"])
    out, prev = [], None
    for wk in sorted(weeks):
        avg = round(sum(weeks[wk]) / len(weeks[wk]), 1)
        out.append({"week_of": wk, "avg": avg, "n": len(weeks[wk]), "change": round(avg - prev, 1) if prev else None})
        prev = avg
    return out


def adherence(start: Date, end: Date, target_kcal: float) -> dict:
    rows = db.query(
        "SELECT date, SUM(kcal) AS kcal, SUM(protein) AS protein FROM food_log WHERE date>=? AND date<=? GROUP BY date",
        (start.isoformat(), end.isoformat()),
    )
    days = (end - start).days + 1
    logged = [r for r in rows if (r["kcal"] or 0) > 800]  # ignore obviously partial days
    avg = sum(r["kcal"] for r in logged) / len(logged) if logged else None
    return {"days": days, "logged_days": len(logged), "avg_kcal": round(avg) if avg else None,
            "avg_protein": round(sum(r["protein"] for r in logged) / len(logged)) if logged else None,
            "on_target": avg is not None and avg <= target_kcal + 150}


def analyze(profile: dict, today: Date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    rows = weigh_ins()
    series = rolling_avg(rows)
    recent = [(Date.fromisoformat(r["date"]), r["weight"]) for r in rows
              if Date.fromisoformat(r["date"]) >= today - timedelta(days=21)]
    rate = _slope_per_week(recent)  # lb/week, negative = losing
    span = (recent[-1][0] - recent[0][0]).days if len(recent) >= 2 else 0
    enough = len(recent) >= 10 and span >= 14
    current = series[-1]["avg7"] if series else None
    goal = profile["goal_weight_lb"]
    loss_rate = -rate if rate is not None else None
    eta_weeks = None
    if current is not None and loss_rate and loss_rate > 0.1 and current > goal:
        eta_weeks = round((current - goal) / loss_rate, 1)
    adh = adherence(today - timedelta(days=13), today, profile["calories"])
    result = {
        "series": series,
        "weekly": weekly_averages(rows),
        "current_avg": current,
        "start_weight": profile["start_weight_lb"],
        "goal_weight": goal,
        "to_go": round(current - goal, 1) if current is not None else None,
        "progress_pct": (round(100 * (profile["start_weight_lb"] - current) / (profile["start_weight_lb"] - goal))
                         if current is not None and profile["start_weight_lb"] != goal else None),
        "rate_lb_per_week": round(loss_rate, 2) if loss_rate is not None and enough else None,
        "eta_weeks": eta_weeks if enough else None,
        "enough_data": enough,
        "adherence": adh,
        "status": None,
        "pending_adjustment": db.query_one("SELECT * FROM adjustments WHERE status='pending' ORDER BY id DESC LIMIT 1"),
    }
    if not enough:
        result["status"] = f"Need about two weeks of morning weigh-ins for a reliable trend ({len(recent)} in the last 21 days)."
        return result
    target_rate = profile["target_rate_lb_per_week"]
    if loss_rate < 0.5:
        result["status"] = "Weight loss has stalled or is slower than planned."
    elif loss_rate > max(1.75, target_rate + 0.5):
        result["status"] = "You're losing faster than planned, which can cost muscle and recovery."
    else:
        result["status"] = "On track."
    maybe_propose(profile, loss_rate, adh, today)
    result["pending_adjustment"] = db.query_one(
        "SELECT * FROM adjustments WHERE status='pending' ORDER BY id DESC LIMIT 1")
    return result


def maybe_propose(profile: dict, loss_rate: float, adh: dict, today: Date) -> None:
    if db.query_one("SELECT id FROM adjustments WHERE status='pending'"):
        return
    last = db.query_one("SELECT created_at FROM adjustments ORDER BY id DESC LIMIT 1")
    if last and datetime.fromisoformat(last["created_at"]).date() > today - timedelta(days=14):
        return
    kcal = int(profile["calories"])
    proposal = reason = None
    if loss_rate < 0.5:
        if adh["logged_days"] >= 9 and adh["on_target"]:
            proposal = max(MIN_KCAL, kcal - 150)
            reason = (f"Your 3-week trend is {loss_rate:.2f} lb/week while you've logged {adh['logged_days']} "
                      f"of the last 14 days near target (avg {adh['avg_kcal']} kcal). A small 150 kcal cut should restart progress.")
    elif loss_rate < 0.9 and adh["logged_days"] >= 9 and adh["on_target"]:
        proposal = max(MIN_KCAL, kcal - 100)
        reason = (f"Trend is {loss_rate:.2f} lb/week vs your {profile['target_rate_lb_per_week']} goal, "
                  f"with good adherence. Consider trimming 100 kcal.")
    elif loss_rate > max(1.75, profile["target_rate_lb_per_week"] + 0.5):
        proposal = kcal + 150
        reason = (f"You're losing {loss_rate:.2f} lb/week. Adding 150 kcal (mostly carbs around training) "
                  f"helps keep strength and muscle while still losing fat.")
    if proposal and proposal != kcal:
        db.execute(
            "INSERT INTO adjustments(created_at,current_kcal,proposed_kcal,reason,status) VALUES(?,?,?,?, 'pending')",
            (db.now_iso(), kcal, proposal, reason),
        )


def resolve(adjustment_id: int, approve: bool) -> dict:
    adj = db.query_one("SELECT * FROM adjustments WHERE id=?", (adjustment_id,))
    if not adj or adj["status"] != "pending":
        raise KeyError(adjustment_id)
    db.execute("UPDATE adjustments SET status=? WHERE id=?", ("approved" if approve else "dismissed", adjustment_id))
    if approve:
        db.save_profile({"calories": adj["proposed_kcal"]})
    return db.query_one("SELECT * FROM adjustments WHERE id=?", (adjustment_id,))
