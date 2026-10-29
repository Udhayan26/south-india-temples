"""ASI — list of Centrally Protected Monuments (asi.nic.in/pdf/CPM_List.pdf).

A public Government of India list: name, locality and district of every monument,
state by state. Temples in the six southern states are kept (and flagged as ASI
protected); sites with inscriptions / edicts also go to the inscriptions list.
No GPS in the list: during merge each one is matched to a mapped temple in its
locality, or placed at the locality (village) if no temple matches.
"""
from __future__ import annotations

import io
import re
from datetime import date

from .common import RAW, STATES, blank_row, finish_row, request_with_retry, session, write_rows
from .inscriptions import blank_ins, ins_type, write_ins

URL = "https://asi.nic.in/pdf/CPM_List.pdf"
SOURCE = "Government: ASI Centrally Protected Monuments"
SOUTH = {v.lower(): v for v in STATES.values()}
STATE_HEAD = re.compile(r"under the jurisdiction of\s+(.+?)\s*\(", re.I | re.S)
TEMPLE_WORDS = re.compile(r"temple|gudi|basadi|basti|shrine|mandapa|matha|devalay|koil|kovil|ratha|"
                          r"svami|swami|eswara|isvara|esvara|natha|linga|cave|vihara|stupa|chaitya", re.I)
INS_WORDS = re.compile(r"inscription|inscribed|edict|epigraph|copper ?plate|hero ?stone|viragal|nishidhi", re.I)


def _rows(pdf_bytes: bytes):
    import pdfplumber
    state = None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pg in pdf.pages:
            heads = []   # (top, state) of each "under the jurisdiction of <State> (" heading
            for hit in pg.search(r"jurisdiction of\s+([A-Za-z&.\s]+?)\s*\(", regex=True, return_groups=True):
                name = " ".join((hit.get("groups") or [hit["text"]])[0].split())
                heads.append((hit["top"], name))
            if not heads:   # heading split over two lines: fall back to the page text
                m = STATE_HEAD.search(pg.extract_text() or "")
                if m:
                    heads.append((0, " ".join(m.group(1).split())))
            for tbl in pg.find_tables():
                top = tbl.bbox[1]
                above = [h for h in heads if h[0] < top]
                if above:
                    state = above[-1][1]
                last = None
                for cells in tbl.extract():
                    cells = [(" ".join((c or "").split())) for c in cells] + ["", "", "", ""]
                    sl, name, locality, district = cells[:4]
                    if sl.lower().startswith("sl"):
                        continue
                    if re.fullmatch(r"\d+[A-Za-z]?\.?", sl):
                        last = {"state": state, "sl": sl.rstrip("."), "name": name,
                                "locality": locality, "district": district}
                        yield last
                    elif last and name and not sl:
                        last["name"] = f"{last['name']} {name}".strip()   # name continued


def fetch() -> int:
    r = request_with_retry(session(), "GET", URL, timeout=180, tries=3)
    today = date.today().isoformat()
    temples, ins = [], []
    rows = list(_rows(r.content))
    for x in rows:
        st = SOUTH.get((x["state"] or "").lower().replace("&", "and"))
        if not st or not x["name"]:
            continue
        name = x["name"].rstrip(" .")
        gid = f"ASI-{st[:2].upper()}-{x['sl']}"
        if TEMPLE_WORDS.search(name):
            temples.append(finish_row(blank_row(
                name=name, state=st, district=x["district"], city=x["locality"],
                heritage="ASI – Centrally Protected Monument", special_group="ASI – Monument of National Importance",
                operator="Archaeological Survey of India", govt_id=gid,
                sources=SOURCE, last_updated=today)))
        if INS_WORDS.search(name):
            ins.append(blank_ins(
                title=name, type=ins_type(name), temple_name=name if TEMPLE_WORDS.search(name) else "",
                village=x["locality"], district=x["district"], district_old=x["district"], state=st,
                reference=gid, source="ASI Centrally Protected Monuments", source_url=URL, last_updated=today))
    write_rows(RAW / "asi" / "cpm.csv", temples)
    write_ins(RAW / "ins_asi" / "cpm.csv", ins)
    return len(temples)
