"""OpenStreetMap (Overpass API) source — free, open data (ODbL).

One job = one district. Each job writes data/raw/osm/<STATE>/<district_rel_id>.csv
"""
from __future__ import annotations

import os
from datetime import date

from .common import RAW, STATES, blank_row, finish_row, request_with_retry, session, write_rows

OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
RELIGIONS = os.environ.get("TEMPLE_RELIGIONS", "hindu|jain|buddhist")

# OSM may still carry the older ISO code for Telangana
ISO_ALIASES = {"IN-TG": "IN-(TG|TS)"}

DENOMINATION = {"shaivism": "Shaivam", "shaiva": "Shaivam", "vaishnavism": "Vaishnavam",
                "vaishnava": "Vaishnavam", "shaktism": "Shaktam", "shakta": "Shaktam",
                "smartism": "Smartam", "digambara": "Jain", "svetambara": "Jain",
                "theravada": "Buddhist", "mahayana": "Buddhist"}

LOCAL_NAME_KEYS = ["name:ta", "name:ml", "name:kn", "name:te", "name:hi", "name:sa"]


def _overpass(query: str) -> dict:
    s = session()
    r = request_with_retry(s, "POST", OVERPASS_URL, data={"data": query})
    return r.json()


def list_districts(state_code: str) -> list[dict]:
    iso = ISO_ALIASES.get(state_code, state_code)
    q = f"""
[out:json][timeout:180];
area["ISO3166-2"~"^{iso}$"]["admin_level"="4"]->.s;
rel(area.s)["boundary"="administrative"]["admin_level"="5"];
out tags;
"""
    data = _overpass(q)
    out = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        name = t.get("name:en") or t.get("name") or str(el["id"])
        out.append({"rel_id": el["id"], "name": name.replace(" District", "").strip()})
    return sorted(out, key=lambda d: d["name"])


def _address(t: dict) -> str:
    if t.get("addr:full"):
        return t["addr:full"]
    parts = [t.get(k) for k in ("addr:housenumber", "addr:street", "addr:place",
                                 "addr:suburb", "addr:village", "addr:city", "addr:district")]
    return ", ".join(p for p in parts if p)


def fetch_district(state_code: str, district: dict) -> int:
    area_id = 3600000000 + int(district["rel_id"])
    q = f"""
[out:json][timeout:900];
area(id:{area_id})->.d;
nwr["amenity"="place_of_worship"]["religion"~"^({RELIGIONS})$"](area.d);
out center tags;
"""
    data = _overpass(q)
    today = date.today().isoformat()
    rows = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None:
            continue
        local = next((t[k] for k in LOCAL_NAME_KEYS if t.get(k)), "")
        name = t.get("name:en") or t.get("name") or local or ""
        if local == name:
            local = ""
        row = blank_row(
            name=name,
            name_local=local,
            deity=t.get("deity") or t.get("subject") or "",
            religion=t.get("religion", ""),
            category=DENOMINATION.get(t.get("denomination", "").lower(), ""),
            state=STATES[state_code],
            district=district["name"],
            city=t.get("addr:city") or t.get("addr:village") or t.get("addr:town") or "",
            address=_address(t),
            pincode=t.get("addr:postcode", ""),
            latitude=lat,
            longitude=lon,
            osm_id=f"{el['type']}/{el['id']}",
            wikidata_id=t.get("wikidata", ""),
            operator=t.get("operator", ""),
            phone=t.get("phone") or t.get("contact:phone") or "",
            website=t.get("website") or t.get("contact:website") or "",
            opening_hours=t.get("opening_hours", ""),
            heritage=t.get("heritage:operator") or t.get("heritage") or "",
            inception=t.get("start_date", ""),
            image=t.get("image") or t.get("wikimedia_commons") or "",
            description=t.get("description", ""),
            sources="OpenStreetMap",
            last_updated=today,
        )
        rows.append(finish_row(row))
    write_rows(RAW / "osm" / state_code / f"{district['rel_id']}.csv", rows)
    return len(rows)
