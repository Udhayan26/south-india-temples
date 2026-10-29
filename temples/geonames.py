"""GeoNames.org gazetteer — India dump (CC BY 4.0, attribution "GeoNames.org").

Keeps temples and shrines (feature codes TMPL temple, SHRN shrine, MSTY monastery,
PGDA pagoda) in the six southern states. The state comes from GeoNames' own
admin1 code, so it is exact.

Writes data/raw/geonames/<STATE>.csv
"""
from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from datetime import date

from .common import RAW, STATES, blank_row, finish_row, request_with_retry, session, write_rows

DUMP = "https://download.geonames.org/export/dump/IN.zip"
ADMIN1 = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"
CODES = {"TMPL": "Temple", "SHRN": "Shrine", "MSTY": "Monastery", "PGDA": "Pagoda"}
NAME_TO_CODE = {"tamil nadu": "IN-TN", "kerala": "IN-KL", "karnataka": "IN-KA", "andhra pradesh": "IN-AP",
                "telangana": "IN-TG", "puducherry": "IN-PY", "pondicherry": "IN-PY"}


def _admin1() -> dict[str, str]:
    """GeoNames admin1 code (e.g. '25') -> our state code, read from GeoNames itself."""
    txt = request_with_retry(session(), "GET", ADMIN1, timeout=120, tries=3).text
    out = {}
    for line in txt.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].startswith("IN."):
            code = NAME_TO_CODE.get(parts[1].strip().lower()) or NAME_TO_CODE.get(parts[2].strip().lower() if len(parts) > 2 else "")
            if code:
                out[parts[0].split(".")[1]] = code
    return out


def fetch() -> int:
    a1 = _admin1()
    raw = request_with_retry(session(), "GET", DUMP, timeout=300, tries=3).content
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read("IN.txt").decode("utf-8")
    today = date.today().isoformat()
    out: dict[str, list] = defaultdict(list)
    for parts in csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(parts) < 11 or parts[6] != "S" or parts[7] not in CODES:
            continue
        code = a1.get(parts[10])
        if not code:
            continue
        alt = [a for a in parts[3].split(",") if a and not a.isascii()][:1]
        out[code].append(finish_row(blank_row(
            name=parts[1], name_local=alt[0] if alt else "", state=STATES[code],
            religion="buddhist" if parts[7] in ("MSTY", "PGDA") else "hindu",
            latitude=float(parts[4]), longitude=float(parts[5]),
            description=f"GeoNames {CODES[parts[7]]} #{parts[0]}",
            sources="GeoNames", last_updated=today)))
    for code, rs in out.items():
        write_rows(RAW / "geonames" / f"{code}.csv", rs)
    return sum(len(v) for v in out.values())
