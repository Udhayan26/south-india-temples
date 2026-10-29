"""Inscriptions: shared schema, loaders and linking to temples.

Sources (each writes rows with INS_FIELDS):
  data/raw/ins_osm/<STATE>/<district>.csv   OpenStreetMap epigraphs, hero stones, inscription tags
  data/raw/ins_wikidata/<STATE>/<district>.csv  Wikidata inscription / stele items
  data/raw/sii/<volume>/<page>.csv          South Indian Inscriptions volumes (index fields only)
  data/sources/inscriptions/*.csv|xlsx      your own / government lists (ARIE, TN Archaeology...)

Every inscription is then linked to a temple:
  1. a temple Wikidata ID it is "part of"          -> exact
  2. GPS: nearest temple within 300 m              -> exact
  3. SII: village found on the map (old district names understood) and a temple
     there with a matching name                    -> matched by name
  4. otherwise the village position is kept         -> village (approximate)
"""
from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher

from .common import DATA, RAW, STATES, maps_url, read_rows

INS_MASTER = DATA / "inscriptions_master.csv"

INS_FIELDS = [
    "inscription_id", "title", "type", "temple_id", "temple_name", "link_method",
    "location_detail", "village", "taluk", "district", "district_old", "state",
    "latitude", "longitude", "location_basis", "google_maps_url",
    "king", "dynasty", "date_text", "year_ce", "language", "script",
    "reference", "volume", "number", "summary", "text", "image",
    "source", "source_url", "osm_id", "wikidata_id", "temple_wikidata_id", "last_updated",
]

INSCRIPTION_WORDS = (r"inscription|kalvettu|kalvettukal|shasana|sasana|shila ?shasana|veerakal|veeragal|"
                     r"virakal|viragal|nadukal|nadukkal|hero ?stone|masti ?kal|mastikal|copper ?plate|"
                     r"edict|epigraph|brahmi|vattezhuthu|vatteluttu")
INS_RX = re.compile(INSCRIPTION_WORDS, re.I)


def blank_ins(**kw) -> dict:
    row = {f: "" for f in INS_FIELDS}
    row.update({k: ("" if v is None else v) for k, v in kw.items() if k in row})
    return row


def write_ins(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=INS_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def ins_type(tags_or_text: str) -> str:
    s = tags_or_text.lower()
    if re.search(r"hero ?stone|veerakal|veeragal|virakal|viragal|nadukal|nadukkal", s):
        return "Hero stone (Veerakal / Nadukal)"
    if re.search(r"masti", s):
        return "Sati stone (Masti kal)"
    if re.search(r"copper", s):
        return "Copper plate"
    if re.search(r"brahmi", s):
        return "Tamil-Brahmi / Brahmi inscription"
    return "Stone inscription"


# ---------------------------------------------------------------- old districts
# District names used in the SII / Annual Reports (Madras Presidency era)
# -> (state code, modern district names to look in first)
OLD_DISTRICTS = {
    "tanjore": ("IN-TN", ["Thanjavur", "Thiruvarur", "Nagapattinam", "Mayiladuthurai"]),
    "trichinopoly": ("IN-TN", ["Tiruchirappalli", "Karur", "Perambalur", "Ariyalur"]),
    "south arcot": ("IN-TN", ["Cuddalore", "Viluppuram", "Kallakurichi"]),
    "north arcot": ("IN-TN", ["Vellore", "Tiruvannamalai", "Ranipet", "Tirupathur"]),
    "chingleput": ("IN-TN", ["Chengalpattu", "Kancheepuram", "Kanchipuram", "Tiruvallur", "Thiruvallur"]),
    "chengalpattu": ("IN-TN", ["Chengalpattu", "Kancheepuram", "Kanchipuram", "Tiruvallur", "Thiruvallur"]),
    "madras": ("IN-TN", ["Chennai"]),
    "madura": ("IN-TN", ["Madurai", "Dindigul", "Theni"]),
    "madurai": ("IN-TN", ["Madurai", "Dindigul", "Theni"]),
    "ramnad": ("IN-TN", ["Ramanathapuram", "Sivaganga", "Virudhunagar"]),
    "tinnevelly": ("IN-TN", ["Tirunelveli", "Thoothukudi", "Tenkasi"]),
    "tirunelveli": ("IN-TN", ["Tirunelveli", "Thoothukudi", "Tenkasi"]),
    "pudukkottai": ("IN-TN", ["Pudukkottai"]), "pudukottai": ("IN-TN", ["Pudukkottai"]),
    "coimbatore": ("IN-TN", ["Coimbatore", "Tiruppur", "Erode"]),
    "salem": ("IN-TN", ["Salem", "Namakkal", "Dharmapuri", "Krishnagiri"]),
    "nilgiris": ("IN-TN", ["Nilgiris", "The Nilgiris"]),
    "kanyakumari": ("IN-TN", ["Kanniyakumari", "Kanyakumari"]),
    "malabar": ("IN-KL", ["Kozhikode", "Kannur", "Malappuram", "Palakkad", "Wayanad", "Kasaragod"]),
    "travancore": ("IN-KL", ["Thiruvananthapuram", "Kollam", "Pathanamthitta", "Alappuzha", "Kottayam", "Idukki"]),
    "cochin": ("IN-KL", ["Ernakulam", "Thrissur"]),
    "south kanara": ("IN-KA", ["Dakshina Kannada", "Udupi"]),
    "north kanara": ("IN-KA", ["Uttara Kannada"]),
    "bellary": ("IN-KA", ["Ballari", "Bellary", "Vijayanagara"]),
    "mysore": ("IN-KA", ["Mysuru", "Mysore", "Chamarajanagara", "Mandya"]),
    "dharwar": ("IN-KA", ["Dharwad", "Gadag", "Haveri"]),
    "bijapur": ("IN-KA", ["Vijayapura", "Bagalkote"]),
    "shimoga": ("IN-KA", ["Shivamogga"]), "kolar": ("IN-KA", ["Kolar", "Chikkaballapura"]),
    "tumkur": ("IN-KA", ["Tumakuru"]), "chitaldrug": ("IN-KA", ["Chitradurga"]),
    "hassan": ("IN-KA", ["Hassan"]), "kadur": ("IN-KA", ["Chikkamagaluru"]),
    "bangalore": ("IN-KA", ["Bengaluru Urban", "Bengaluru Rural", "Ramanagara"]),
    "coorg": ("IN-KA", ["Kodagu"]), "raichur": ("IN-KA", ["Raichur", "Koppal"]),
    "gulbarga": ("IN-KA", ["Kalaburagi", "Yadgir"]), "bidar": ("IN-KA", ["Bidar"]),
    "belgaum": ("IN-KA", ["Belagavi"]),
    "cuddapah": ("IN-AP", ["YSR Kadapa", "Kadapa", "Annamayya"]),
    "kurnool": ("IN-AP", ["Kurnool", "Nandyal"]),
    "anantapur": ("IN-AP", ["Anantapuramu", "Anantapur", "Sri Sathya Sai"]),
    "nellore": ("IN-AP", ["Sri Potti Sriramulu Nellore", "Nellore"]),
    "chittoor": ("IN-AP", ["Chittoor", "Tirupati", "Annamayya"]),
    "guntur": ("IN-AP", ["Guntur", "Palnadu", "Bapatla"]),
    "kistna": ("IN-AP", ["Krishna", "NTR"]), "krishna": ("IN-AP", ["Krishna", "NTR"]),
    "godavari": ("IN-AP", ["East Godavari", "West Godavari", "Kakinada", "Konaseema", "Eluru"]),
    "east godavari": ("IN-AP", ["East Godavari", "Kakinada", "Konaseema"]),
    "west godavari": ("IN-AP", ["West Godavari", "Eluru"]),
    "vizagapatam": ("IN-AP", ["Visakhapatnam", "Anakapalli", "Alluri Sitharama Raju", "Vizianagaram"]),
    "visakhapatnam": ("IN-AP", ["Visakhapatnam", "Anakapalli"]),
    "prakasam": ("IN-AP", ["Prakasam"]),
    "warangal": ("IN-TG", ["Warangal", "Hanumakonda"]), "karimnagar": ("IN-TG", ["Karimnagar"]),
    "nalgonda": ("IN-TG", ["Nalgonda", "Suryapet", "Yadadri Bhuvanagiri"]),
    "mahbubnagar": ("IN-TG", ["Mahabubnagar", "Nagarkurnool", "Wanaparthy", "Jogulamba Gadwal"]),
    "medak": ("IN-TG", ["Medak", "Sangareddy", "Siddipet"]), "nizamabad": ("IN-TG", ["Nizamabad", "Kamareddy"]),
    "adilabad": ("IN-TG", ["Adilabad", "Nirmal", "Mancherial"]), "khammam": ("IN-TG", ["Khammam"]),
    "hyderabad": ("IN-TG", ["Hyderabad", "Rangareddy"]),
    "pondicherry": ("IN-PY", ["Puducherry"]), "puducherry": ("IN-PY", ["Puducherry"]),
    "karaikal": ("IN-PY", ["Karaikal"]),
    "ganjam": ("OD", []),  # now Odisha — outside South India, kept but not linked
}


def old_district(text: str):
    t = re.sub(r"\bdistrict\b|\.", "", (text or "").lower()).strip()
    if t in OLD_DISTRICTS:
        return OLD_DISTRICTS[t]
    for k, v in OLD_DISTRICTS.items():
        if k in t:
            return v
    return None


# ---------------------------------------------------------------- name matching
_SUBS = [("aa", "a"), ("ee", "i"), ("ii", "i"), ("oo", "u"), ("uu", "u"), ("th", "t"), ("dh", "d"),
         ("bh", "b"), ("ph", "p"), ("kh", "k"), ("gh", "g"), ("zh", "l"), ("sh", "s"), ("ch", "c"),
         ("w", "v"), ("z", "s"), ("q", "k"), ("x", "ks")]
_STOP = re.compile(r"\b(sri|shri|shree|sree|arulmigu|temple|kovil|koil|gudi|swamy|swami|devasthanam|"
                   r"the|of|at|in|near|shrine)\b")


# colonial-era spellings in the SII -> today's names (as on the map)
PLACE_ALIASES = {
    "tanjore": "thanjavur", "tanjavur": "thanjavur", "trichinopoly": "tiruchirappalli", "trichy": "tiruchirappalli",
    "conjeeveram": "kanchipuram", "conjeevaram": "kanchipuram", "kanchi": "kanchipuram",
    "madura": "madurai", "tinnevelly": "tirunelveli", "cuddapah": "kadapa", "bellary": "ballari",
    "bangalore": "bengaluru", "mysore": "mysuru", "calicut": "kozhikode", "trivandrum": "thiruvananthapuram",
    "cochin": "kochi", "vizagapatam": "visakhapatnam", "masulipatam": "machilipatnam", "negapatam": "nagapattinam",
    "tuticorin": "thoothukudi", "ootacamund": "udhagamandalam", "chingleput": "chengalpattu",
    "pondicherry": "puducherry", "tranquebar": "tharangambadi", "mahabalipuram": "mamallapuram",
    "seven pagodas": "mamallapuram", "tiruvadi": "thiruvadi", "shiyali": "sirkazhi", "shiyali.": "sirkazhi",
    "palghat": "palakkad", "cannanore": "kannur", "quilon": "kollam", "alleppey": "alappuzha",
    "mangalore": "mangaluru", "shimoga": "shivamogga", "tumkur": "tumakuru", "hubli": "hubballi",
    "belgaum": "belagavi", "gulbarga": "kalaburagi", "bijapur": "vijayapura", "nellore": "nellore",
    "rajahmundry": "rajamahendravaram", "bezwada": "vijayawada", "cocanada": "kakinada", "chicacole": "srikakulam",
    "srikakulam": "srikakulam", "anantapur": "anantapuramu", "kurnool": "kurnool",
}


def skeleton(s: str) -> str:
    """Spelling-tolerant key: Mopur = Mopuru, Tiruvannamalai = Thiruvannaamalai."""
    s = _STOP.sub(" ", (s or "").lower())
    s = re.sub(r"[^a-z]", "", s)
    for a, b in _SUBS:
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)
    s = re.sub(r"(am|um|u|a|e|i)$", "", s)  # Telugu/Tamil endings: Mopuru, Kanchipuram
    return s


def name_sim(a: str, b: str) -> float:
    a, b = skeleton(a), skeleton(b)
    if not a or not b:
        return 0.0
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 6 and short in long_ and len(short) / len(long_) >= 0.6:
        return 0.92          # "Bhairavesvar" in "Bhairavesvaraswam" — not "esvar" in "Kalamesvar"
    return SequenceMatcher(None, a, b).ratio()


def _dist_m(lat1, lon1, lat2, lon2) -> float:
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (math.sin((p2 - p1) / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 12742000 * math.asin(math.sqrt(h))


class Grid:
    def __init__(self, items, cell=0.03):
        """items: (lat, lon, obj)"""
        self.cell, self.g = cell, defaultdict(list)
        for lat, lon, o in items:
            self.g[(int(lat / cell), int(lon / cell))].append((lat, lon, o))

    def around(self, lat, lon, max_m):
        reach = int(max_m / (self.cell * 111000)) + 1
        ci, cj = int(lat / self.cell), int(lon / self.cell)
        out = []
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                for plat, plon, o in self.g.get((ci + di, cj + dj), ()):
                    d = _dist_m(lat, lon, plat, plon)
                    if d <= max_m:
                        out.append((d, o))
        return sorted(out, key=lambda x: x[0])


# ---------------------------------------------------------------- manual files
ALIASES = {
    "title": ["title", "inscription", "inscription title", "name"],
    "temple_name": ["temple", "temple name", "name of the temple", "kovil"],
    "village": ["village", "place", "town", "city", "locality", "ur"],
    "taluk": ["taluk", "taluka", "mandal", "tehsil"],
    "district": ["district", "dist"],
    "location_detail": ["location", "position", "found on", "location detail", "place in temple"],
    "king": ["king", "ruler", "king name", "reign of"],
    "dynasty": ["dynasty", "kingdom", "period"],
    "date_text": ["date", "date text", "regnal year", "period date"],
    "year_ce": ["year", "year ce", "year (ce)", "ad", "a.d.", "ce"],
    "language": ["language", "lang"],
    "script": ["script"],
    "reference": ["reference", "ref", "ar no", "a.r. no", "are no", "arie no", "sii ref", "sii no", "number"],
    "summary": ["summary", "content", "notes", "remarks", "description", "subject"],
    "text": ["text", "transcription", "full text"],
    "type": ["type", "kind", "inscription type"],
    "image": ["image", "photo", "photo url"],
}
_LOOK = {a: f for f, al in ALIASES.items() for a in al}


def load_manual() -> list[dict]:
    from .manual import _norm, _read_any  # same flexible reader as temple lists
    folder = DATA / "sources" / "inscriptions"
    rows, today = [], date.today().isoformat()
    if not folder.exists():
        return rows
    for path in sorted(folder.glob("*")):
        if path.suffix.lower() not in (".csv", ".xlsx") or path.name.startswith(("~", "TEMPLATE")):
            continue
        for rec in _read_any(path):
            row = blank_ins(source=f"Collection: {path.stem}", last_updated=today)
            for col, val in rec.items():
                h, val = _norm(col), str(val).strip()
                if not val:
                    continue
                if h in ("gps", "gps location", "coordinates", "lat long", "latlong"):
                    m = re.findall(r"-?\d+\.\d+", val)
                    if len(m) >= 2:
                        row["latitude"], row["longitude"] = m[0], m[1]
                elif h in ("latitude", "lat"):
                    row["latitude"] = val
                elif h in ("longitude", "long", "lon", "lng"):
                    row["longitude"] = val
                elif h in _LOOK and not row[_LOOK[h]]:
                    row[_LOOK[h]] = val
            if not (row["title"] or row["temple_name"] or row["reference"]):
                continue
            row["type"] = row["type"] or ins_type(" ".join(rec_v for rec_v in map(str, rec.values())))
            row["location_basis"] = "Exact (from collection)" if row["latitude"] else ""
            rows.append(row)
    return rows


# ---------------------------------------------------------------- build + link
def _load_raw() -> list[dict]:
    rows = []
    for sub in ("ins_osm", "ins_wikidata", "sii"):
        for p in sorted((RAW / sub).rglob("*.csv")):
            rows += read_rows(p)
    return rows + load_manual()


def _places_index():
    by_key: dict[str, list] = defaultdict(list)
    for p in (RAW / "places").rglob("*.csv"):
        st = p.parent.name
        for r in read_rows(p):
            by_key[skeleton(r["name"])].append((float(r["lat"]), float(r["lon"]), st, p.stem))
    return by_key


def _dedupe(rows: list[dict]) -> list[dict]:
    seen, out = {}, []
    for r in rows:
        key = r.get("wikidata_id") or r.get("osm_id") or (
            r.get("reference") and f"{r['reference']}|{r.get('volume', '')}|{r.get('number', '')}")
        if key and key in seen:
            prev = seen[key]
            for f in INS_FIELDS:
                if not prev.get(f) and r.get(f):
                    prev[f] = r[f]
            continue
        if key:
            seen[key] = r
        out.append(r)
    return out


def build(temples: list[dict]) -> list[dict]:
    """Link inscriptions to temples; adds inscription_count / inscription_refs to temples."""
    rows = _dedupe(_load_raw())
    if not rows:
        return []
    t_by_wd = {t["wikidata_id"]: t for t in temples if t.get("wikidata_id")}
    t_by_osm = {t["osm_id"]: t for t in temples if t.get("osm_id")}
    grid = Grid([(float(t["latitude"]), float(t["longitude"]), t) for t in temples if t["latitude"] != ""])
    by_district = defaultdict(list)
    for t in temples:
        by_district[(t["state"], t["district"].lower())].append(t)
    places = _places_index()
    district_rel = {}  # state code -> {rel_id: district name}
    try:
        import json
        st = json.loads((DATA / "state.json").read_text())
        for sc, ds in st.get("districts", {}).items():
            district_rel[sc] = {str(d["rel_id"]): d["name"] for d in ds}
    except Exception:
        pass

    def link(r, t, method):
        r["temple_id"], r["temple_name"], r["link_method"] = t["temple_id"], t["name"], method
        r["state"] = t.get("state") or r.get("state", "")
        r["district"] = t.get("district") or r.get("district", "")   # modern district name
        r["village"] = r.get("village") or t.get("city", "")

    for r in rows:
        lat = float(r["latitude"]) if str(r.get("latitude", "")).strip() else None
        lon = float(r["longitude"]) if lat is not None else None
        # 1. explicit temple ids
        t = t_by_wd.get(r.get("temple_wikidata_id")) or t_by_osm.get(r.get("osm_id"))
        if t and r.get("temple_wikidata_id"):
            link(r, t, "Wikidata: part of temple")
        elif t:
            link(r, t, "Inscription recorded on the temple (OSM)")
        # 2. GPS
        elif lat is not None:
            near = grid.around(lat, lon, 300)
            if near:
                link(r, near[0][1], f"Nearest temple ({int(near[0][0])} m)")
            r["location_basis"] = r.get("location_basis") or "Exact (mapped)"
        # 3. village + temple name (SII, collections without GPS)
        else:
            _link_by_village(r, places, grid, by_district, district_rel, link)
        if r.get("latitude") not in ("", None):
            r["google_maps_url"] = maps_url(r["latitude"], r["longitude"])
        key = (r.get("wikidata_id") or r.get("osm_id") or
               f"{r.get('reference')}|{r.get('volume')}|{r.get('number')}|{r.get('title')}|{r.get('source')}")
        r["inscription_id"] = "INS-" + hashlib.md5(key.encode()).hexdigest()[:10].upper()

    counts, refs = defaultdict(int), defaultdict(list)
    for r in rows:
        if r.get("temple_id"):
            counts[r["temple_id"]] += 1
            ref = r.get("reference") or r.get("title")
            if ref and len(refs[r["temple_id"]]) < 6:
                refs[r["temple_id"]].append(ref)
    for t in temples:
        t["inscription_count"] = counts.get(t["temple_id"], "") or ""
        t["inscription_refs"] = "; ".join(refs.get(t["temple_id"], []))
    rows.sort(key=lambda r: (r.get("state", ""), r.get("district", ""), r.get("village", ""),
                             r.get("temple_name", ""), r.get("year_ce", "")))
    write_ins(INS_MASTER, rows)
    return rows


# dedications too common to match across a whole district without the village name
GENERIC = re.compile(r"^\s*(sri\s+)?(isvara|iswara|eswara|eshwar|ishvara|siva|shiva|vishnu|perumal|rama|krishna|"
                     r"hanuman|anjaneya|durga|jaina|jain|basava|basavanna|ramalinga|mallikarjuna|somesvara|"
                     r"someshvara|kalleshvara|kallesvara|virupaksha|narayana|venkatesa|ganesa|ganapati|vinayaka|"
                     r"pillaiyar|amman|kali|mariamman|subrahmanya|nagesvara|kailasanatha|chennakesava|"
                     r"gopalakrishna|lakshmi ?narasimha|narasimha|varadaraja|siddhesvara|ramesvara)"
                     r"(deva|devar|svamin|swamin|swami|swamy)?(\s+temple)?\s*$", re.I)


def _link_by_village(r, places, grid, by_district, district_rel, link):
    od = old_district(r.get("district_old") or r.get("district", ""))
    state_code, modern = (od or (None, []))
    if state_code in STATES:
        r["state"] = STATES[state_code]
    v = (r.get("village") or "").strip().lower()
    vkey = skeleton(PLACE_ALIASES.get(v, v))
    if not vkey:
        return
    cands = [c for c in places.get(vkey, []) if state_code in (None, c[2])]
    if modern and len(cands) > 1:
        prefer = [c for c in cands if district_rel.get(c[2], {}).get(c[3], "") in modern]
        cands = prefer or cands
    dedication = r.get("temple_name", "")
    best = None
    for plat, plon, sc, rel in cands[:5]:
        for d, t in grid.around(plat, plon, 3000):
            s = name_sim(dedication, t["name"]) if dedication else 0
            if s >= 0.72 and (best is None or s > best[0]):
                best = (s, t, plat, plon)
    if best:
        _, t, plat, plon = best
        link(r, t, "Village + temple name match")
        r["latitude"], r["longitude"] = t["latitude"], t["longitude"]
        r["location_basis"] = "Temple (matched by name in village)"
        return
    # temples whose village (city) column matches, even without a place node
    if dedication:
        for (st_name, _d), ts in by_district.items():
            if state_code in STATES and st_name != STATES[state_code]:
                continue
            for t in ts:
                if skeleton(t.get("city", "")) == vkey and name_sim(dedication, t["name"]) >= 0.72:
                    link(r, t, "Village + temple name match")
                    r["latitude"], r["longitude"] = t["latitude"], t["longitude"]
                    r["location_basis"] = "Temple (matched by name in village)"
                    return
    # dedication unique within the old district's modern districts: "Kripapurisvara temple"
    if dedication and modern:
        mk = {skeleton(m) for m in modern}
        pool = [t for (st_name, d), ts in by_district.items()
                if (state_code not in STATES or st_name == STATES[state_code]) and skeleton(d) in mk for t in ts]
        hits = [t for t in pool if name_sim(dedication, t["name"]) >= 0.85]
        named_here = [t for t in hits if vkey and (vkey in skeleton(t["name"]) or vkey == skeleton(t.get("city", "")))]
        ded = skeleton(dedication)
        distinctive = len(ded) >= 10 and not GENERIC.search(dedication)
        pick = named_here[0] if len(named_here) == 1 else (hits[0] if len(hits) == 1 and distinctive else None)
        if pick:
            link(r, pick, "Temple name match in district")
            r["latitude"], r["longitude"] = pick["latitude"], pick["longitude"]
            r["location_basis"] = "Temple (matched by name in district)"
            return
    if cands:
        plat, plon, sc, rel = cands[0]
        r["latitude"], r["longitude"] = plat, plon
        r["location_basis"] = "Village (approximate)"
        r["state"] = r.get("state") or STATES.get(sc, "")
        r["district"] = district_rel.get(sc, {}).get(rel, "") or r.get("district", "")
