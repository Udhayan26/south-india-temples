"""Wikidata (SPARQL) source — free, CC0. Adds deity, heritage status,
inception, images and famous groupings (Divya Desam, Paadal Petra Sthalam...).

One job = one district (small query, avoids Wikidata's 60-second limit).
Writes data/raw/wikidata/<STATE>/<district_rel_id>.csv
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from .common import RAW, STATES, blank_row, finish_row, request_with_retry, session, write_rows

ENDPOINT = "https://query.wikidata.org/sparql"
LOCAL_LANG = {"IN-TN": "ta", "IN-PY": "ta", "IN-KL": "ml", "IN-KA": "kn", "IN-AP": "te", "IN-TG": "te"}

SPECIAL_GROUPS = [
    (r"divya desam", "Divya Desam"),
    (r"paadal petra|padal petra|thevaram", "Paadal Petra Sthalam"),
    (r"jyotirlinga", "Jyotirlinga"),
    (r"shakti peeth|sakthi peeth", "Shakti Peetha"),
    (r"arupadai|six abodes of murugan", "Arupadai Veedu"),
    (r"pancha bhoota|pancha bhuta", "Pancha Bhoota Sthalam"),
    (r"navagraha", "Navagraha Sthalam"),
    (r"great living chola", "UNESCO – Great Living Chola Temples"),
    (r"world heritage", "UNESCO World Heritage"),
    (r"monument of national importance", "ASI – Monument of National Importance"),
]

QUERY = """
SELECT ?item ?itemLabel ?local ?coord ?deityLabel ?adminLabel ?inception ?image
       ?heritageLabel ?groupLabel ?website ?operatorLabel ?itemDescription WHERE {{
  ?item wdt:P31/wdt:P279* wd:Q44539 ;
        wdt:P131* wd:{qid} ;
        wdt:P625 ?coord .
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


def fetch_district(state_code: str, district: dict) -> int:
    q = QUERY.format(qid=district["wikidata"], lang=LOCAL_LANG[state_code])
    r = request_with_retry(session(), "GET", ENDPOINT, params={"query": q, "format": "json"},
                           headers={"Accept": "application/sparql-results+json"}, timeout=120, tries=2)
    items: dict[str, dict] = {}
    multi: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for b in r.json()["results"]["bindings"]:
        v = {k: x["value"] for k, x in b.items()}
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
        for key in ("deityLabel", "heritageLabel", "groupLabel"):
            if v.get(key) and not re.fullmatch(r"Q\d+", v[key]):
                multi[qid][key].add(v[key])

    today = date.today().isoformat()
    rows = []
    for qid, it in items.items():
        groups_text = " ".join(multi[qid]["groupLabel"] | multi[qid]["heritageLabel"]) + " " + it["desc"]
        special = sorted({label for rx, label in SPECIAL_GROUPS if re.search(rx, groups_text, re.I)})
        admin_is_district = "district" in it["admin"].lower()
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
