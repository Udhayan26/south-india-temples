"""Google Maps source via the official Places API (New) Text Search.

OFF unless the GOOGLE_MAPS_API_KEY secret is set. Uses the paid Google Maps
Platform API (never scrapes google.com/maps, which breaks Google's terms).

Google's terms limit how long Places content may be stored (place IDs may be
kept; most other fields only cached temporarily). By default this source
therefore only *enriches* temples already found in open data with their
google_place_id + Google Maps link. Set GOOGLE_ADD_NEW=1 to also add temples
that only Google knows about — check the current terms before doing that.

One job = one district. Writes data/raw/google/<STATE>/<district_rel_id>.csv
"""
from __future__ import annotations

import os
import time
from datetime import date

from .common import RAW, STATES, blank_row, finish_row, request_with_retry, session, write_rows

URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ("places.id,places.displayName,places.formattedAddress,places.location,"
              "places.types,places.googleMapsUri,nextPageToken")
SEARCH_TERMS = ["Hindu temple", "kovil", "Amman temple", "Perumal temple",
                "Shiva temple", "Murugan temple", "Jain temple"]

_budget = {"left": int(os.environ.get("GOOGLE_MAX_REQUESTS", "150"))}


def enabled() -> bool:
    return bool(os.environ.get("GOOGLE_MAPS_API_KEY"))


def budget_left() -> int:
    return _budget["left"]


def fetch_district(state_code: str, district: dict) -> int:
    key = os.environ["GOOGLE_MAPS_API_KEY"]
    s = session()
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK}
    seen, rows, today = set(), [], date.today().isoformat()
    for term in SEARCH_TERMS:
        token = None
        for _page in range(3):  # API returns max 60 results per query
            if _budget["left"] <= 0:
                break
            body = {"textQuery": f"{term} in {district['name']} district, {STATES[state_code]}, India",
                    "pageSize": 20}
            if token:
                body["pageToken"] = token
            _budget["left"] -= 1
            data = request_with_retry(s, "POST", URL, json=body, headers=headers, timeout=60).json()
            for p in data.get("places", []):
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                loc = p.get("location", {})
                rows.append(finish_row(blank_row(
                    name=p.get("displayName", {}).get("text", ""),
                    address=p.get("formattedAddress", ""),
                    state=STATES[state_code], district=district["name"],
                    latitude=loc.get("latitude"), longitude=loc.get("longitude"),
                    google_place_id=p["id"], religion="hindu",
                    sources="Google Maps", last_updated=today,
                )))
            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(2)
    write_rows(RAW / "google" / state_code / f"{district['rel_id']}.csv", rows)
    return len(rows)
