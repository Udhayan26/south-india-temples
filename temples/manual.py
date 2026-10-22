"""Government lists and private (personal) collections dropped in as files.

Put any .csv or .xlsx into
    data/sources/govt/      e.g. TN HR&CE temple list, data.gov.in, ASI lists
    data/sources/private/   e.g. your own field survey, a friend's collection
Column names are matched loosely (see ALIASES), so most sheets work as-is.
A single "GPS" / "Location" column like "12.9716, 77.5946" is understood too.
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


def _read_any(path: Path) -> list[dict]:
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
        if path.suffix.lower() not in (".csv", ".xlsx") or path.name.startswith(("~", "TEMPLATE")):
            continue
        for rec in _read_any(path):
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
            if row["name"]:
                rows.append(finish_row(row))
    return rows
