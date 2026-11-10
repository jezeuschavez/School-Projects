"""Command line helpers.

  python -m app.cli menu 40 2026-11-11        # print a hall's menu with macros
  python -m app.cli plan 2026-11-11 [--hall 40]
  python -m app.cli prepare-tomorrow          # same as the 8 PM job
  python -m app.cli refresh-today
"""
from __future__ import annotations

import argparse
from datetime import date

from . import config, db, menus, planner, scheduler, service


def main() -> None:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("menu")
    m.add_argument("hall", choices=sorted(config.DINING_HALLS))
    m.add_argument("date")
    p = sub.add_parser("plan")
    p.add_argument("date")
    p.add_argument("--hall")
    sub.add_parser("prepare-tomorrow")
    sub.add_parser("refresh-today")
    args = ap.parse_args()

    if args.cmd == "menu":
        d = date.fromisoformat(args.date)
        print(menus.sync_menu(args.hall, d))
        menu = menus.get_menu(args.hall, d)
        for meal, items in menu["meals"].items():
            print(f"\n== {meal} ({len(items)} items)")
            for it in items:
                n = "no data" if it["kcal"] is None else f"{it['kcal']:.0f} kcal {it['protein']:.1f}P {it['carbs']:.1f}C {it['fat']:.1f}F"
                print(f"  [{it['category']}] {it['name']} — {it['portion']} — {n}")
    elif args.cmd == "plan":
        d = date.fromisoformat(args.date)
        plan = service.generate_plan(d, args.hall)
        for it in plan["items"]:
            t = planner.totals([it])
            print(f"{it['slot']:9} {it['name']:45} {it['amount_label']:24} {t['kcal']:6.0f} kcal {t['protein']:5.1f} g protein")
        print("TOTAL", planner.totals(plan["items"]), "TARGET", planner.daily_targets(db.get_profile(), plan["day_type"]))
        for n in plan["notes"]:
            print("note:", n)
        if plan.get("summary"):
            print("\n" + plan["summary"])
    elif args.cmd == "prepare-tomorrow":
        print(scheduler.prepare_tomorrow())
    elif args.cmd == "refresh-today":
        print(scheduler.refresh_today())


if __name__ == "__main__":
    main()
