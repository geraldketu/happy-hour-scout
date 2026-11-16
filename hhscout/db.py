"""SQLite storage for discovered restaurants and their happy hours."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path("data/happyhours.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurants (
    place_id        TEXT PRIMARY KEY,          -- Google Places id
    name            TEXT NOT NULL,
    address         TEXT,
    city            TEXT,                      -- e.g. "Baltimore, MD"
    neighborhood    TEXT,                      -- the search area it was found in
    phone           TEXT,
    website         TEXT,
    lat             REAL,
    lng             REAL,
    rating          REAL,
    rating_count    INTEGER,
    types           TEXT,                      -- comma-separated Google place types
    business_status TEXT,
    -- workflow
    scrape_status   TEXT NOT NULL DEFAULT 'pending',  -- pending | found | not_found | no_website | error
    on_happyhopper  INTEGER,                   -- NULL = unchecked, 0 = missing, 1 = already listed
    submitted       INTEGER NOT NULL DEFAULT 0,-- 1 once you've added it on happyhopper.app
    notes           TEXT,
    discovered_at   TEXT NOT NULL,
    scraped_at      TEXT
);

CREATE TABLE IF NOT EXISTS happy_hours (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id    TEXT NOT NULL REFERENCES restaurants(place_id) ON DELETE CASCADE,
    days        TEXT,          -- e.g. "Mon-Fri"
    start_time  TEXT,          -- "16:00"
    end_time    TEXT,          -- "19:00" or "close"
    deals       TEXT,          -- newline-separated deal lines
    snippet     TEXT NOT NULL, -- raw text the info came from, for review
    source_url  TEXT NOT NULL,
    confidence  TEXT NOT NULL, -- high | medium | low
    extracted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hh_place ON happy_hours(place_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(restaurants)")}
    if "city" not in cols:  # databases created before multi-city support were Baltimore-only
        conn.execute("ALTER TABLE restaurants ADD COLUMN city TEXT")
        conn.execute("UPDATE restaurants SET city = 'Baltimore, MD'")
        conn.commit()
    return conn


def upsert_restaurant(conn: sqlite3.Connection, r: dict) -> bool:
    """Insert a restaurant; refresh its Google fields if it already exists. Returns True if new."""
    exists = conn.execute("SELECT 1 FROM restaurants WHERE place_id = ?", (r["place_id"],)).fetchone()
    fields = ("name", "address", "phone", "website", "lat", "lng", "rating", "rating_count", "types", "business_status")
    if exists:
        conn.execute(
            f"UPDATE restaurants SET {', '.join(f'{f} = ?' for f in fields)} WHERE place_id = ?",
            [r.get(f) for f in fields] + [r["place_id"]],
        )
        return False
    status = "pending" if r.get("website") else "no_website"
    conn.execute(
        f"INSERT INTO restaurants (place_id, city, neighborhood, scrape_status, discovered_at, {', '.join(fields)}) "
        f"VALUES (?, ?, ?, ?, ?, {', '.join('?' for _ in fields)})",
        [r["place_id"], r.get("city"), r.get("neighborhood"), status, now()] + [r.get(f) for f in fields],
    )
    return True


def save_happy_hours(conn: sqlite3.Connection, place_id: str, results: list[dict], status: str) -> None:
    conn.execute("DELETE FROM happy_hours WHERE place_id = ?", (place_id,))
    for h in results:
        conn.execute(
            "INSERT INTO happy_hours (place_id, days, start_time, end_time, deals, snippet, source_url, confidence, extracted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (place_id, h.get("days"), h.get("start_time"), h.get("end_time"), h.get("deals"),
             h["snippet"], h["source_url"], h["confidence"], now()),
        )
    conn.execute("UPDATE restaurants SET scrape_status = ?, scraped_at = ? WHERE place_id = ?", (status, now(), place_id))
