"""Central configuration. Everything secret comes from environment variables."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "app.db"))

TZ = "America/Los_Angeles"

# --- FoodPro (UCSC's public menu + nutrition system) ---------------------------
FOODPRO_BASE = "https://nutrition.sa.ucsc.edu/"
# Identify ourselves honestly; do not impersonate a browser.
USER_AGENT = os.environ.get(
    "FOODPRO_USER_AGENT", "slug-macros/0.1 (personal student meal planner)"
)
# Minimum spacing between requests to the UCSC server (seconds).
REQUEST_DELAY_S = float(os.environ.get("FOODPRO_DELAY", "1.5"))
# nutrition.sa.ucsc.edu serves the wrong intermediate certificate; browsers fix
# this via AIA fetching. We ship the missing (public) intermediate instead of
# disabling verification.
FOODPRO_EXTRA_CA = BASE_DIR / "certs" / "incommon-ecc-ov-ssl-ca-3.pem"

DINING_HALLS: dict[str, str] = {
    "40": "John R. Lewis & College Nine Dining Hall",
    "05": "Cowell & Stevenson Dining Hall",
    "20": "Crown & Merrill Dining Hall",
    "25": "Porter & Kresge Dining Hall",
    "30": "Rachel Carson & Oakes Dining Hall",
}

# --- App / auth -------------------------------------------------------------------
APP_PASSCODE = os.environ.get("APP_PASSCODE", "")


def _secret_key() -> str:
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    path = DATA_DIR / ".secret_key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_hex(32)
    path.write_text(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


SECRET_KEY = _secret_key()

# --- Automation ---------------------------------------------------------------------
PLAN_HOUR = int(os.environ.get("PLAN_HOUR", "20"))  # 8 PM Pacific
ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "1") == "1"

# --- Claude (optional; only writes the friendly plan explanation) -----------------
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
LLM_ENABLED = bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("LLM_ENABLED", "1") == "1"

# --- Default profile (editable in Settings; these are starting estimates) --------
DEFAULT_PROFILE: dict = {
    "age": 19,
    "sex": "male",
    "height_in": 70,
    "start_weight_lb": 184.0,
    "goal_weight_lb": 170.0,
    "target_rate_lb_per_week": 1.25,
    "calories": 2150,
    "protein_g": 160,
    "fat_g": 65,
    # Extra calories on training days (added as carbohydrate) – 0 disables.
    "training_day_extra_kcal": 150,
    # 0=Mon … 6=Sun. Empty until you pick a schedule.
    "training_weekdays": [],
    "default_hall": "05",  # Cowell & Stevenson
    # Every word must appear in the item name for it to be excluded.
    "avoid_rules": [["scrambled", "egg"]],
    "use_breakfast_template": True,
    "include_snack": True,
}
