"""hhscout - find happy hours in any city (built for adding missing spots to happyhopper.app).

Quick start
  python -m hhscout run --city "Austin, TX"

Commands
  run        discover + scrape + export for a city, in one go
  discover   search Google Places for a city's bars/restaurants -> database
  scrape     visit each restaurant's website and extract happy hour info
  list       show restaurants with happy hours that still need checking/adding
  mark       record whether a place is on happyhopper / that you submitted it
  export     write a CSV for review
  stats      counts by status
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from hhscout import db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def cmd_discover(args, conn):
    from hhscout import places

    key = places.api_key()
    city_name, box = places.city_box(args.city, key)
    print(f"City: {city_name}")
    hoods = args.neighborhoods if args.neighborhoods is not None else places.NEIGHBORHOODS.get(args.city, [])
    if args.grid or not hoods:
        # area label, search text, rectangle
        areas = [(f"grid {i + 1}", args.city, t) for i, t in enumerate(places.tiles(box, args.grid or 3))]
    else:
        areas = [(n, f"{n}, {args.city}", box) for n in hoods]
    total_new = 0
    for label, area, rect in areas:
        for tmpl in places.QUERY_TEMPLATES:
            query = tmpl.format(area=area)
            found = places.search(query, key, rect, max_pages=args.pages)
            new = sum(db.upsert_restaurant(conn, places.parse_place(p, label, args.city)) for p in found
                      if p.get("businessStatus", "OPERATIONAL") == "OPERATIONAL")
            conn.commit()
            total_new += new
            print(f"{label:16s} {query[:50]:50s} {len(found):3d} results, {new:3d} new")
    print(f"\n{total_new} new restaurants added for {args.city}.")


def cmd_scrape(args, conn):
    from hhscout.extract import SiteFetcher

    statuses = ("pending", "error") if args.retry_errors else ("pending",)
    city_sql, city_params = ("AND city = ?", (args.city,)) if args.city else ("", ())
    rows = conn.execute(
        f"SELECT place_id, name, website FROM restaurants WHERE scrape_status IN ({','.join('?' * len(statuses))}) "
        f"AND website IS NOT NULL {city_sql} ORDER BY rating_count DESC NULLS LAST LIMIT ?",
        (*statuses, *city_params, args.limit),
    ).fetchall()
    fetcher = SiteFetcher(delay=args.delay)
    for i, r in enumerate(rows, 1):
        status, results = fetcher.scrape(r["website"])
        db.save_happy_hours(conn, r["place_id"], results, status)
        if status == "error":
            conn.execute("UPDATE restaurants SET notes = ? WHERE place_id = ?", (fetcher.last_error, r["place_id"]))
        conn.commit()
        best = results[0] if results else None
        info = (f"{best['days'] or '?'} {best['start_time'] or '?'}-{best['end_time'] or '?'} ({best['confidence']})"
                if best else fetcher.last_error or "")
        print(f"[{i}/{len(rows)}] {status:9s} {r['name'][:40]:40s} {info}")


def _rows_with_hh(conn, where: str = "", params=()):
    return conn.execute(
        f"""SELECT r.*, h.days, h.start_time, h.end_time, h.deals, h.confidence, h.source_url, h.snippet
            FROM restaurants r JOIN happy_hours h ON h.id = (
                SELECT id FROM happy_hours WHERE place_id = r.place_id
                ORDER BY CASE confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END LIMIT 1)
            {where}
            ORDER BY CASE h.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, r.rating_count DESC""",
        params,
    ).fetchall()


def _city_filter(args, alias: str = "r") -> tuple[str, tuple]:
    return (f" AND {alias}.city = ?", (args.city,)) if getattr(args, "city", None) else ("", ())


def cmd_list(args, conn):
    where = ("WHERE r.submitted = 0 AND (r.on_happyhopper IS NULL OR r.on_happyhopper = 0)"
             " AND (r.notes IS NULL OR r.notes NOT LIKE 'REJECTED%')")
    params: tuple = ()
    if args.confidence:
        where += " AND h.confidence = ?"
        params += (args.confidence,)
    city_sql, city_params = _city_filter(args)
    rows = _rows_with_hh(conn, where + city_sql, params + city_params)[: args.limit]
    for r in rows:
        hh = "unchecked" if r["on_happyhopper"] is None else "NOT on happyhopper"
        print(f"\n{r['name']}  [{r['confidence']}] [{hh}]")
        print(f"  id: {r['place_id']}")
        print(f"  {r['address']}  |  {r['phone'] or ''}")
        print(f"  When: {r['days'] or '?'}  {r['start_time'] or '?'} - {r['end_time'] or '?'}")
        if r["deals"]:
            for d in r["deals"].splitlines()[:6]:
                print(f"    • {d}")
        print(f"  Source: {r['source_url']}")
        if args.snippets:
            print("  ---\n  " + r["snippet"].replace("\n", "\n  "))
    print(f"\n{len(rows)} shown.")


def cmd_mark(args, conn):
    sets, params = [], []
    if args.on_happyhopper is not None:
        sets.append("on_happyhopper = ?")
        params.append(1 if args.on_happyhopper == "yes" else 0)
    if args.submitted:
        sets += ["submitted = 1", "on_happyhopper = 1"]
    if args.note:
        sets.append("notes = ?")
        params.append(args.note)
    if not sets:
        raise SystemExit("Nothing to mark: pass --on-happyhopper yes/no, --submitted or --note")
    cur = conn.execute(f"UPDATE restaurants SET {', '.join(sets)} WHERE place_id = ?", (*params, args.place_id))
    conn.commit()
    print("updated" if cur.rowcount else f"no restaurant with id {args.place_id}")


def _slug(city: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in city.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def cmd_export(args, conn):
    city_sql, city_params = _city_filter(args)
    rows = _rows_with_hh(conn, "WHERE 1=1" + city_sql, city_params)
    out = args.out or Path("data") / (f"happyhours-{_slug(args.city)}.csv" if args.city else "happyhours.csv")
    cols = ["place_id", "name", "address", "city", "neighborhood", "phone", "website", "days", "start_time", "end_time",
            "deals", "confidence", "source_url", "on_happyhopper", "submitted", "rating", "rating_count", "notes"]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
    print(f"{len(rows)} rows -> {out}")


def cmd_run(args, conn):
    cmd_discover(args, conn)
    print()
    cmd_scrape(args, conn)
    print()
    args.out = None
    cmd_export(args, conn)
    args.confidence, args.snippets, args.limit = None, False, 10
    cmd_list(args, conn)


def cmd_stats(args, conn):
    city_sql, city_params = _city_filter(args, "restaurants")
    base = "FROM restaurants WHERE 1=1" + city_sql
    print("Scrape status:")
    for r in conn.execute(f"SELECT scrape_status, COUNT(*) n {base} GROUP BY 1 ORDER BY 2 DESC", city_params):
        print(f"  {r['scrape_status']:12s} {r['n']}")
    print("By confidence (restaurants with happy hours):")
    for r in conn.execute(f"SELECT confidence, COUNT(DISTINCT place_id) n FROM happy_hours "
                          f"WHERE place_id IN (SELECT place_id {base}) GROUP BY 1", city_params):
        print(f"  {r['confidence']:12s} {r['n']}")
    s = conn.execute(f"SELECT SUM(on_happyhopper = 1) listed, SUM(on_happyhopper = 0) missing, "
                     f"SUM(on_happyhopper IS NULL) unchecked, SUM(submitted) submitted {base}", city_params).fetchone()
    print(f"happyhopper: {s['listed'] or 0} already listed, {s['missing'] or 0} missing, "
          f"{s['unchecked'] or 0} unchecked, {s['submitted'] or 0} submitted by you")
    print("Cities:", ", ".join(f"{r[0]} ({r[1]})" for r in conn.execute(
        "SELECT city, COUNT(*) FROM restaurants GROUP BY 1 ORDER BY 2 DESC")))


def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser(prog="hhscout", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=db.DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def city_arg(p, required=False):
        p.add_argument("--city", required=required, help='e.g. "Austin, TX" or "Baltimore, MD"')

    def discover_args(p):
        city_arg(p, required=True)
        p.add_argument("--neighborhoods", nargs="*",
                       help="search these neighborhoods (built-in list for Baltimore, MD); otherwise a grid is used")
        p.add_argument("--grid", type=int, default=None,
                       help="search an N x N grid over the city (default 3 when there's no neighborhood list)")
        p.add_argument("--pages", type=int, default=2, help="result pages per query (20 places each, max 3)")

    def scrape_args(p):
        p.add_argument("--limit", type=int, default=50, help="max websites to visit")
        p.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
        p.add_argument("--retry-errors", action="store_true")

    p = sub.add_parser("run", help="discover + scrape + export for a city")
    discover_args(p)
    scrape_args(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("discover", help="find restaurants via Google Places")
    discover_args(p)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("scrape", help="extract happy hours from restaurant websites")
    city_arg(p)
    scrape_args(p)
    p.set_defaults(func=cmd_scrape)

    p = sub.add_parser("list", help="show candidates to add to happyhopper")
    city_arg(p)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--confidence", choices=["high", "medium", "low"])
    p.add_argument("--snippets", action="store_true", help="also print the raw website text")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("mark", help="update a restaurant's happyhopper status")
    p.add_argument("place_id")
    p.add_argument("--on-happyhopper", choices=["yes", "no"])
    p.add_argument("--submitted", action="store_true", help="you added it on happyhopper.app")
    p.add_argument("--note")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("export", help="CSV for review")
    city_arg(p)
    p.add_argument("--out", type=Path, default=None)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("stats")
    city_arg(p)
    p.set_defaults(func=cmd_stats)

    args = ap.parse_args(argv)
    with db.connect(args.db) as conn:
        args.func(args, conn)


if __name__ == "__main__":
    main()
