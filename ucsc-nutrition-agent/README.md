# Slug Macros: UCSC dining hall meal planner

A personal meal-planning app for UC Santa Cruz dining halls. Every night at 8 PM Pacific it pulls
tomorrow's real menu and nutrition data from UCSC Dining. It then builds a day of meals that hits
your calorie and protein targets, starting from your usual breakfast. You track it from your iPhone.

## Where the data comes from (Phase 1 findings)

| Source | Verdict |
|---|---|
| **UCSC FoodPro**: `https://nutrition.sa.ucsc.edu/` (public, no login, no robots.txt restrictions) | ✅ **Used.** Official source for UCSC dining menus and nutrition. |
| Official API / JSON feed | ❌ None exists. FoodPro only serves HTML pages. |
| Slug Dining iOS app | Not used. It shows the same FoodPro data, and reading app traffic isn't needed. |

How the app reads FoodPro, per dining hall per day:

1. `GET /` sets the session cookies FoodPro requires (without them every page is an ASP.NET error).
2. `shortmenu.aspx` lists the meals for that day. Meals with zero items mean **not published yet**.
3. `longmenu.aspx` gives items, station, serving size and diet/allergen icons.
4. `POST nutRpt.aspx` is the site's own "Nutrition Calculator". **One request returns calories,
   protein, carbs, sugar and fat for every item in the meal.**
5. `label.aspx` (on demand) gives the full label: ingredients, the official allergen statement, fiber
   and sodium. It's also a fallback when the bulk report is missing an item's numbers.

That's about 10 requests per hall per day, spaced 1.5 s apart, from an honestly named client
(`slug-macros/0.1`). No authentication or protection is bypassed.

**Quirks handled:**
- The server sends the wrong intermediate TLS certificate. Browsers silently repair this; Python
  doesn't. The missing public certificate (*InCommon ECC OV SSL CA 3*, signed by Sectigo) ships in
  `app/certs/`. Verification stays on.
- The calculator stores its "cart" in cookies that grow with each report until FoodPro returns
  400/502 errors. The client clears them after each report.
- The `gluten` icon means **gluten-free**, not "contains gluten".
- Some items have no numbers, and some labels are all zeros even though the ingredients list sugar
  and oil (e.g. the hazelnut creamer). Those are shown as **"No nutrition data"** and not counted.
  Nothing is estimated.
- Duplicate listings are removed, and the menu is re-checked at 6:30 AM and 1 PM for changes.

## What it does

- **Plans** breakfast, lunch, dinner and a snack from the real menu of the hall you pick. It uses
  your usual breakfast when the items exist (sourdough, turkey, hard-boiled egg, Greek yogurt with
  strawberries and honey, banana, coffee with hazelnut creamer). When an item is missing it says
  what's missing and what it substituted. Scrambled eggs are never suggested.
- **The math is deterministic:** every number is FoodPro's per-serving value × the quantity. A
  small optimizer chooses a protein, a starch and a vegetable, with portions, to land near your
  targets. Claude (optional) only writes a 2–4 sentence explanation from those computed numbers.
- **Swap** any item for alternatives from the same meal, with portions matched to keep your totals
  close. Change portions, mark items eaten, and see remaining calories and macros update.
- **Training vs rest days** (+150 kcal from carbs on training days, configurable).
- **Learns** from your 👍/👎, swaps and what you actually eat.
- **Tracks** food from outside the dining hall (marked "you entered"), plus morning weigh-ins with a
  7-day average, weekly averages and a trend chart.
- **Suggests calorie changes only from a 3-week trend**, with at least 10 weigh-ins and good logging,
  and only applies them when you tap Approve. A single weigh-in never changes anything.

## Run it locally

```bash
cd ucsc-nutrition-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000      # open http://localhost:8000
pytest -q                                      # 15 tests, offline (real captured FoodPro pages)
```

Command-line helpers:

```bash
python -m app.cli menu 05 2026-11-12          # print a hall's menu with macros
python -m app.cli plan 2026-11-12 --hall 05   # build + save a plan and print it
python -m app.cli prepare-tomorrow            # what the 8 PM job does
```

Hall codes: `05` Cowell & Stevenson (default), `40` John R. Lewis & College Nine, `20` Crown & Merrill,
`25` Porter & Kresge, `30` Rachel Carson & Oakes.

### Settings (environment variables)

| Variable | Purpose |
|---|---|
| `APP_PASSCODE` | **Required when deployed.** Passcode for the login screen. |
| `SECRET_KEY` | Optional; signs the login cookie. Auto-generated in the data dir if unset. |
| `ANTHROPIC_API_KEY` | Optional. Enables the short Claude-written plan explanation. |
| `CLAUDE_MODEL` | Defaults to `claude-opus-5-5`. |
| `DATA_DIR` | Where the SQLite database lives (`./data` locally, `/data` on Fly). |
| `PLAN_HOUR` | Hour (Pacific) to build tomorrow's plan. Default `20`. |
| `ENABLE_SCHEDULER` | `0` to disable the in-app scheduler (then use an external cron + `app.cli`). |

Set secrets as host secrets (`fly secrets set …`), never in the repo.

## Deploy so it works on your iPhone (Fly.io)

The app needs an always-on server (so the 8 PM job runs) and a small disk (for your logs). A Fly.io
machine with 512 MB of memory and a 1 GB volume costs a few dollars a month. Check Fly's pricing
page for current rates.

1. Install the CLI and sign in: <https://fly.io/docs/flyctl/install/>, then `fly auth login`.
2. In `fly.toml`, change `app = "slug-macros-change-me"` to a unique name.
3. From the `ucsc-nutrition-agent` folder:

   ```bash
   fly apps create <your-app-name>
   fly volumes create slug_data --region sjc --size 1
   fly secrets set APP_PASSCODE='pick-something-long'
   fly secrets set ANTHROPIC_API_KEY='sk-ant-...'   # optional
   fly deploy
   ```

4. Open `https://<your-app-name>.fly.dev` in **Safari** on your iPhone and log in.
   Then tap **Share → Add to Home Screen**. It opens full-screen like an app.
5. Check it's running: `fly logs` should show `scheduler started`. The Settings tab lists the next
   8 PM run and recent job results.

Keep a single machine (`fly scale count 1`): the scheduler runs inside the web process.

## Troubleshooting

- **"No menu is posted for this hall on this date"**: UCSC hasn't published it, or the hall is closed
  that day (e.g. Porter/Kresge on some Saturdays). The app retries at 10 PM, midnight and 2 AM,
  and you can switch halls.
- **`certificate verify failed` for nutrition.sa.ucsc.edu**: make sure `app/certs/` was deployed.
  If UCSC renews its certificate under a different intermediate, replace the file with the one
  named in the server certificate's "CA Issuers" URL.
- **First load of a day is slow (about a minute)**: that's the polite, rate-limited menu download.
  After that it's cached, and the 8 PM job pre-loads tomorrow.
- **The menu changed after the plan was made**: untouched plans regenerate automatically. If you've
  edited the plan, missing items show **"No longer listed"** so you can swap them.

## Layout

```
app/
  foodpro.py    FoodPro client + HTML parsers (all data retrieval)
  menus.py      menu sync, cache, change detection, label fallback
  planner.py    targets, breakfast template, optimizer, swaps (deterministic)
  service.py    saving plans, edits, eaten tracking, food log, feedback
  progress.py   weight trend, weekly averages, suggested adjustments
  scheduler.py  8 PM plan + menu refresh jobs
  llm.py        optional Claude explanation (never produces numbers)
  main.py       FastAPI routes + auth
  static/       mobile dashboard (HTML/CSS/JS, installable)
tests/          parsers, planner and API tests against captured FoodPro pages
```

Nutrition numbers come from UCSC Dining's FoodPro system, which notes they are approximations.
This is a personal planning tool, not medical advice.
