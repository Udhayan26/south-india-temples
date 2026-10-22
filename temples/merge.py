"""Combine every source into one de-duplicated master list.

Matching order: shared IDs (Wikidata / OSM / Google / govt) -> GPS distance +
name similarity -> (no GPS) same district + near-identical name.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher

from . import manual
from .common import DATA, FIELDS, RAW, maps_url, read_rows, write_rows

MASTER = DATA / "temples_master.csv"

# Which source wins for each kind of field (first non-empty value is kept)
PRIORITY_DEFAULT = ["Private collection", "Government", "Wikidata", "OpenStreetMap", "Google Maps"]
PRIORITY_GPS = ["Private collection", "OpenStreetMap", "Wikidata", "Government", "Google Maps"]
LIST_FIELDS = {"special_group", "sources"}
ID_FIELDS = ["wikidata_id", "osm_id", "google_place_id"]

STOP = r"\b(sri|shri|shree|arulmigu|arulmighu|arulmiku|thiru|temple|kovil|koil|kovel|gudi|devasthanam|devasthana|devalayam|alayam|swamy|swami|thirukoil|thirukkoil|mandir|mandira|kshetram|kshetra|the|of|and|devaru|temples)\b"


def norm_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(STOP, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similar(a: str, b: str) -> float:
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def dist_m(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742000 * math.asin(math.sqrt(h))


CELL = 0.002  # ~220 m grid for spatial lookup


def _cell(lat, lon):
    return int(lat / CELL), int(lon / CELL)


def _src(row: dict) -> str:
    return row["sources"].split(":")[0].strip()


class Merger:
    def __init__(self):
        self.groups: list[list[dict]] = []
        self.by_id: dict[str, int] = {}
        self.grid: dict[tuple, list[int]] = defaultdict(list)
        self.by_district: dict[str, list[int]] = defaultdict(list)

    def _coords(self, row):
        try:
            return float(row["latitude"]), float(row["longitude"])
        except (TypeError, ValueError):
            return None

    def _find(self, row) -> int | None:
        for f in ID_FIELDS:
            if row.get(f) and f"{f}:{row[f]}" in self.by_id:
                return self.by_id[f"{f}:{row[f]}"]
        c = self._coords(row)
        if c:
            best, best_score = None, 0.0
            ci, cj = _cell(*c)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for gi in self.grid.get((ci + di, cj + dj), []):
                        for other in self.groups[gi]:
                            oc = self._coords(other)
                            if not oc:
                                continue
                            d = dist_m(c, oc)
                            sim = similar(row["name"], other["name"])
                            unnamed = not norm_name(row["name"]) or not norm_name(other["name"])
                            ok = (d <= 60 and (sim >= 0.5 or unnamed)) or (d <= 200 and sim >= 0.75)
                            score = sim + (1 - d / 250)
                            if ok and score > best_score:
                                best, best_score = gi, score
            if best is not None:
                return best
        # no GPS (typical for govt lists): same district + very similar name
        key = (row.get("district") or "").lower()
        if key and row["name"]:
            hits = {gi for gi in self.by_district.get(key, [])
                    for o in self.groups[gi] if similar(row["name"], o["name"]) >= 0.88}
            if len(hits) == 1:
                return hits.pop()
        return None

    def add(self, row, allow_new=True):
        gi = self._find(row)
        if gi is None:
            if not allow_new:
                return
            self.groups.append([])
            gi = len(self.groups) - 1
        self.groups[gi].append(row)
        for f in ID_FIELDS:
            if row.get(f):
                self.by_id.setdefault(f"{f}:{row[f]}", gi)
        c = self._coords(row)
        if c and gi not in self.grid[_cell(*c)]:
            self.grid[_cell(*c)].append(gi)
        if row.get("district"):
            self.by_district[row["district"].lower()].append(gi)


def _combine(rows: list[dict]) -> dict:
    out = {f: "" for f in FIELDS}
    rank = lambda order: (lambda r: order.index(_src(r)) if _src(r) in order else 99)
    ordered = sorted(rows, key=rank(PRIORITY_DEFAULT))
    for f in FIELDS:
        if f in LIST_FIELDS:
            vals = []
            for r in ordered:
                for v in str(r.get(f, "")).split("; "):
                    if v and v not in vals:
                        vals.append(v)
            out[f] = "; ".join(vals)
        else:
            out[f] = next((r[f] for r in ordered if str(r.get(f, "")).strip()), "")
    gps = next((r for r in sorted(rows, key=rank(PRIORITY_GPS)) if str(r.get("latitude", "")).strip()), None)
    if gps:
        out["latitude"], out["longitude"] = gps["latitude"], gps["longitude"]
        out["google_maps_url"] = maps_url(gps["latitude"], gps["longitude"])
    if out.get("google_place_id"):
        out["google_maps_url"] = ("https://www.google.com/maps/search/?api=1&query="
                                  f"{out['latitude']},{out['longitude']}&query_place_id={out['google_place_id']}")
    if not out["name"]:
        out["name"] = "(unnamed temple)"
    out["source_count"] = len({_src(r) for r in rows})
    out["last_updated"] = max((r.get("last_updated") or "") for r in rows) or date.today().isoformat()
    key = (out["wikidata_id"] or out["osm_id"] or out["govt_id"] or out["google_place_id"]
           or f"{out['name']}{out['latitude']}{out['longitude']}")
    out["temple_id"] = "SIT-" + hashlib.md5(key.encode()).hexdigest()[:10].upper()
    return out


def _fill_districts(records: list[dict]) -> None:
    """Fill missing state from district, and missing district from nearby temples (~10 km)."""
    coarse = defaultdict(Counter)
    for r in records:
        if r["district"] and r["latitude"] != "":
            coarse[(round(float(r["latitude"]), 1), round(float(r["longitude"]), 1))][(r["district"], r["state"])] += 1
    by_district = Counter()
    for r in records:
        if r["district"] and r["state"]:
            by_district[(r["district"].lower(), r["state"])] += 1
    for r in records:
        if r["district"] and not r["state"]:
            hits = [(n, st) for (d, st), n in by_district.items() if d == r["district"].lower()]
            if hits:
                r["state"] = max(hits)[1]
    for r in records:
        if not r["district"] and r["latitude"] != "":
            k = (round(float(r["latitude"]), 1), round(float(r["longitude"]), 1))
            if coarse.get(k):
                (r["district"], st), _ = coarse[k].most_common(1)[0]
                r["state"] = r["state"] or st


def build_master() -> list[dict]:
    m = Merger()
    for src in ("osm", "wikidata"):
        for p in sorted((RAW / src).rglob("*.csv")):
            for row in read_rows(p):
                m.add(row)
    for kind in ("govt", "private"):
        for row in manual.load(kind):
            m.add(row)
    add_new_google = os.environ.get("GOOGLE_ADD_NEW") == "1"
    for p in sorted((RAW / "google").rglob("*.csv")):
        for row in read_rows(p):
            m.add(row, allow_new=add_new_google)

    records = [_combine(g) for g in m.groups if g]
    _fill_districts(records)
    records.sort(key=lambda r: (r["state"], r["district"], r["name"] == "(unnamed temple)", r["name"]))
    write_rows(MASTER, records)
    return records
