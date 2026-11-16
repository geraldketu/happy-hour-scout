# Happy Hour Scout

Type in a city, get its happy hours. `hhscout` finds bars and restaurants with the
**Google Places API**, reads each venue's **own website** for happy hour days, times
and deals, and stores everything in a local **SQLite** database with the source page
for every result, so a person can check each one before it's used.

Built to find happy hours missing from [happyhopper.app](https://happyhopper.app/).

## Baltimore dataset

[`data/verified/`](data/verified/) has the first results, checked by hand on Nov 15, 2026:

- **14 verified happy hours** in Fells Point, Canton and Federal Hill, each with days, times, deals and a link to the venue's page
- **14 leads**: venues whose sites mention a happy hour, but where the times are in an image or PDF or not stated clearly

The same data is in three formats: [`baltimore.csv`](data/verified/baltimore.csv) (opens in Excel),
[`baltimore.json`](data/verified/baltimore.json), and [`baltimore.db`](data/verified/baltimore.db) (SQLite with
`venues`, `happy_hours` and `deals` tables). It was built from 89 venues scanned across the three
neighborhoods. Five results that looked right turned out to be wrong when checked: opening hours, a one-off
event, another location's deals. That is why every row is reviewed before it's published.

To rebuild it after re-running the scraper: `python tools/build_verified.py`

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your key: GOOGLE_PLACES_API_KEY=...
```

## Any city, one command

```bash
python -m hhscout run --city "Austin, TX"
```

This looks up the city on Google, searches a 3×3 grid over it for bars and happy hours,
visits up to 50 restaurant websites, saves everything to `data/happyhours.db`, writes
`data/happyhours-austin-tx.csv`, and prints the 10 best finds. Useful options:

| Option | What it does |
|---|---|
| `--grid 4` | finer grid for big cities (16 areas) |
| `--limit 150` | visit more websites |
| `--pages 3` | up to 60 results per search instead of 40 |
| `--neighborhoods "Fells Point" Canton` | search named neighborhoods instead of a grid (Baltimore, MD has a built-in list) |

## Step by step

```bash
python -m hhscout discover --city "Baltimore, MD"                 # built-in neighborhood list
python -m hhscout discover --city "Baltimore, MD" --neighborhoods Canton   # cheaper test run
python -m hhscout scrape --city "Baltimore, MD" --limit 50        # visit websites, extract happy hours
python -m hhscout list --city "Baltimore, MD" --confidence high   # best candidates
python -m hhscout list --snippets                                 # include the raw website text
python -m hhscout export --city "Baltimore, MD"                   # CSV (opens in Excel)
python -m hhscout stats
```

`list`, `export`, `scrape` and `stats` work across all cities when `--city` is left out.

For each candidate, search for it on happyhopper.app, then record what you found:

```bash
python -m hhscout mark <place_id> --on-happyhopper yes   # already listed: skip it
python -m hhscout mark <place_id> --on-happyhopper no    # missing
python -m hhscout mark <place_id> --submitted            # you added it
```

`list` hides places already listed on happyhopper or already submitted by you.

## How extraction works

The scraper fetches the homepage plus up to 3 same-site pages whose links mention
happy hour, specials, drinks or menu. It respects each site's robots.txt and waits
1 second between requests. It finds every block of text that mentions "happy hour",
then parses:

- **days**: `Mon-Fri`, `Daily`, `Tue, Thu`, ...
- **times**: `16:00`-`19:00`, or `22:00`-`close`
- **deals**: lines with prices, "half off", "% off", BOGO

Each result is rated **high**, **medium** or **low** confidence by how many of
those three it found. The raw snippet and source URL are always saved, so check
the snippet before you submit. Happy hours that only appear in images, PDFs or
Instagram are not found (`not_found`). Those are worth a manual look for
popular places.

## Database

`data/happyhours.db` contains two tables:

- `restaurants`: Google data plus workflow columns `scrape_status`, `on_happyhopper`, `submitted` and `notes`
- `happy_hours`: up to 3 extracted results per place, with snippet, source URL and confidence

## Tests

```bash
pytest -q
```
