import pytest

from hhscout import db
from hhscout.extract import happy_hour_snippets, parse_days, parse_deals, parse_snippet, parse_times, SiteFetcher
from hhscout.places import parse_place


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Happy Hour 4-7pm", ("16:00", "19:00")),
        ("happy hour 3:30 pm - 6:30 pm", ("15:30", "18:30")),
        ("Happy hour from 4 to 7", ("16:00", "19:00")),
        ("Late night 10pm - close", ("22:00", "close")),
        ("11am-2pm lunch", ("11:00", "14:00")),
        ("4PM–6PM", ("16:00", "18:00")),
        ("open 2024 - 2025", (None, None)),
        ("no times here", (None, None)),
    ],
)
def test_parse_times(text, expected):
    assert parse_times(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Monday - Friday 4-7", "Mon-Fri"),
        ("Mon thru Thurs", "Mon-Thu"),
        ("Tues-Sun", "Tue-Sun"),
        ("Happy hour daily", "Daily"),
        ("Weekdays only", "Mon-Fri"),
        ("Tuesday and Thursday", "Tue, Thu"),
        ("all the time", None),
    ],
)
def test_parse_days(text, expected):
    assert parse_days(text) == expected


def test_parse_deals():
    text = "Happy Hour\n$5 house wines\n$4 drafts • half price wings\nGreat vibes"
    assert parse_deals(text) == "$5 house wines\n$4 drafts\nhalf price wings"


def test_parse_snippet_confidence():
    r = parse_snippet("Happy Hour Mon-Fri 4-7pm\n$5 rail drinks", "https://x.com/specials")
    assert (r["days"], r["start_time"], r["end_time"], r["confidence"]) == ("Mon-Fri", "16:00", "19:00", "high")
    assert parse_snippet("Join us for happy hour!", "u")["confidence"] == "low"


HTML = """
<html><body>
<nav><a href="/menu">Menu</a> <a href="/specials">Happy Hour</a> <a href="https://other.com/x">x</a>
     <a href="/about">About</a> <a href="/menu.pdf">PDF</a></nav>
<script>var happyHour = 1;</script>
<section><h2>Happy Hour</h2><p>Monday through Friday, 4pm - 7pm at the bar.</p>
<ul><li>$5 house wine</li><li>$3 Natty Boh</li><li>Half off oysters</li></ul></section>
<footer>Happy hour excludes holidays</footer>
</body></html>
"""


def test_happy_hour_snippets():
    snippets = happy_hour_snippets(HTML)
    assert not any("happyHour" in s for s in snippets)  # script ignored
    assert any("excludes holidays" in s for s in snippets)  # footer kept
    r = parse_snippet(snippets[0], "u")
    assert r["days"] == "Mon-Fri" and r["start_time"] == "16:00" and r["end_time"] == "19:00"
    assert "$3 Natty Boh" in r["deals"] and "Half off oysters" in r["deals"]


def test_candidate_pages():
    pages = SiteFetcher().candidate_pages("https://www.bar.com/", HTML)
    assert pages[0] == "https://www.bar.com/specials"  # happy-hour link ranked first
    assert "https://www.bar.com/menu" in pages
    assert all("other.com" not in p and not p.endswith(".pdf") and "about" not in p for p in pages)


def test_db_roundtrip(tmp_path):
    raw = {
        "id": "abc",
        "displayName": {"text": "The Bar"},
        "formattedAddress": "1 Main St, Baltimore, MD",
        "websiteUri": "https://bar.com",
        "location": {"latitude": 39.28, "longitude": -76.6},
        "types": ["bar", "restaurant"],
    }
    conn = db.connect(tmp_path / "t.db")
    assert db.upsert_restaurant(conn, parse_place(raw, "Canton", "Baltimore, MD")) is True
    assert db.upsert_restaurant(conn, parse_place(raw, "Fells Point")) is False  # no duplicate
    db.save_happy_hours(conn, "abc", [parse_snippet("Happy hour daily 4-6pm $5 wine", "https://bar.com")], "found")
    row = conn.execute("SELECT * FROM restaurants").fetchone()
    assert (row["name"], row["neighborhood"], row["scrape_status"], row["types"]) == ("The Bar", "Canton", "found", "bar,restaurant")
    assert conn.execute("SELECT COUNT(*) FROM happy_hours").fetchone()[0] == 1

    no_site = parse_place({"id": "x", "displayName": {"text": "No Site"}})
    db.upsert_restaurant(conn, no_site)
    assert conn.execute("SELECT scrape_status FROM restaurants WHERE place_id='x'").fetchone()[0] == "no_website"


def test_lunch_times_not_borrowed():
    r = parse_snippet("LUNCH SPECIAL & HAPPY HOUR\nLUNCH SPECIAL\n(MON - FRI (11 AM - 3 PM)\n$12 bowl", "u")
    assert r["start_time"] is None
    r = parse_snippet("HAPPY HOUR SPECIAL MON - FRI (3 PM - 6 PM)\n$8 board", "u")
    assert (r["days"], r["start_time"], r["end_time"]) == ("Mon-Fri", "15:00", "18:00")


def test_footer_and_sibling_blocks():
    html = """<body><div><h4>HAPPY HOUR!</h4></div><div><p>Tues-Fri 4-6pm</p><p>$5 margaritas</p></div>
              <footer><p>Hours: Sun 1pm-2am</p><p><strong>Happy Hour: Mon-Fri 5-7pm</strong></p></footer></body>"""
    found = {(r["days"], r["start_time"], r["end_time"]) for r in (parse_snippet(s, "u") for s in happy_hour_snippets(html))}
    assert ("Tue-Fri", "16:00", "18:00") in found
    assert ("Mon-Fri", "17:00", "19:00") in found


def test_split_price_lines():
    assert parse_deals("HAPPY HOUR:\n$3\nDomestic Bottles\n$4 Seltzers") == "$3 Domestic Bottles\n$4 Seltzers"
    assert parse_deals("FOOD\nPAPADUM BOARD\n$8\nVEG BITE CLUB\n$12") == "PAPADUM BOARD $8\nVEG BITE CLUB $12"


def test_tiles_cover_box():
    from hhscout.places import tiles
    box = {"low": {"latitude": 0.0, "longitude": 0.0}, "high": {"latitude": 3.0, "longitude": 6.0}}
    t = tiles(box, 3)
    assert len(t) == 9
    assert t[0]["low"] == {"latitude": 0.0, "longitude": 0.0}
    assert t[-1]["high"] == {"latitude": 3.0, "longitude": 6.0}
    assert t[4]["low"] == {"latitude": 1.0, "longitude": 2.0}


def test_city_column_migration(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE restaurants (place_id TEXT PRIMARY KEY, name TEXT NOT NULL, scrape_status TEXT, discovered_at TEXT)")
    old.execute("INSERT INTO restaurants VALUES ('a', 'Old Bar', 'found', 'x')")
    old.commit()
    old.close()
    assert db.connect(path).execute("SELECT city FROM restaurants").fetchone()[0] == "Baltimore, MD"
