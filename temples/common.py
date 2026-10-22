"""Shared schema, HTTP session and deity/category inference."""
from __future__ import annotations

import csv
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
OUTPUT = ROOT / "output"

USER_AGENT = os.environ.get(
    "TEMPLE_UA",
    "south-india-temples/1.0 (https://github.com/Udhayan26/south-india-temples)",
)

# South Indian states / UTs (ISO 3166-2 code -> name)
STATES = {
    "IN-TN": "Tamil Nadu",
    "IN-KL": "Kerala",
    "IN-KA": "Karnataka",
    "IN-AP": "Andhra Pradesh",
    "IN-TG": "Telangana",
    "IN-PY": "Puducherry",
}

# Wikidata item IDs of each state (for SPARQL)
STATE_QIDS = {
    "IN-TN": "Q1445",
    "IN-KL": "Q1186",
    "IN-KA": "Q1185",
    "IN-AP": "Q1159",
    "IN-TG": "Q677037",
    "IN-PY": "Q66743",
}

# Every source writes rows with exactly these columns.
FIELDS = [
    "temple_id", "name", "name_local", "deity", "category", "religion",
    "special_group", "state", "district", "city", "address", "pincode",
    "latitude", "longitude", "google_maps_url",
    "osm_id", "wikidata_id", "google_place_id", "govt_id",
    "operator", "phone", "website", "opening_hours", "heritage",
    "inception", "image", "description",
    "sources", "source_count", "last_updated",
]

# (regex on name/deity text, deity, category)
DEITY_RULES = [
    (r"murug|subrama|subbara|karthike|kartike|skanda|kumaraswam|velava|dandayudha|shanmuk|arumuga", "Murugan (Kartikeya)", "Kaumaram"),
    (r"vinay|ganapat|ganesh|pillaiyar|pillayar|vigneshwar|vighnesh", "Ganesha", "Ganapatyam"),
    (r"ayyapp|sastha|sasta|dharma ?sastha", "Ayyappan (Sastha)", "Shaivam/Vaishnavam"),
    (r"anjane|hanuman|maruti|maruthi|anjaneya", "Hanuman", "Vaishnavam"),
    (r"venkate|balaji|srinivas|tirupati|govindaraj", "Venkateswara", "Vaishnavam"),
    (r"narasimh|narasimm|lakshmi ?narasim", "Narasimha", "Vaishnavam"),
    (r"krishna|guruvayur|parthasarath|gopal|venugopal|rajagopal|udupi", "Krishna", "Vaishnavam"),
    (r"\brama\b|ramar|ramaswam|kodandaram|seetharam|sitaram|raghunath", "Rama", "Vaishnavam"),
    (r"jagannath", "Jagannath", "Vaishnavam"),
    (r"perumal|vishnu|narayan|ranganath|varadaraj|padmanabh|sowriraj|aravamudh|kesava|vittal|panduranga|ananta", "Vishnu (Perumal)", "Vaishnavam"),
    (r"amman|ammavaru|devi|durga|kali|mariamm|mariyamm|bhagavathi|bagavathi|bhagavati|meenakshi|kamakshi|parvathi|parvati|chamund|yellamm|renuka|shakti|sakthi|lakshmi|saraswathi|saraswati|gangamm|pochamma|bhavani|kanaka ?durga", "Devi (Amman / Shakti)", "Shaktam"),
    (r"shiva|siva|eswar|eeswar|ishwar|iswar|lingeswar|natar|nataraj|mahadev|someshwar|someswar|mallikarjun|ramalinga|nageshwar|nageswar|bhimeshwar|kasi ?visw|vishwanath|jambukeswar|arunachal|kapaleesh|ekambar|thyagaraj|tyagaraj|mahalinga|sankara|shankar|rudra|bhairav|dakshinamurth|vadakkunnath|ernakulathappan|thirunakkara", "Shiva", "Shaivam"),
    (r"navagraha|sani\b|shani|surya|suryanar", "Navagraha", "Smartam"),
    (r"nagar|naga\b|subbaraya", "Naga", "Folk"),
    (r"ayyanar|karuppa|muniswar|munisw|madasam|sudalai|veerabhadr|aiyanar", "Village deity (Ayyanar / Karuppasamy)", "Folk / Grama Devata"),
]
_COMPILED = [(re.compile(p, re.I), d, c) for p, d, c in DEITY_RULES]

DEITY_NORMALISE = {  # common OSM / Wikidata labels -> canonical
    "shiva": "Shiva", "siva": "Shiva", "vishnu": "Vishnu (Perumal)",
    "ganesha": "Ganesha", "ganesh": "Ganesha", "murugan": "Murugan (Kartikeya)",
    "kartikeya": "Murugan (Kartikeya)", "hanuman": "Hanuman", "durga": "Devi (Amman / Shakti)",
    "parvati": "Devi (Amman / Shakti)", "kali": "Devi (Amman / Shakti)", "krishna": "Krishna",
    "rama": "Rama", "venkateswara": "Venkateswara", "ayyappan": "Ayyappan (Sastha)",
}


def infer_deity(*texts: str) -> tuple[str, str]:
    """Return (deity, category) from name/deity text; ('', '') if unknown."""
    blob = " ".join(t for t in texts if t)
    for rx, deity, cat in _COMPILED:
        if rx.search(blob):
            return deity, cat
    return "", ""


def normalise_deity(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    canon = DEITY_NORMALISE.get(raw.lower())
    if canon:
        return canon
    d, _ = infer_deity(raw)
    return d or raw


def maps_url(lat, lon) -> str:
    if lat in (None, "") or lon in (None, ""):
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={float(lat):.6f},{float(lon):.6f}"


def blank_row(**kw) -> dict:
    row = {f: "" for f in FIELDS}
    row.update({k: ("" if v is None else v) for k, v in kw.items() if k in row})
    return row


def finish_row(row: dict) -> dict:
    """Fill derived columns (deity/category guess, maps link)."""
    if row.get("deity"):
        row["deity"] = normalise_deity(row["deity"])
    if not row.get("deity") or not row.get("category"):
        d, c = infer_deity(row.get("deity", ""), row.get("name", ""), row.get("name_local", ""))
        row["deity"] = row.get("deity") or d
        if not row.get("category"):
            if row.get("religion") == "jain":
                c = "Jain"
            elif row.get("religion") == "buddhist":
                c = "Buddhist"
            row["category"] = c
    if row.get("latitude") not in ("", None):
        row["latitude"] = round(float(row["latitude"]), 6)
        row["longitude"] = round(float(row["longitude"]), 6)
        row["google_maps_url"] = maps_url(row["latitude"], row["longitude"])
    return row


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def request_with_retry(s: requests.Session, method: str, url: str, tries: int = 4, **kw):
    """Polite retries with backoff (Overpass / Wikidata rate-limit with 429/504)."""
    delay = 15
    timeout = kw.pop("timeout", 360)
    for attempt in range(1, tries + 1):
        try:
            r = s.request(method, url, timeout=timeout, **kw)
            if r.status_code in (429, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} from {url}")
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            if attempt == tries:
                raise
            print(f"   retry {attempt}/{tries} after error: {e}; sleeping {delay}s")
            time.sleep(delay)
            delay *= 2
