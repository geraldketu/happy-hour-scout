"""Find bars and restaurants in any city with the Google Places API (New) Text Search.

Docs: https://developers.google.com/maps/documentation/places/web-service/text-search

Each query returns at most 60 places (3 pages of 20), so a city is covered by
looking up its map viewport, splitting it into a grid of tiles, and running the
search queries restricted to each tile.
"""

from __future__ import annotations

import os
import time

import requests

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Only request the fields we store (the field mask also determines billing tier).
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.types",
        "places.businessStatus",
        "nextPageToken",
    ]
)

QUERY_TEMPLATES = ["happy hour in {area}", "bars in {area}"]

# Optional hand-picked neighborhoods (better than a grid for cities you know).
NEIGHBORHOODS = {
    "Baltimore, MD": [
        "Fells Point", "Canton", "Federal Hill", "Inner Harbor", "Harbor East", "Mount Vernon",
        "Hampden", "Station North", "Locust Point", "Remington", "Charles Village", "Little Italy",
        "Highlandtown", "Brewers Hill", "Mount Washington", "Roland Park", "Riverside",
        "Downtown", "Pigtown", "Hamilton", "Lauraville", "Waverly", "Greektown", "Butchers Hill",
    ],
}


def api_key() -> str:
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        raise SystemExit("GOOGLE_PLACES_API_KEY is not set. Put it in a .env file (see .env.example).")
    return key


def _post(body: dict, key: str, field_mask: str, session: requests.Session) -> dict:
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": field_mask, "Content-Type": "application/json"}
    resp = session.post(SEARCH_URL, json=body, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Places API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def city_box(city: str, key: str, session: requests.Session | None = None) -> tuple[str, dict]:
    """Return (resolved city name, viewport rectangle) for a city like "Austin, TX"."""
    data = _post({"textQuery": city, "pageSize": 1}, key, "places.displayName,places.formattedAddress,places.viewport",
                 session or requests.Session())
    places = data.get("places") or []
    if not places or "viewport" not in places[0]:
        raise SystemExit(f"Couldn't find a city called {city!r}. Try adding the state or country, e.g. 'Austin, TX'.")
    p = places[0]
    return p.get("formattedAddress") or p["displayName"]["text"], p["viewport"]


def tiles(box: dict, n: int) -> list[dict]:
    """Split a viewport rectangle into an n x n grid of rectangles."""
    lo, hi = box["low"], box["high"]
    dlat = (hi["latitude"] - lo["latitude"]) / n
    dlng = (hi["longitude"] - lo["longitude"]) / n
    out = []
    for i in range(n):
        for j in range(n):
            out.append({
                "low": {"latitude": lo["latitude"] + i * dlat, "longitude": lo["longitude"] + j * dlng},
                "high": {"latitude": lo["latitude"] + (i + 1) * dlat, "longitude": lo["longitude"] + (j + 1) * dlng},
            })
    return out


def parse_place(p: dict, neighborhood: str | None = None, city: str | None = None) -> dict:
    loc = p.get("location", {})
    return {
        "place_id": p["id"],
        "name": p.get("displayName", {}).get("text", ""),
        "address": p.get("formattedAddress"),
        "phone": p.get("nationalPhoneNumber"),
        "website": p.get("websiteUri"),
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "rating": p.get("rating"),
        "rating_count": p.get("userRatingCount"),
        "types": ",".join(p.get("types", [])),
        "business_status": p.get("businessStatus"),
        "neighborhood": neighborhood,
        "city": city,
    }


def search(query: str, key: str, box: dict, max_pages: int = 3, session: requests.Session | None = None) -> list[dict]:
    session = session or requests.Session()
    body = {"textQuery": query, "pageSize": 20, "locationRestriction": {"rectangle": box}}
    places = []
    for _ in range(max_pages):
        data = _post(body, key, FIELD_MASK, session)
        places.extend(data.get("places", []))
        token = data.get("nextPageToken")
        if not token:
            break
        body["pageToken"] = token
        time.sleep(1)  # next-page tokens take a moment to become valid
    return places
