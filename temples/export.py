"""Write the master list to output/south_india_temples.xlsx."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill

from .common import OUTPUT, STATES

XLSX = OUTPUT / "south_india_temples.xlsx"

COLUMNS = [  # (field, header, width)
    ("temple_id", "Temple ID", 16), ("name", "Temple Name", 38), ("name_local", "Local Name", 30),
    ("deity", "Deity (Moolavar)", 24), ("category", "Category / Tradition", 18),
    ("special_group", "Special Group", 24), ("religion", "Religion", 10),
    ("state", "State", 15), ("district", "District", 18), ("city", "City / Village", 18),
    ("address", "Address", 40), ("pincode", "PIN", 9),
    ("latitude", "Latitude", 11), ("longitude", "Longitude", 11), ("google_maps_url", "Google Maps", 13),
    ("operator", "Managed By", 22), ("phone", "Phone", 15), ("website", "Website", 26),
    ("opening_hours", "Timings", 20), ("heritage", "Heritage Status", 22), ("inception", "Built / Inception", 12),
    ("description", "Description", 36), ("image", "Image", 20),
    ("osm_id", "OSM ID", 16), ("wikidata_id", "Wikidata ID", 12), ("google_place_id", "Google Place ID", 20),
    ("govt_id", "Govt ID", 12), ("sources", "Sources", 30), ("source_count", "No. of Sources", 9),
    ("last_updated", "Last Updated", 12),
]
HEAD_FILL = PatternFill("solid", fgColor="8B1E1E")
HEAD_FONT = Font(bold=True, color="FFFFFF")
LINK_FONT = Font(color="0563C1", underline="single")


def _header(ws, labels):
    cells = []
    for lab in labels:
        c = WriteOnlyCell(ws, value=lab)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center", wrap_text=True)
        cells.append(c)
    ws.append(cells)


def _temple_sheet(wb, title, records):
    ws = wb.create_sheet(title[:31])
    for i, (_, _, w) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[_col(i)].width = w
    ws.freeze_panes = "C2"
    _header(ws, [h for _, h, _ in COLUMNS])
    for r in records:
        row = []
        for f, _, _ in COLUMNS:
            v = r.get(f, "")
            if f in ("latitude", "longitude", "source_count") and v != "":
                v = float(v) if f != "source_count" else int(v)
            if f == "google_maps_url" and v:
                c = WriteOnlyCell(ws, value=f'=HYPERLINK("{v}","Open map")')
                c.font = LINK_FONT
                row.append(c)
                continue
            row.append(v)
        ws.append(row)
    ws.auto_filter.ref = f"A1:{_col(len(COLUMNS))}{len(records) + 1}"


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _summary(wb, records):
    ws = wb.create_sheet("Summary", 0)
    ws.column_dimensions["A"].width = 34
    for c in "BCDEFGHIJ":
        ws.column_dimensions[c].width = 14
    bold = Font(bold=True, size=13)

    def title(text):
        c = WriteOnlyCell(ws, value=text)
        c.font = bold
        ws.append([])
        ws.append([c])

    t = WriteOnlyCell(ws, value="South India Temples — GPS Directory")
    t.font = Font(bold=True, size=16, color="8B1E1E")
    ws.append([t])
    ws.append([f"Updated {datetime.now():%d %b %Y %H:%M}", f"{len(records):,} temples"])

    title("By state")
    _header(ws, ["State", "Temples", "With GPS", "Named", "Deity known", "Multi-source"])
    by_state = defaultdict(list)
    for r in records:
        by_state[r["state"] or "(unknown)"].append(r)
    for st in list(STATES.values()) + sorted(set(by_state) - set(STATES.values())):
        rs = by_state.get(st, [])
        ws.append([st, len(rs), sum(r["latitude"] != "" for r in rs),
                   sum(r["name"] != "(unnamed temple)" for r in rs),
                   sum(bool(r["deity"]) for r in rs), sum(int(r["source_count"] or 0) > 1 for r in rs)])

    title("By category (tradition)")
    cats = Counter(r["category"] or "(not identified)" for r in records)
    states = list(STATES.values())
    _header(ws, ["Category"] + states + ["Total"])
    for cat, n in cats.most_common():
        ws.append([cat] + [sum(1 for r in by_state.get(s, []) if (r["category"] or "(not identified)") == cat)
                           for s in states] + [n])

    title("Top deities")
    _header(ws, ["Deity", "Temples"])
    for d, n in Counter(r["deity"] for r in records if r["deity"]).most_common(25):
        ws.append([d, n])

    title("Special groups")
    _header(ws, ["Group", "Temples"])
    groups = Counter(g for r in records for g in r["special_group"].split("; ") if g)
    for g, n in groups.most_common():
        ws.append([g, n])

    title("Records per source")
    _header(ws, ["Source", "Temples"])
    srcs = Counter(s.split(":")[0].strip() for r in records for s in set(r["sources"].split("; ")) if s)
    for s, n in srcs.most_common():
        ws.append([s, n])


def _notes(wb):
    ws = wb.create_sheet("About")
    ws.column_dimensions["A"].width = 120
    for line in [
        "Data sources & licences",
        "OpenStreetMap — © OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright). Attribution required when sharing.",
        "Wikidata — CC0 public domain.",
        "Google Maps — Places API; place IDs only by default. Follow Google Maps Platform terms for stored content.",
        "Government — files in data/sources/govt (e.g. HR&CE, data.gov.in, ASI). Check each dataset's licence.",
        "Private collection — files in data/sources/private.",
        "",
        "Deity and Category are taken from the source when given, otherwise guessed from the temple name — verify important rows.",
        "Category: Shaivam (Shiva), Vaishnavam (Vishnu), Shaktam (Devi/Amman), Kaumaram (Murugan), Ganapatyam (Ganesha), Folk / Grama Devata, Jain, Buddhist.",
        "Google Maps column opens the exact GPS point.",
    ]:
        ws.append([line])


def write_excel(records: list[dict]) -> str:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    wb = Workbook(write_only=True)
    _summary(wb, records)
    _temple_sheet(wb, "All Temples", records)
    for st in STATES.values():
        rs = [r for r in records if r["state"] == st]
        if rs:
            _temple_sheet(wb, st, rs)
    _notes(wb)
    tmp = XLSX.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(XLSX)
    return str(XLSX)
