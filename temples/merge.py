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
from .common import DATA, FIELDS, RAW, infer_deity, maps_url, read_rows, write_rows

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
        self.by_district: dict[str, set[int]] = defaultdict(set)

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
        if not c and key and norm_name(row["name"]):
            target = norm_name(row["name"])
            hits = {gi for gi in self.by_district.get(key, ())
                    for o in self.groups[gi] if o.get("_norm") and o["_norm"][:2] == target[:2]
                    and similar(row["name"], o["name"]) >= 0.88}
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
        row["_norm"] = norm_name(row["name"])
        if row.get("district"):
            self.by_district[row["district"].lower()].add(gi)


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
    best = sorted(rows, key=lambda r: (r.get("deity_basis") != "Source data", rank(PRIORITY_DEFAULT)(r)))
    pick = next((r for r in best if r.get("deity")), None)
    if pick:
        out["deity"], out["deity_basis"] = pick["deity"], pick.get("deity_basis", "")
        out["category"] = pick.get("category") or out["category"]
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


def _upgrade(row: dict) -> dict:
    """Raw files saved before 'deity_basis' existed had their deity guessed with older,
    weaker rules — guess again with the current rules."""
    if "deity_basis" not in row:
        d, c = infer_deity(row.get("name", ""), row.get("name_local", ""))
        if d or c:
            row["deity"], row["category"] = d, c or row.get("category", "")
        row["deity_basis"] = "Guessed from name" if row.get("deity") else ""
    return row


class Nearest:
    """Grid index for 'nearest point within X km' lookups."""
    def __init__(self, points, cell=0.05):
        self.cell, self.grid = cell, defaultdict(list)
        for lat, lon, val in points:
            self.grid[(int(lat / cell), int(lon / cell))].append((lat, lon, val))

    def find(self, lat, lon, max_m):
        ci, cj, best = int(lat / self.cell), int(lon / self.cell), (max_m, None)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for plat, plon, val in self.grid.get((ci + di, cj + dj), []):
                    d = dist_m((lat, lon), (plat, plon))
                    if d < best[0]:
                        best = (d, val)
        return best[1]


PLACE_RANK = {"city": 0, "town": 1, "suburb": 2, "village": 3, "neighbourhood": 4, "locality": 5, "hamlet": 6}


def _fill_places(records: list[dict]) -> None:
    """City / Village for every temple from the nearest OSM village/town (within 4 km)."""
    pts = []
    for p in (RAW / "places").rglob("*.csv"):
        for r in read_rows(p):
            pts.append((float(r["lat"]), float(r["lon"]), r["name"]))
    if not pts:
        return
    idx = Nearest(pts)
    for r in records:
        if not r["city"] and r["latitude"] != "":
            r["city"] = idx.find(float(r["latitude"]), float(r["longitude"]), 4000) or ""


def _refine_wikidata_only(records: list[dict]) -> None:
    """Wikidata rows are fetched by bounding box, so their district can be a neighbour's.
    Take district/state from the nearest OSM temple instead (within 8 km, else 25 km)."""
    pts = [(float(r["latitude"]), float(r["longitude"]), (r["district"], r["state"]))
           for r in records if "OpenStreetMap" in r["sources"] and r["latitude"] != "" and r["district"]]
    near, wide = Nearest(pts, cell=0.08), Nearest(pts, cell=0.25)
    for r in records:
        if r["sources"] == "Wikidata" and r["latitude"] != "":
            lat, lon = float(r["latitude"]), float(r["longitude"])
            hit = near.find(lat, lon, 8000) or wide.find(lat, lon, 25000)
            if hit:
                r["district"], r["state"] = hit


def build_master() -> list[dict]:
    m = Merger()
    for src in ("osm", "wikidata"):
        for p in sorted((RAW / src).rglob("*.csv")):
            for row in read_rows(p):
                m.add(_upgrade(row))
    for kind in ("govt", "private"):
        for row in manual.load(kind):
            m.add(row)
    add_new_google = os.environ.get("GOOGLE_ADD_NEW") == "1"
    for p in sorted((RAW / "google").rglob("*.csv")):
        for row in read_rows(p):
            m.add(_upgrade(row), allow_new=add_new_google)

    records = [_combine(g) for g in m.groups if g]
    _refine_wikidata_only(records)
    _fill_districts(records)
    _fill_places(records)
    from . import inscriptions
    inscriptions.build(records)        # links inscriptions, adds counts to temples
    records.sort(key=lambda r: (r["state"], r["district"], r["name"] == "(unnamed temple)", r["name"]))
    write_rows(MASTER, records)
    return records
