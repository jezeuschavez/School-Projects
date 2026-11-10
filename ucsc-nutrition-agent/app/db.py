"""SQLite persistence (stdlib only). One file at config.DB_PATH."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS menu_items (
    loc TEXT NOT NULL, date TEXT NOT NULL, meal TEXT NOT NULL, rec TEXT NOT NULL,
    name TEXT NOT NULL, category TEXT, portion TEXT,
    kcal REAL, protein REAL, carbs REAL, sugar REAL, fat REAL,
    nutrition_source TEXT,             -- 'foodpro_report' | 'foodpro_label' | NULL (missing)
    icons TEXT NOT NULL DEFAULT '[]',
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (loc, date, meal, rec)
);
CREATE TABLE IF NOT EXISTS menu_fetches (
    loc TEXT NOT NULL, date TEXT NOT NULL,
    status TEXT NOT NULL,              -- ok | unpublished | error
    content_hash TEXT, error TEXT, fetched_at TEXT NOT NULL,
    PRIMARY KEY (loc, date)
);
CREATE TABLE IF NOT EXISTS labels (
    loc TEXT NOT NULL, date TEXT NOT NULL, rec TEXT NOT NULL, data TEXT NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY (loc, date, rec)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans (
    date TEXT PRIMARY KEY, loc TEXT NOT NULL, data TEXT NOT NULL,
    user_edited INTEGER NOT NULL DEFAULT 0, menu_hash TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS food_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL, slot TEXT, plan_item_id TEXT,
    source TEXT NOT NULL,              -- plan | dining | custom
    name TEXT NOT NULL, rec TEXT, portion TEXT, qty REAL NOT NULL DEFAULT 1,
    kcal REAL, protein REAL, carbs REAL, fat REAL,
    verified INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS food_log_date ON food_log(date);
CREATE TABLE IF NOT EXISTS custom_foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, serving TEXT,
    kcal REAL NOT NULL, protein REAL NOT NULL, carbs REAL NOT NULL, fat REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weigh_ins (date TEXT PRIMARY KEY, weight REAL NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback (
    name_key TEXT PRIMARY KEY, name TEXT NOT NULL,
    rating INTEGER NOT NULL DEFAULT 0,    -- -1 dislike, 0 neutral, 1 like
    eaten_count INTEGER NOT NULL DEFAULT 0,
    swapped_out INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
    current_kcal INTEGER NOT NULL, proposed_kcal INTEGER NOT NULL,
    reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending'   -- pending|approved|dismissed
);
CREATE TABLE IF NOT EXISTS job_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, job TEXT NOT NULL, message TEXT NOT NULL
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None) -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None or path is not None:
            conn = sqlite3.connect(str(path or config.DB_PATH), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            _conn = conn
        return _conn


@contextmanager
def tx():
    conn = connect()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, params=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in connect().execute(sql, params).fetchall()]


def query_one(sql: str, params=()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=()) -> int:
    with tx() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


# ---------------------------------------------------------------- settings / profile
def get_setting(key: str, default=None):
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def get_profile() -> dict:
    stored = get_setting("profile", {}) or {}
    return {**config.DEFAULT_PROFILE, **stored}


def save_profile(update: dict) -> dict:
    profile = {**get_profile(), **update}
    set_setting("profile", profile)
    return profile


def log_job(job: str, message: str) -> None:
    execute("INSERT INTO job_log(at, job, message) VALUES(?,?,?)", (now_iso(), job, message))
