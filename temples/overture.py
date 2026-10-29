"""Overture Maps Foundation — Places theme (open data).

Licences: CDLA-Permissive-2.0 / Apache-2.0 / CC0-1.0 depending on the contributor
(Meta, Microsoft, Foursquare, AllThePlaces...). No OpenStreetMap data inside.
Attribution: "Overture Maps Foundation" (see docs.overturemaps.org/attribution).

The latest release is read straight from the public S3 bucket with DuckDB, only
for the South India bounding box and temple-like categories. District comes from
the nearest OpenStreetMap temple during merge; places whose address says another
state are dropped here.

Writes data/raw/overture/<STATE>.csv (UNKNOWN.csv when the state is not given).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from .common import RAW, STATES, blank_row, finish_row, session

BUCKET = "https://overturemaps-us-west-2.s3.amazonaws.com"
BBOX = (74.0, 7.9, 84.9, 20.0)      # lon/lat: Kerala tip to north Telangana / AP
MIN_CONFIDENCE = 0.45
CATEGORY_RX = r"hindu_temple|buddhist_temple|jain_temple|temple|place_of_worship|shrine|monastery|religious"
NOT_RELIGION_RX = r"church|cathedral|chapel|mosque|masjid|synagogue|gurdwara|gurudwara|islam|christian|dargah"
NOT_TEMPLE = re.compile(r"church|cathedral|chapel|mosque|masjid|dargah|juma|gurudwara|gurdwara|synagogue|"
                        r"school|college|hospital|hotel|lodge|restaurant|store|shop|bank|atm|pharmacy|"
                        r"hall\b|apartment|residency|tours|travels|bus stop|road\b|street\b", re.I)
ALAYAM = r"devalayam|shivalayam|sivalayam|ramalayam|vishnvalayam|\balayam\b"   # not "Mettupalayam"
TEMPLE_NAME = re.compile(r"temple|kovil|koil|gudi|devasth|kshetr|kshethr|mandir|amman|perumal|swamy|swami|"
                         r"eswar|iswar|basadi|kavu|ambalam|mutt\b|math\b|vihar|stupa|" + ALAYAM + "|"
                         r"கோவில்|கோயில்|ஆலயம்|அம்மன்|ஈஸ்வர|ஸ்வரர்|பெருமாள்|முருக|விநாயக|பிள்ளையார்|"
                         r"ക്ഷേത്ര|ദേവീ|ദേവി|കാവ്|ഭഗവതി|മഹാദേവ|ശിവ|"
                         r"ದೇವಸ್ಥಾನ|ದೇವಾಲಯ|ಗುಡಿ|ಮಠ|ದೇವಿ|ಸ್ವಾಮಿ|ಅಮ್ಮ|ದೇವರ|"
                         r"దేవస్థానం|దేవాలయం|గుడి|ఆలయం|క్షేత్ర|స్వామి|అమ్మవారి|ఆలయ", re.I)
STRONG = re.compile(r"temple|kovil|koil|gudi|mandir|devasth|kshetr|kshethr|" + ALAYAM + "|"
                    r"கோவில்|கோயில்|ക്ഷേത്ര|ದೇವಸ್ಥಾನ|ದೇವಾಲಯ|దేవస్థానం|దేవాలయం|క్షేత్ర", re.I)
ORG = re.compile(r"trust|sangh|sangam|seva|samithi|samiti|society|association|cent(re|er)|kendra|ಕೇಂದ್ರ|"
                 r"office|foundation|committee|yanthram|yantra|kalyana mandapam|marriage|function hall", re.I)
REGION = {
    "IN-TN": ["tn", "tamil nadu", "tamilnadu"], "IN-KL": ["kl", "kerala"], "IN-KA": ["ka", "karnataka"],
    "IN-AP": ["ap", "andhra pradesh"], "IN-TG": ["tg", "ts", "telangana"],
    "IN-PY": ["py", "pondicherry", "puducherry"],
}
_REGION = {alias: code for code, al in REGION.items() for alias in al}


def latest_release() -> str:
    r = session().get(BUCKET, params={"list-type": "2", "prefix": "release/", "delimiter": "/"}, timeout=60)
    r.raise_for_status()
    rels = sorted(re.findall(r"<Prefix>release/([\d\-.]+(?:-beta\.\d+)?)/</Prefix>", r.text))
    rels = [x for x in rels if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", x)]
    if not rels:
        raise RuntimeError("could not list Overture releases")
    return rels[-1]


def _sql(con, src: str) -> str:
    cols = {r[0]: r[1] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{src}', hive_partitioning=1)").fetchall()}
    cat_cols = [c for c in ("taxonomy", "basic_category", "categories") if c in cols]
    cat = "lower(concat_ws(' ', " + ", ".join(f"CAST({c} AS VARCHAR)" for c in cat_cols) + "))"
    addr = cols.get("addresses", "")
    fields = {k: f"addresses[1].{k}" if k in addr else "NULL" for k in ("freeform", "locality", "region", "postcode")}
    first = lambda c: f"CAST({c}[1] AS VARCHAR)" if c in cols else "NULL"
    return f"""
SELECT id,
       names.primary                       AS name,
       {cat}                               AS cat,
       confidence,
       {fields['freeform']}                AS street,
       {fields['locality']}                AS locality,
       {fields['region']}                  AS region,
       {fields['postcode']}                AS postcode,
       {first('websites')}                 AS website,
       {first('phones')}                   AS phone,
       CAST(sources AS VARCHAR)            AS src,
       (bbox.xmin + bbox.xmax) / 2         AS lon,
       (bbox.ymin + bbox.ymax) / 2         AS lat
FROM read_parquet('{src}', hive_partitioning=1)
WHERE bbox.xmin BETWEEN {BBOX[0]} AND {BBOX[2]}
  AND bbox.ymin BETWEEN {BBOX[1]} AND {BBOX[3]}
  AND confidence >= {MIN_CONFIDENCE}
  AND regexp_matches({cat}, '{CATEGORY_RX}')
  AND NOT regexp_matches({cat}, '{NOT_RELIGION_RX}')
"""


def fetch() -> int:
    import duckdb
    release = latest_release()
    src = f"s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
    con = duckdb.connect()
    for stmt in ("INSTALL httpfs", "LOAD httpfs", "SET s3_region='us-west-2'", "SET threads=4"):
        con.execute(stmt)
    rows = con.execute(_sql(con, src)).fetchall()
    names = [d[0] for d in con.description]
    today = date.today().isoformat()
    out: dict[str, list] = defaultdict(list)
    for vals in rows:
        p = dict(zip(names, vals))
        name = (p["name"] or "").strip()
        if not name or NOT_TEMPLE.search(name):
            continue
        from .common import infer_deity
        if not (TEMPLE_NAME.search(name) or infer_deity(name)[0]):
            continue                      # bare place names ("Thakazhy"), businesses...
        if ORG.search(name) and not STRONG.search(name):
            continue                      # trusts, associations, halls without "temple" in the name
        region = (p["region"] or "").strip().lower()
        code = _REGION.get(region)
        if region and not code:
            continue                      # address says another state (Goa, Maharashtra, Odisha...)
        religion = "jain" if re.search(r"jain|basadi", (p["cat"] or "") + name, re.I) else \
                   "buddhist" if re.search(r"buddh|vihar|stupa", (p["cat"] or "") + name, re.I) else "hindu"
        out[code or "UNKNOWN"].append(finish_row(blank_row(
            name=name, religion=religion, state=STATES.get(code, ""),
            city=p["locality"] or "", address=", ".join(x for x in (p["street"], p["locality"]) if x),
            pincode=p["postcode"] or "", latitude=p["lat"], longitude=p["lon"],
            website=p["website"] or "", phone=p["phone"] or "",
            description=f"Overture {release}; confidence {p['confidence']:.2f}",
            sources="Overture Maps", last_updated=today, govt_id="")))
    from .common import write_rows
    for code, rs in out.items():
        write_rows(RAW / "overture" / f"{code}.csv", rs)
    return sum(len(v) for v in out.values())
