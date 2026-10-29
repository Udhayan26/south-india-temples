"""data.gov.in — official Open Government Data API (needs a free API key).

1. Register at https://data.gov.in, then "My Account -> API key".
2. Add it as the GitHub secret DATA_GOV_IN_API_KEY.
3. List the datasets ("resources") to pull in data/sources/datagovin.json, e.g.
   {"resources": [{"id": "<resource id from the dataset page>", "name": "karnataka_ab_temples"}]}

Each resource is saved as data/sources/govt/datagovin_<name>.csv and then merged
like any other government list (column names are matched automatically).
"""
from __future__ import annotations

import csv
import json
import os
import time

from .common import DATA, request_with_retry, session

CONFIG = DATA / "sources" / "datagovin.json"
URL = "https://api.data.gov.in/resource/{id}"


def resources() -> list[dict]:
    if not os.environ.get("DATA_GOV_IN_API_KEY") or not CONFIG.exists():
        return []
    try:
        return [r for r in json.loads(CONFIG.read_text()).get("resources", []) if r.get("id")]
    except json.JSONDecodeError:
        print("   data/sources/datagovin.json is not valid JSON — skipped")
        return []


def fetch(res: dict) -> int:
    key = os.environ["DATA_GOV_IN_API_KEY"]
    s, records, offset = session(), [], 0
    while True:
        r = request_with_retry(s, "GET", URL.format(id=res["id"]), timeout=120, tries=3,
                               params={"api-key": key, "format": "json", "limit": 1000, "offset": offset})
        data = r.json()
        batch = data.get("records", [])
        records += batch
        offset += len(batch)
        if not batch or offset >= int(data.get("total", 0) or 0):
            break
        time.sleep(3)
    name = res.get("name") or res["id"]
    path = DATA / "sources" / "govt" / f"datagovin_{name}.csv"
    cols = list(dict.fromkeys(k for rec in records for k in rec))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(records)
    return len(records)
