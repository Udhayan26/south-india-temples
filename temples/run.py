"""Daily time-boxed collector.

Each run works through a queue of small jobs (one district / one state at a
time), oldest first, until the time budget is used up, then rebuilds the master
CSV and the Excel file. Progress is saved in data/state.json, so tomorrow's run
continues where today's stopped. After the first full pass it keeps refreshing
the oldest data.

    python -m temples.run                    # default 50 min budget
    python -m temples.run --minutes 10 --states IN-TN
    python -m temples.run --export-only      # just rebuild Excel from what's collected
"""
from __future__ import annotations

import argparse
import csv
import json
import time
import traceback
from datetime import datetime, timedelta

from . import google_places, osm, wikidata
from .common import DATA, STATES
from .export import write_excel
from .merge import build_master

STATE_FILE = DATA / "state.json"
LOG_FILE = DATA / "run_log.csv"
REFRESH_DAYS = {"districts": 60, "osm": 30, "wikidata": 14, "google": 90}
EXPORT_RESERVE_MIN = 6       # keep time for merge + Excel
PAUSE_BETWEEN_CALLS = 10     # seconds; be polite to free public APIs


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"districts": {}, "done": {}}


def save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, indent=1, ensure_ascii=False, sort_keys=True))


def _due(st, key, kind) -> bool:
    last = st["done"].get(key)
    return not last or datetime.fromisoformat(last) < datetime.now() - timedelta(days=REFRESH_DAYS[kind])


def build_queue(st: dict, states: list[str]) -> list[tuple]:
    jobs = []
    for sc in states:
        if _due(st, f"districts:{sc}", "districts") or sc not in st["districts"]:
            jobs.append(("districts", sc, None))
        if _due(st, f"wikidata:{sc}", "wikidata"):
            jobs.append(("wikidata", sc, None))
        for d in st["districts"].get(sc, []):
            if _due(st, f"osm:{sc}:{d['rel_id']}", "osm"):
                jobs.append(("osm", sc, d))
            if google_places.enabled() and google_places.budget_left() > 0 and _due(st, f"google:{sc}:{d['rel_id']}", "google"):
                jobs.append(("google", sc, d))
    # a job that failed in the last 12 h waits until the next run
    recent = (datetime.now() - timedelta(hours=12)).isoformat()
    jobs = [j for j in jobs if st.get("failed", {}).get(_key(j), "") < recent]
    order = {"districts": 0, "wikidata": 1, "osm": 2, "google": 3}
    # never-done jobs first, then oldest refresh first
    return sorted(jobs, key=lambda j: (st["done"].get(_key(j), ""), order[j[0]]))


def _key(job) -> str:
    kind, sc, d = job
    return f"{kind}:{sc}" + (f":{d['rel_id']}" if d else "")


def run(minutes: float, states: list[str], export_only: bool = False) -> None:
    start = time.monotonic()
    deadline = start + max(1.0, minutes - EXPORT_RESERVE_MIN) * 60
    st = load_state()
    done_jobs, found, errors = 0, 0, 0
    consecutive_fail = 0

    while not export_only and time.monotonic() < deadline:
        queue = build_queue(st, states)
        if not queue:
            print("All data is fresh — nothing due today.")
            break
        job = queue[0]
        kind, sc, d = job
        label = f"{kind:9} {STATES[sc]}" + (f" / {d['name']}" if d else "")
        print(f"[{(time.monotonic() - start) / 60:5.1f} min] {label}", flush=True)
        try:
            if kind == "districts":
                st["districts"][sc] = osm.list_districts(sc)
                n = len(st["districts"][sc])
                print(f"   {n} districts")
            elif kind == "wikidata":
                n = wikidata.fetch_state(sc)
            elif kind == "osm":
                n = osm.fetch_district(sc, d)
            else:
                n = google_places.fetch_district(sc, d)
            if kind != "districts":
                found += n
                print(f"   {n} temples")
            st["done"][_key(job)] = datetime.now().isoformat(timespec="seconds")
            done_jobs += 1
            consecutive_fail = 0
            save_state(st)
        except Exception as e:  # keep going; the job stays due and is retried later
            errors += 1
            consecutive_fail += 1
            print(f"   FAILED: {e}")
            traceback.print_exc(limit=1)
            st.setdefault("failed", {})[_key(job)] = datetime.now().isoformat(timespec="seconds")
            save_state(st)
            if consecutive_fail >= 3:
                print("3 failures in a row — stopping collection for today.")
                break
        time.sleep(PAUSE_BETWEEN_CALLS)

    save_state(st)
    print("Merging sources…")
    records = build_master()
    path = write_excel(records)
    remaining = len(build_queue(st, list(STATES)))
    print(f"Excel: {path}  ({len(records):,} temples)  jobs done today: {done_jobs}, still due: {remaining}")

    new = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["run_at", "minutes", "jobs_done", "rows_fetched", "errors", "total_temples", "jobs_still_due"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), round((time.monotonic() - start) / 60, 1),
                    done_jobs, found, errors, len(records), remaining])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=50, help="time budget for this run (default 50)")
    ap.add_argument("--states", default=",".join(STATES), help="comma list, e.g. IN-TN,IN-KL")
    ap.add_argument("--export-only", action="store_true", help="skip collection, rebuild master + Excel")
    a = ap.parse_args()
    states = [s.strip() for s in a.states.split(",") if s.strip() in STATES]
    run(a.minutes, states, a.export_only)


if __name__ == "__main__":
    main()
