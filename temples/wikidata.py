"""Wikidata (SPARQL) source — free, CC0. Adds deity, heritage status,
inception, images and famous groupings (Divya Desam, Paadal Petra Sthalam...).

One job = one district. Searches the district's bounding box (taken from the OSM
temples) with Wikidata's fast geo index; a box that times out is split in 4.
District/state are refined from nearby OSM temples during merge.
Writes data/raw/wikidata/<STATE>/<district_rel_id>.csv
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import date

from .common import RAW, STATES, blank_row, finish_row, read_rows, request_with_retry, session, write_rows

ENDPOINT = "https://query.wikidata.org/sparql"
LOCAL_LANG = {"IN-TN": "ta", "IN-PY": "ta", "IN-KL": "ml", "IN-KA": "kn", "IN-AP": "te", "IN-TG": "te"}

SPECIAL_GROUPS = [
    (r"divya desam", "Divya Desam"),
    (r"paadal petra|padal petra|thevaram|tevaram", "Paadal Petra Sthalam"),
    (r"abhimana|abhimana sthalam", "Abhimana Sthalam"),
    (r"vaippu", "Vaippu Sthalam"),
    (r"jyotirlinga", "Jyotirlinga"),
    (r"shakti peeth|sakthi peeth", "Shakti Peetha"),
    (r"arupadai|six abodes of murugan", "Arupadai Veedu"),
    (r"pancha bhoota|pancha bhuta", "Pancha Bhoota Sthalam"),
    (r"navagraha", "Navagraha Sthalam"),
    (r"great living chola", "UNESCO – Great Living Chola Temples"),
    (r"world heritage", "UNESCO World Heritage"),
    (r"monument of national importance", "ASI – Monument of National Importance"),
]

NOT_TEMPLE = re.compile(r"church|cathedral|chapel|basilica|mosque|masjid|dargah|gurudwara|synagogue", re.I)

BOX_QUERY = """
SELECT DISTINCT ?item WHERE {{
  hint:Query hint:optimizer "None" .
  SERVICE wikibase:box {{
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerSouthWest "Point({w} {s})"^^geo:wktLiteral ;
                    wikibase:cornerNorthEast "Point({e} {n})"^^geo:wktLiteral .
  }}
  ?item wdt:P31/wdt:P279* wd:Q44539 .
}}
"""

DETAIL_QUERY = """
SELECT ?item ?itemLabel ?local ?coord ?deityLabel ?adminLabel ?inception ?image
       ?heritageLabel ?groupLabel ?typeLabel ?website ?operatorLabel ?itemDescription WHERE {{
  VALUES ?item {{ {items} }}
  ?item wdt:P625 ?coord .
  OPTIONAL {{ ?item wdt:P31 ?type }}
  OPTIONAL {{ ?item wdt:P825 ?deity }}
  OPTIONAL {{ ?item wdt:P131 ?admin }}
  OPTIONAL {{ ?item wdt:P571 ?inception }}
  OPTIONAL {{ ?item wdt:P18 ?image }}
  OPTIONAL {{ ?item wdt:P1435 ?heritage }}
  OPTIONAL {{ ?item wdt:P361|wdt:P5008 ?group }}
  OPTIONAL {{ ?item wdt:P856 ?website }}
  OPTIONAL {{ ?item wdt:P137 ?operator }}
  OPTIONAL {{ ?item rdfs:label ?local FILTER(lang(?local) = "{lang}") }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
"""


def _sparql(query: str) -> list[dict]:
    r = request_with_retry(session(), "POST", ENDPOINT, data={"query": query, "format": "json"},
                           headers={"Accept": "application/sparql-results+json"}, timeout=90, tries=2)
    return [{k: x["value"] for k, x in b.items()} for b in r.json()["results"]["bindings"]]


def district_boxes(state_code: str, district: dict, pad: float = 0.02) -> list[tuple]:
    """Bounding boxes around the OSM temples already collected in the district.
    Districts in separate pieces (Puducherry = Puducherry + Karaikal + Mahe + Yanam)
    get one box per piece, so the search never spans other states."""
    p = RAW / "osm" / state_code / f"{district['rel_id']}.csv"
    if not p.exists():
        return []
    cells: dict[tuple, list] = defaultdict(list)
    for r in read_rows(p):
        if r.get("latitude"):
            lat, lon = float(r["latitude"]), float(r["longitude"])
            cells[(int(lat / 0.25), int(lon / 0.25))].append((lat, lon))
    boxes, seen = [], set()
    for start in cells:                       # connected groups of occupied 0.25° cells
        if start in seen:
            continue
        stack, pts = [start], []
        seen.add(start)
        while stack:
            ci, cj = stack.pop()
            pts += cells[(ci, cj)]
            for nb in ((ci + a, cj + b) for a in (-1, 0, 1) for b in (-1, 0, 1)):
                if nb in cells and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        lats, lons = [a for a, _ in pts], [b for _, b in pts]
        boxes.append((min(lats) - pad, min(lons) - pad, max(lats) + pad, max(lons) + pad))
    return boxes


def _items_in_box(box, depth: int = 0) -> set[str]:
    s, w, n, e = box
    try:
        rows = _sparql(BOX_QUERY.format(s=s, w=w, n=n, e=e))
        return {r["item"].rsplit("/", 1)[-1] for r in rows}
    except Exception as ex:
        if depth >= 2:
            raise
        print(f"   box too heavy ({ex}); splitting into 4")
        mlat, mlon = (s + n) / 2, (w + e) / 2
        out: set[str] = set()
        for q in ((s, w, mlat, mlon), (s, mlon, mlat, e), (mlat, w, n, mlon), (mlat, mlon, n, e)):
            out |= _items_in_box(q, depth + 1)
            time.sleep(2)
        return out


def fetch_district(state_code: str, district: dict) -> int:
    found: set[str] = set()
    for box in district_boxes(state_code, district):
        found |= _items_in_box(box)
    qids = sorted(found)
    bindings = []
    for i in range(0, len(qids), 150):
        chunk = " ".join(f"wd:{q}" for q in qids[i:i + 150])
        bindings += _sparql(DETAIL_QUERY.format(items=chunk, lang=LOCAL_LANG[state_code]))
        time.sleep(2)
    items: dict[str, dict] = {}
    multi: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for v in bindings:
        qid = v["item"].rsplit("/", 1)[-1]
        m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", v.get("coord", ""))
        if not m:
            continue
        items.setdefault(qid, {
            "name": v.get("itemLabel", ""), "local": v.get("local", ""),
            "lon": float(m.group(1)), "lat": float(m.group(2)),
            "admin": v.get("adminLabel", ""), "inception": v.get("inception", "")[:10].lstrip("+"),
            "image": v.get("image", ""), "website": v.get("website", ""),
            "operator": v.get("operatorLabel", ""), "desc": v.get("itemDescription", ""),
        })
        for key in ("deityLabel", "heritageLabel", "groupLabel", "typeLabel"):
            if v.get(key) and not re.fullmatch(r"Q\d+", v[key]):
                multi[qid][key].add(v[key])

    today = date.today().isoformat()
    rows = []
    for qid, it in items.items():
        groups_text = " ".join(multi[qid]["groupLabel"] | multi[qid]["heritageLabel"] | multi[qid]["typeLabel"]) + " " + it["desc"]
        special = sorted({label for rx, label in SPECIAL_GROUPS if re.search(rx, groups_text, re.I)})
        if NOT_TEMPLE.search(it["name"]):
            continue
        admin_is_district = "district" in it["admin"].lower()
        if it["admin"] in STATES.values() or it["admin"] in ("India", ""):
            admin_is_district = True          # admin is only the state -> no city
        row = blank_row(
            name=it["name"] if not re.fullmatch(r"Q\d+", it["name"]) else "",
            name_local=it["local"],
            deity="; ".join(sorted(multi[qid]["deityLabel"])),
            religion="jain" if "jain" in it["desc"].lower() else
                     "buddhist" if "buddhist" in it["desc"].lower() else "hindu",
            special_group="; ".join(special),
            state=STATES[state_code],
            district=district["name"],
            city="" if admin_is_district else it["admin"],
            latitude=it["lat"], longitude=it["lon"],
            wikidata_id=qid,
            operator=it["operator"], website=it["website"],
            heritage="; ".join(sorted(multi[qid]["heritageLabel"])),
            inception=it["inception"], image=it["image"], description=it["desc"],
            sources="Wikidata", last_updated=today,
        )
        if row["name"]:
            rows.append(finish_row(row))
    write_rows(RAW / "wikidata" / state_code / f"{district['rel_id']}.csv", rows)
    return len(rows)
