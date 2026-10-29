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
    "temple_id", "name", "name_local", "deity", "category", "deity_basis", "religion",
    "special_group", "state", "district", "city", "address", "pincode",
    "latitude", "longitude", "google_maps_url",
    "osm_id", "wikidata_id", "google_place_id", "govt_id",
    "operator", "phone", "website", "opening_hours", "heritage",
    "inscription_count", "inscription_refs",
    "inception", "image", "description",
    "sources", "source_count", "last_updated",
]

# (regex on name/deity text, deity, category)
DEITY_RULES = [  # first match wins, so the order matters
    (r"\bjain|basadi|basti\b|parshwanath|parsvanath|mahaveer|mahavir|tirthankar|neminath|adinath|chandraprabh|bahubali|gommat", "Jain Tirthankara", "Jain"),
    (r"buddha|buddhist|vihar|stupa", "Buddha", "Buddhist"),
    (r"sai ?baba|saibaba|shirdi|sathya sai|satya sai|raghavendra|narayana ?guru|\bsndp\b|gurudev|guru ?mandir|\bguru\b|swami samarth|ramanuja|ramana maharshi|vallalar|aurobindo|meher baba|basavanna", "Guru / Saint", "Guru / Saint"),
    (r"murug|subram|subrahm|subhram|subbara|karthike|kartike|skanda|kumaraswam|velava|velan\b|dandayudha|shanmuk|arumuga|swaminath|palani", "Murugan (Kartikeya)", "Kaumaram"),
    (r"vinay|ganapat|ganesh|pillaiyar|pillayar|vigneshwar|vighnesh|vinayak", "Ganesha", "Ganapatyam"),
    (r"ayyap|ayap|aiyapp|sastha|shastha|sasta\b|shasta\b|dharmasast|dharmashast", "Ayyappan (Sastha)", "Shaivam/Vaishnavam"),
    (r"anjane|hanuman|maruti|maruthi|anjaneya|abhayanjaneya", "Hanuman", "Vaishnavam"),
    (r"venkate|venkata|balaji|srinivas|tirupati|govindaraj|thimmappa|timmappa", "Venkateswara", "Vaishnavam"),
    (r"narasimh|narasimm|narasinh|lakshmi ?narasim", "Narasimha", "Vaishnavam"),
    (r"krishna|guruvayur|parthasarath|gopal|venugopal|rajagopal|udupi|iskcon|hare krishna|radha", "Krishna", "Vaishnavam"),
    (r"\brama\b|\bram\b|ramar\b|ramaswam|ramalay|kodandaram|seetharam|sitaram|raghunath|ramachandra", "Rama", "Vaishnavam"),
    (r"jagannath", "Jagannath", "Vaishnavam"),
    (r"dattatre|\bdatta\b", "Dattatreya", "Smartam"),
    (r"perumal|vishnu|narayan|ranganath|varadaraj|padmanabh|sowriraj|aravamudh|kesava|keshava|chennakesh|vittal|vithoba|panduranga|ananta|azhagar|alagar|mahavishnu|varaha", "Vishnu (Perumal)", "Vaishnavam"),
    (r"navagraha|sani\b|shani|shanee|shanish|shanesh|surya|sooryanar|suryanar", "Navagraha", "Smartam"),
    (r"ayyanar|aiyanar|karupp|muniswar|munishwar|muneshwar|muneeshwar|muniyapp|madasam|madaswam|sudalai|muthappan|madappura|vettakkorumakan|vettakorumakan|pechi|sangili|kateri|chathan|kandakarnan", "Village deity (Ayyanar / Karuppasamy / Muthappan)", "Folk / Grama Devata"),
    (r"amman|ammavar|ammavaru|\bamma\b|devi|durga|kali|mariamm|mariyamm|maramma|maremma|bhagavath|bagavath|bhagavat|meenakshi|kamakshi|parvathi|parvati|chamund|yellamm|ellamm|renuka|shakti|sakthi|lakshmi|laxmi|saraswat|gangamm|ganganamm|pochamma|bhavani|eshwari|eswari|ishwari|iswari|thalli|talli\b|muthyalamm|peddamm|poleramm|paradevat|mookambik|kurumba|bhadrakal|bhadrakaal|sharada|sharadamb|vasavi|kanyaka|annapoorn|annapurn|banashankar|huligemm|chowdamm|\bmata\b|\bmaa\b|pidari|selliamm|angalamm|angala ?param|kanakadurg", "Devi (Amman / Shakti)", "Shaktam"),
    (r"shiva|siva|\bshiv\b|eswar|eeswar|eshwar|ishwar|iswar|lingeswar|lingam|\blinga\b|natar|nathar\b|nataraj|mahadev|someshwar|someswar|mallikarjun|ramalinga|nageshwar|nageswar|bhimeshwar|kasi ?visw|vishwanath|jambukeswar|arunachal|kapaleesh|ekambar|thyagaraj|tyagaraj|mahalinga|sankara|shankar|rudra|bhairav|dakshinamurth|vadakkunnath|ernakulathappan|thirunakkara|kailasanath|maheshwar|maheswar|mahadeshwar|manjunath|mahabaleshwar|mallesh|veerabhadr|virabhadr|basavesh|nandi\b|udaiyar|tirumalesh", "Shiva", "Shaivam"),
    (r"\bnaga\b|nagaraj|nagamma|naga ?devat|sarpa", "Naga", "Folk / Grama Devata"),
    # no deity in the name, but the kind of shrine is clear
    (r"\bkavu\b|\bkaavu\b", "", "Kavu (Sacred Grove, Kerala)"),
    (r"tharavad|kudumba|family", "", "Family shrine"),
    (r"bhajan", "", "Bhajana Mandir"),
    (r"\bkalari\b", "", "Kalari shrine"),
    (r"\bmutt\b|\bmath\b|ashram|\bmadom\b|\bmadam\b|\bmatam\b", "", "Mutt / Ashram"),
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
    # "Visalakshi Amman sametha Viswanathar" -> the main (moolavar) deity comes after "sametha"
    m = re.search(r"\b(samedha|sametha|samet|sametham)\b(.*)", blob, re.I)
    if m:
        d, c = infer_deity(m.group(2))
        if d:
            return d, c
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
        row["deity_basis"] = "Source data"
    if not row.get("deity") or not row.get("category"):
        d, c = infer_deity(row.get("deity", ""), row.get("name", ""), row.get("name_local", ""))
        if not row.get("deity") and d:
            row["deity"], row["deity_basis"] = d, "Guessed from name"
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
            if r.status_code >= 400:
                print(f"   HTTP {r.status_code}: {r.text[:200]!r}")
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
