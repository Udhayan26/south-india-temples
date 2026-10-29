"""OpenStreetMap (Overpass API) source — free, open data (ODbL).

One job = one district. Each job writes data/raw/osm/<STATE>/<district_rel_id>.csv
"""
from __future__ import annotations

import csv
import os
import re
from datetime import date

from .common import RAW, STATES, blank_row, finish_row, request_with_retry, session, write_rows
from .inscriptions import INS_RX, INSCRIPTION_WORDS, blank_ins, ins_type, write_ins

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
        out.append({"rel_id": el["id"], "name": name.replace(" District", "").strip(),
                    "wikidata": t.get("wikidata", "")})
    return sorted(out, key=lambda d: d["name"])


def _address(t: dict) -> str:
    if t.get("addr:full"):
        return t["addr:full"]
    parts = [t.get(k) for k in ("addr:housenumber", "addr:street", "addr:place",
                                 "addr:suburb", "addr:village", "addr:city", "addr:district")]
    return ", ".join(p for p in parts if p)


# names that mark a temple when OSM has no religion tag
TEMPLE_WORDS = ("temple|kovil|koil|gudi|devasthan|devalay|kshetr|mandir|amman|perumal|eswarar|easwarar|"
                "iswarar|alayam|basadi|kavu|ambalam|thirukoil|swamy temple|matha temple")
NOT_TEMPLE = re.compile(r"church|cathedral|chapel|mosque|masjid|dargah|juma|gurudwara|gurdwara|synagogue|"
                        r"school|college|hospital|hotel|lodge|bus stop|road|street|nagar\b|layout|colony", re.I)


def _is_inscription(t: dict) -> bool:
    h, m = t.get("historic", ""), t.get("memorial", "")
    if t.get("amenity") == "place_of_worship":
        return False
    return (h in ("epigraph", "inscription", "hero_stone") or m in ("stele", "hero_stone")
            or bool(INS_RX.search(t.get("name", "") + " " + t.get("name:en", ""))))


def _inscription_row(el, t, lat, lon, state_code, district, today) -> dict:
    name = t.get("name:en") or t.get("name") or ""
    kind_text = " ".join([name, t.get("historic", ""), t.get("memorial", ""), t.get("description", "")])
    return blank_ins(
        title=name or ("Inscription" if "inscription" in t else "Hero stone / inscription"),
        type=ins_type(kind_text),
        location_detail=t.get("description", ""),
        village=t.get("addr:village") or t.get("addr:city") or "",
        district=district["name"], state=STATES[state_code],
        latitude=round(lat, 6), longitude=round(lon, 6), location_basis="Exact (mapped)",
        date_text=t.get("start_date", ""), language=t.get("inscription:language") or t.get("language", ""),
        script=t.get("script", ""), dynasty=t.get("historic:civilization") or t.get("historic:period", ""),
        text=(t.get("inscription") or "")[:500], image=t.get("image") or t.get("wikimedia_commons") or "",
        source="OpenStreetMap", source_url=f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
        osm_id=f"{el['type']}/{el['id']}", wikidata_id=t.get("wikidata", ""), last_updated=today,
    )


def fetch_district(state_code: str, district: dict) -> int:
    area_id = 3600000000 + int(district["rel_id"])
    q = f"""
[out:json][timeout:900];
area(id:{area_id})->.d;
(
  nwr["amenity"="place_of_worship"]["religion"~"^({RELIGIONS})$"](area.d);
  nwr["amenity"="place_of_worship"][!"religion"]["name"~"{TEMPLE_WORDS}",i](area.d);
  nwr["building"="temple"][!"amenity"](area.d);
  nwr["historic"~"^(temple|archaeological_site|ruins|monument)$"]["name"~"{TEMPLE_WORDS}",i](area.d);
  nwr["historic"~"^(epigraph|inscription|hero_stone)$"](area.d);
  nwr["memorial"~"^(stele|hero_stone)$"](area.d);
  nwr["name"~"{INSCRIPTION_WORDS}",i](area.d);
);
out center tags;
"""
    data = _overpass(q)
    today = date.today().isoformat()
    rows, ins_rows = [], []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None:
            continue
        if _is_inscription(t):
            ins_rows.append(_inscription_row(el, t, lat, lon, state_code, district, today))
            continue
        if t.get("religion") and t["religion"] not in RELIGIONS.split("|"):
            continue
        if not t.get("religion") and NOT_TEMPLE.search(t.get("name", "") + " " + t.get("name:en", "")):
            continue
        if "inscription" in t or t.get("historic") in ("epigraph",):
            ins_rows.append(_inscription_row(el, t, lat, lon, state_code, district, today))
        local = next((t[k] for k in LOCAL_NAME_KEYS if t.get(k)), "")
        name = t.get("name:en") or t.get("name") or local or ""
        if local == name:
            local = ""
        row = blank_row(
            name=name,
            name_local=local,
            deity=t.get("deity") or t.get("subject") or "",
            religion=t.get("religion") or ("jain" if re.search(r"basadi|jain", t.get("name", ""), re.I) else "hindu"),
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
    write_ins(RAW / "ins_osm" / state_code / f"{district['rel_id']}.csv", ins_rows)
    return len(rows)


PLACE_TYPES = "city|town|village|hamlet|suburb|neighbourhood|locality"


def fetch_places(state_code: str, district: dict) -> int:
    """Villages / towns of the district, used to give every temple a City / Village."""
    area_id = 3600000000 + int(district["rel_id"])
    q = f"""
[out:json][timeout:600];
area(id:{area_id})->.d;
node["place"~"^({PLACE_TYPES})$"]["name"](area.d);
out body;
"""
    rows = []
    for el in _overpass(q).get("elements", []):
        t = el.get("tags", {})
        rows.append({"name": t.get("name:en") or t["name"], "place": t["place"],
                     "lat": round(el["lat"], 5), "lon": round(el["lon"], 5)})
    path = RAW / "places" / state_code / f"{district['rel_id']}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "place", "lat", "lon"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)
