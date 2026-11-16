"""Build the curated, shareable dataset in data/verified/ from the hhscout database.

Schedules and deals below were checked by hand against each venue's own website;
addresses, phones and source links come from the database. Only fields that are
safe to redistribute are exported (no Google ratings, coordinates or place types).

    python tools/build_verified.py

Outputs: data/verified/baltimore.csv, baltimore.json, baltimore.db
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "happyhours.db"
OUT_DIR = ROOT / "data" / "verified"
CHECKED = "Nov 15, 2026"

MON, TUE, WED, THU, FRI, SAT, SUN = 1, 2, 3, 4, 5, 6, 0
WEEKDAYS = [MON, TUE, WED, THU, FRI]

# name in database -> verified details
VERIFIED = {
    "The Horse You Came In On Saloon": {
        "segments": [{"days": [MON, TUE, WED, THU], "start": "16:00", "end": "20:00"}],
        "deals": ["$3 domestic bottles", "$4 seltzers", "$5 Green Tea | Lemon Drops", "$6 well cocktails"],
    },
    "Harbor Tandoor": {
        "segments": [{"days": WEEKDAYS, "start": "15:00", "end": "18:00"}],
        "deals": ["$4 local draft beer", "$4 house wine", "$8 papadum board", "$10 Baltimore crab naan",
                  "$12 veg or non-veg bite club"],
    },
    "Wayward Bar & Kitchen": {
        "segments": [{"days": [TUE, WED, THU], "start": "16:00", "end": "19:00"},
                     {"days": [FRI], "allDay": True, "end": "22:00"}],
        "deals": ["$4 drafts", "$5 single rail mixed drinks", "$6.50 Surfside cans", "Full-size happy hour food"],
        "note": "Closed Mondays.",
    },
    "Liv’s Tavern": {
        "display": "Liv's Tavern",
        "segments": [{"days": [MON], "allDay": True},
                     {"days": [TUE, WED, THU], "start": "16:00", "end": "19:00"},
                     {"days": [FRI], "start": "11:00", "end": "19:00"}],
        "deals": ["$5 drafts, Rumple & green teas", "$6.50 crushes & transfusions", "$7 munchies",
                  "$8 Flambongs", "$10 cocktails"],
        "note": "Excludes game days.",
    },
    "Nobles Bar And Grill": {
        "display": "Nobles Bar and Grill",
        "segments": [{"days": [TUE, WED, THU, FRI], "start": "16:00", "end": "19:00"}],
        "deals": ["$5 craft beer cans (all day)", "$3 Natty Boh 16 oz cans (all day)"],
    },
    "The HappyJack Tavern": {
        "segments": [{"days": WEEKDAYS, "end": "18:00", "label": "Drinks"},
                     {"days": [MON, TUE, WED, THU], "start": "16:00", "end": "18:00", "label": "Food"}],
        "deals": ["$1 off select beer, wine & cocktails", "$5 rail liquor",
                  "$8 smash burger, veggie burger, grilled chicken or grilled cheese with a side"],
        "note": "Food is dine-in only.",
    },
    "Smaltimore": {
        "segments": [{"days": WEEKDAYS, "end": "19:00"}],
        "deals": ["$6 house wine", "$6 select drafts", "$6 Four Walls", "$6 Jameson"],
        "note": "Start time isn't listed on their site.",
    },
    "Mama's on the Half Shell": {
        "segments": [{"days": WEEKDAYS, "start": "15:00", "end": "18:00"}],
        "deals": [],
        "note": "First floor, indoors only.",
    },
    "of Love & Regret": {
        "segments": [{"days": [TUE, WED, THU, FRI], "start": "16:00", "end": "18:30"}],
        "deals": [],
    },
    "Chilango's": {
        "segments": [{"days": WEEKDAYS, "start": "15:00", "end": "19:00"}],
        "deals": [],
        "note": "Their website gives the address as 915 S Wolfe St.",
    },
    "RYE of BALTIMORE": {
        "display": "Rye",
        "segments": [{"days": WEEKDAYS, "start": "17:00", "end": "19:00"}],
        "deals": [],
    },
    "The Point In Fells": {
        "display": "The Point in Fells",
        "segments": [{"days": [MON, WED, THU], "start": "16:00", "end": "19:00"},
                     {"days": [FRI], "allDay": True}],
        "deals": [],
    },
    "The Outpost American Tavern": {
        "segments": [{"days": WEEKDAYS, "start": "16:00", "end": "18:00"}],
        "deals": [],
    },
    "Bayside Cantina": {
        "segments": [{"days": WEEKDAYS, "start": "15:00", "end": "19:00"}],
        "deals": [],
    },
}

# found "happy hour" on their site but no clear schedule, or the text wasn't theirs
LEAD_EXCLUDE = {"Blarney Stone Pub"}  # text came from a third-party review site


def short_address(addr: str) -> str:
    return addr.replace(", USA", "").replace(", Baltimore, MD", ", Baltimore")


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    def best_source(place_id: str) -> str:
        row = conn.execute(
            "SELECT source_url FROM happy_hours WHERE place_id = ? "
            "ORDER BY CASE confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, start_time IS NULL LIMIT 1",
            (place_id,)).fetchone()
        return (row["source_url"] if row else "").replace("?utm_source=google", "")

    venues = []
    for name, info in VERIFIED.items():
        r = conn.execute("SELECT * FROM restaurants WHERE name = ? AND city = 'Baltimore, MD'", (name,)).fetchone()
        if r is None:
            raise SystemExit(f"{name!r} is not in the database")
        venues.append({
            "name": info.get("display", name),
            "hood": r["neighborhood"],
            "address": short_address(r["address"]),
            "phone": r["phone"],
            "source": best_source(r["place_id"]),
            "segments": info["segments"],
            "deals": info["deals"],
            "note": info.get("note"),
        })

    leads = []
    for r in conn.execute(
        "SELECT * FROM restaurants WHERE city = 'Baltimore, MD' AND scrape_status = 'found' "
        "AND (notes IS NULL OR notes NOT LIKE 'REJECTED%') ORDER BY neighborhood, name"
    ):
        if r["name"] in VERIFIED or r["name"] in LEAD_EXCLUDE:
            continue
        leads.append({"name": r["name"], "hood": r["neighborhood"], "address": short_address(r["address"]),
                      "source": best_source(r["place_id"])})

    write_outputs(venues, leads)


DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0]


def fmt_time(t: str) -> str:
    h, m = map(int, t.split(":"))
    ap = "PM" if 12 <= h < 24 else "AM"
    h = h % 12 or 12
    return f"{h}:{m:02d} {ap}" if m else f"{h} {ap}"


def fmt_days(days: list[int]) -> str:
    order = [d for d in WEEK_ORDER if d in days]
    out, i = [], 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and WEEK_ORDER.index(order[j + 1]) == WEEK_ORDER.index(order[j]) + 1:
            j += 1
        out.append(f"{DAY_NAMES[order[i]]}-{DAY_NAMES[order[j]]}" if j - i >= 2 else ", ".join(DAY_NAMES[d] for d in order[i:j + 1]))
        i = j + 1
    return ", ".join(out)


def fmt_segment(s: dict) -> str:
    if s.get("allDay"):
        when = f"all day until {fmt_time(s['end'])}" if s.get("end") else "all day"
    elif not s.get("start"):
        when = f"until {fmt_time(s['end'])}"
    else:
        when = f"{fmt_time(s['start'])}-{fmt_time(s['end'])}"
    return (f"{s['label']}: " if s.get("label") else "") + f"{fmt_days(s['days'])} {when}"


def write_outputs(venues: list[dict], leads: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"city": "Baltimore, MD", "checked": CHECKED,
            "note": "Verified against each venue's own website on the date above. Times and prices change."}
    (OUT_DIR / "baltimore.json").write_text(
        json.dumps({**meta, "verified": venues, "leads": leads}, indent=2, ensure_ascii=False), encoding="utf-8")

    with (OUT_DIR / "baltimore.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["status", "name", "neighborhood", "address", "phone", "happy_hour", "deals", "notes", "source"])
        for v in venues:
            w.writerow(["verified", v["name"], v["hood"], v["address"], v["phone"],
                        "; ".join(fmt_segment(s) for s in v["segments"]), "; ".join(v["deals"]), v["note"] or "", v["source"]])
        for l in leads:
            w.writerow(["lead", l["name"], l["hood"], l["address"], "", "", "",
                        "Mentions happy hour on its site; times not stated clearly", l["source"]])

    db_path = OUT_DIR / "baltimore.db"
    db_path.unlink(missing_ok=True)
    out = sqlite3.connect(db_path)
    out.executescript("""
        CREATE TABLE venues (
            id INTEGER PRIMARY KEY, status TEXT NOT NULL CHECK (status IN ('verified', 'lead')),
            name TEXT NOT NULL, neighborhood TEXT, address TEXT, phone TEXT, notes TEXT, source TEXT,
            checked TEXT NOT NULL);
        CREATE TABLE happy_hours (
            id INTEGER PRIMARY KEY, venue_id INTEGER NOT NULL REFERENCES venues(id),
            label TEXT,              -- e.g. 'Drinks' / 'Food' when they differ
            days TEXT NOT NULL,      -- comma-separated: Mon,Tue,...
            start_time TEXT,         -- HH:MM, NULL if not published
            end_time TEXT,           -- HH:MM, NULL for open-ended all-day
            all_day INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE deals (id INTEGER PRIMARY KEY, venue_id INTEGER NOT NULL REFERENCES venues(id), description TEXT NOT NULL);
    """)
    for v in venues:
        cur = out.execute("INSERT INTO venues (status, name, neighborhood, address, phone, notes, source, checked) "
                          "VALUES ('verified', ?, ?, ?, ?, ?, ?, ?)",
                          (v["name"], v["hood"], v["address"], v["phone"], v["note"], v["source"], CHECKED))
        for s in v["segments"]:
            out.execute("INSERT INTO happy_hours (venue_id, label, days, start_time, end_time, all_day) VALUES (?, ?, ?, ?, ?, ?)",
                        (cur.lastrowid, s.get("label"), ",".join(DAY_NAMES[d] for d in WEEK_ORDER if d in s["days"]),
                         s.get("start"), s.get("end"), int(bool(s.get("allDay")))))
        for d in v["deals"]:
            out.execute("INSERT INTO deals (venue_id, description) VALUES (?, ?)", (cur.lastrowid, d))
    for l in leads:
        out.execute("INSERT INTO venues (status, name, neighborhood, address, source, checked, notes) "
                    "VALUES ('lead', ?, ?, ?, ?, ?, 'Mentions happy hour on its site; times not stated clearly')",
                    (l["name"], l["hood"], l["address"], l["source"], CHECKED))
    out.commit()
    out.close()
    print(f"{len(venues)} verified, {len(leads)} leads -> {OUT_DIR}")

if __name__ == "__main__":
    main()
