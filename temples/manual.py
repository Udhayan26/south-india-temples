"""Government lists and private (personal) collections dropped in as files.

Put any .csv or .xlsx into
    data/sources/govt/      e.g. TN HR&CE temple list, data.gov.in, ASI lists
    data/sources/private/   e.g. your own field survey, a friend's collection
Column names are matched loosely (see ALIASES), so most sheets work as-is.
A single "GPS" / "Location" column like "12.9716, 77.5946" is understood too.

Map files work as well:
    .kml / .kmz   Google My Maps ("⋮ -> Download KML"), Google Earth, other map apps.
                  Pin name -> temple name, folder/layer -> Special Group,
                  "Key: value" lines in the pin description (Moolavar: ..., District: ...)
                  and ExtendedData fields are matched like spreadsheet columns.
    .geojson/.json  GeoJSON, incl. Google Takeout "Saved Places.json" (your starred places).
"""
from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

from .common import DATA, blank_row, finish_row

ALIASES = {
    "name": ["name", "temple name", "temple", "name of the temple", "temple_name", "kovil", "koil name"],
    "name_local": ["local name", "tamil name", "name (tamil)", "name_ta", "name_local", "malayalam name", "kannada name", "telugu name"],
    "deity": ["deity", "main deity", "presiding deity", "moolavar", "moolavar name", "god", "devata", "swamy"],
    "category": ["category", "type", "sect", "tradition", "temple type"],
    "special_group": ["special group", "group", "significance", "divya desam / padal petra"],
    "state": ["state"],
    "district": ["district", "dist", "district name"],
    "city": ["city", "town", "village", "taluk", "place", "locality"],
    "address": ["address", "full address", "location address"],
    "pincode": ["pincode", "pin", "pin code", "postal code", "zip"],
    "latitude": ["latitude", "lat", "y"],
    "longitude": ["longitude", "long", "lon", "lng", "x"],
    "govt_id": ["govt id", "temple id", "hrce id", "temple code", "code", "id", "s.no", "sl no"],
    "operator": ["operator", "managed by", "administration", "trust", "management"],
    "phone": ["phone", "contact", "mobile", "phone number"],
    "website": ["website", "url", "web"],
    "opening_hours": ["timings", "opening hours", "darshan time", "temple timings"],
    "description": ["notes", "remarks", "description", "history"],
}
GPS_COLS = ["gps", "gps location", "coordinates", "location", "lat long", "lat,long", "latlong"]
_LOOKUP = {a: f for f, al in ALIASES.items() for a in al}


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", str(h or "").strip().lower().replace("_", " "))


def _strip_html(t: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", t or "", flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    import html
    return html.unescape(t)


def _desc_fields(desc: str) -> dict:
    """'Moolavar: Shiva\nDistrict: Thanjavur' -> {'Moolavar': 'Shiva', 'District': 'Thanjavur'}"""
    out = {}
    for line in desc.splitlines():
        m = re.match(r"\s*([A-Za-z][\w ./()]{1,30}?)\s*[:=\-]\s*(.+)$", line)
        if m and _norm(m.group(1)) in _LOOKUP:
            out.setdefault(m.group(1), m.group(2).strip())
    return out


def _read_kml(path: Path) -> list[dict]:
    import xml.etree.ElementTree as ET
    import zipfile
    if path.suffix.lower() == ".kmz":
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            raw = z.read(name) if name else b""
    else:
        raw = path.read_bytes()
    root = ET.fromstring(raw)
    ns = lambda tag: f"{{http://www.opengis.net/kml/2.2}}{tag}"
    out = []

    doc_title = (root.findtext(f"{ns('Document')}/{ns('name')}") or "").strip()

    def walk(el, folder):
        for child in el:
            tag = child.tag.split("}")[-1]
            if tag in ("Folder", "Document"):
                fname = child.findtext(ns("name")) or folder
                walk(child, fname if tag == "Folder" else folder)
            elif tag == "Placemark":
                coords = None
                for g in ("Point", "Polygon", "LineString"):
                    c = child.find(f".//{ns(g)}//{ns('coordinates')}") or child.find(f".//{ns(g)}/{ns('coordinates')}")
                    if c is not None and c.text:
                        pts = [tuple(map(float, p.split(",")[:2])) for p in c.text.split() if "," in p]
                        if pts:
                            lon = sum(p[0] for p in pts) / len(pts)
                            lat = sum(p[1] for p in pts) / len(pts)
                            coords = (lat, lon)
                            break
                desc = _strip_html(child.findtext(ns("description")) or "")
                name = re.sub(r"^\s*\d{1,4}\s*[).:\-]\s*", "", (child.findtext(ns("name")) or "").strip())
                note = " ".join(desc.split())[:500]
                if folder:
                    note = f"[{folder}] {note}".strip()
                rec = {"Temple Name": name, "Notes": note}
                if coords:
                    rec["GPS"] = f"{coords[0]}, {coords[1]}"
                if doc_title:
                    rec["Group"] = doc_title
                for d in child.iter(ns("Data")):
                    rec.setdefault(d.get("name", ""), (d.findtext(ns("value")) or "").strip())
                for sd in child.iter(ns("SimpleData")):
                    rec.setdefault(sd.get("name", ""), (sd.text or "").strip())
                for k, v in _desc_fields(desc).items():
                    rec.setdefault(k, v)
                out.append(rec)

    walk(root, "")
    return out


def _read_geojson(path: Path) -> list[dict]:
    import json
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    feats = data.get("features", []) if isinstance(data, dict) else []
    out = []
    for f in feats:
        g, p = f.get("geometry") or {}, dict(f.get("properties") or {})
        loc = p.pop("location", None) if isinstance(p.get("location"), dict) else None   # Google Takeout
        rec = {k: (v if not isinstance(v, (dict, list)) else "") for k, v in p.items()}
        if loc:
            rec["_takeout"] = "1"
            rec.setdefault("Temple Name", loc.get("name") or p.get("Title") or "")
            rec.setdefault("Address", loc.get("address", ""))
        rec.setdefault("Temple Name", p.get("Title") or p.get("name") or "")
        if g.get("type") == "Point" and g.get("coordinates"):
            lon, lat = g["coordinates"][:2]
            if lat or lon:
                rec["GPS"] = f"{lat}, {lon}"
        if p.get("google_maps_url") or p.get("Google Maps URL"):
            rec["website"] = ""          # keep Google's own link out of the website column
        out.append(rec)
    return out


TEMPLE_NAME = re.compile(r"temple|kovil|koil|gudi|devasth|devalay|kshetr|mandir|amman|perumal|swamy|swami|"
                         r"eswarar|iswarar|alayam|basadi|kavu|ambalam|mutt|math\b|ashram", re.I)


def _looks_like_temple(name: str) -> bool:
    from .common import infer_deity
    return bool(TEMPLE_NAME.search(name) or infer_deity(name)[0])


def _read_any(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix in (".kml", ".kmz"):
        return _read_kml(path)
    if suffix in (".geojson", ".json"):
        return _read_geojson(path)
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        if not header:
            continue
        for vals in it:
            if any(v not in (None, "") for v in vals):
                out.append({str(h): ("" if v is None else v) for h, v in zip(header, vals)})
    return out


def _coord(v) -> float | None:
    try:
        f = float(str(v).strip())
        return f if f else None
    except ValueError:
        return None


def load(kind: str) -> list[dict]:
    """kind = 'govt' or 'private'"""
    folder = DATA / "sources" / kind
    label = "Government" if kind == "govt" else "Private collection"
    rows, today = [], date.today().isoformat()
    for path in sorted(folder.glob("*")):
        if path.suffix.lower() not in (".csv", ".xlsx", ".kml", ".kmz", ".geojson", ".json") \
                or path.name.startswith(("~", "TEMPLATE")):
            continue
        for rec in _read_any(path):
            if rec.pop("_takeout", None) and not _looks_like_temple(rec.get("Temple Name", "")):
                continue          # Saved Places also holds shops, restaurants...
            row = blank_row(sources=f"{label}: {path.stem}", last_updated=today)
            for col, val in rec.items():
                h = _norm(col)
                val = str(val).strip()
                if not val:
                    continue
                if h in GPS_COLS:
                    m = re.findall(r"-?\d+\.\d+", val)
                    if len(m) >= 2:
                        row["latitude"], row["longitude"] = m[0], m[1]
                elif h in _LOOKUP and not row[_LOOKUP[h]]:
                    row[_LOOKUP[h]] = val
            lat, lon = _coord(row["latitude"]), _coord(row["longitude"])
            # sanity: South India roughly lat 8–20, lon 74–85; fix swapped columns
            if lat and lon and lat > 70 and lon < 25:
                lat, lon = lon, lat
            if not (lat and lon and 6 <= lat <= 21 and 72 <= lon <= 86):
                lat = lon = None
            row["latitude"] = lat if lat else ""
            row["longitude"] = lon if lon else ""
            if row["special_group"]:
                from .wikidata import SPECIAL_GROUPS
                canon = [lab for rx, lab in SPECIAL_GROUPS if re.search(rx, row["special_group"], re.I)]
                row["special_group"] = "; ".join(canon) if canon else row["special_group"]
            if row["name"]:
                rows.append(finish_row(row))
    return rows
