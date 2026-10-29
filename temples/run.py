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
import threading
import traceback
from datetime import datetime, timedelta

from . import asi, datagovin, google_places, osm, sii, wikidata
from .common import DATA, STATES
from .export import write_excel, write_geojson
from .merge import build_master

STATE_FILE = DATA / "state.json"
LOG_FILE = DATA / "run_log.csv"
REFRESH_DAYS = {"districts": 60, "osm": 30, "wikidata": 14, "places": 90, "google": 90,
                "sii_index": 90, "sii": 365, "datagovin": 30, "asi": 180}
EXPORT_RESERVE_MIN = 6       # keep time for merge + Excel
PAUSE_BETWEEN_CALLS = 10     # seconds between two requests to the SAME server

# one worker per server, so each server still sees one polite request at a time
WORKERS = {
    "overpass": ("districts", "osm", "places"),
    "wikidata": ("wikidata",),
    "sii": ("sii_index", "sii"),
    "other": ("asi", "datagovin", "google"),
}
ORDER = {"districts": 0, "osm": 1, "wikidata": 2, "sii_index": 3, "sii": 4, "places": 5,
         "datagovin": 6, "google": 7, "asi": 6}
GLOBAL = ("SII", "GOV")   # pseudo "states" for jobs not tied to a state


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"districts": {}, "done": {}}


def save_state(st: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False, sort_keys=True))
    tmp.replace(STATE_FILE)


def _due(st, key, kind) -> bool:
    last = st["done"].get(key)
    return not last or datetime.fromisoformat(last) < datetime.now() - timedelta(days=REFRESH_DAYS[kind])


def build_queue(st: dict, states: list[str], skip_kinds: frozenset = frozenset(),
                only_kinds: tuple | None = None) -> list[tuple]:
    jobs = []
    for sc in states:
        if sc == "SII":
            if _due(st, "sii_index:SII", "sii_index") or not st.get("sii_pages"):
                jobs.append(("sii_index", "SII", None))
            for pg in st.get("sii_pages", []):
                if _due(st, f"sii:{pg['path']}", "sii"):
                    jobs.append(("sii", "SII", pg))
            continue
        if sc == "GOV":
            if _due(st, "asi:GOV", "asi"):
                jobs.append(("asi", "GOV", None))
            for res in datagovin.resources():
                if _due(st, f"datagovin:{res['id']}", "datagovin"):
                    jobs.append(("datagovin", "GOV", res))
            continue
        dl = st["districts"].get(sc)
        # re-list districts when stale, missing, or saved before Wikidata IDs were kept
        if _due(st, f"districts:{sc}", "districts") or not dl or "wikidata" not in dl[0]:
            jobs.append(("districts", sc, None))
        for d in dl or []:
            for kind in ("osm", "wikidata", "places"):
                # Wikidata's search box is built from the district's OSM temples, so OSM comes first
                if kind == "wikidata" and f"osm:{sc}:{d['rel_id']}" not in st["done"]:
                    continue
                if _due(st, f"{kind}:{sc}:{d['rel_id']}", kind):
                    jobs.append((kind, sc, d))
            if google_places.enabled() and google_places.budget_left() > 0 and _due(st, f"google:{sc}:{d['rel_id']}", "google"):
                jobs.append(("google", sc, d))
    # a job that failed in the last 12 h waits until the next run
    recent = (datetime.now() - timedelta(hours=12)).isoformat()
    jobs = [j for j in jobs if st.get("failed", {}).get(_key(j), "") < recent and j[0] not in skip_kinds
            and (only_kinds is None or j[0] in only_kinds)]
    fails = st.get("fail_count", {})
    # district lists first; jobs that keep failing go to the back so they can't block the rest;
    # then never-done jobs, then oldest refresh first
    return sorted(jobs, key=lambda j: (j[0] not in ("districts", "sii_index"), fails.get(_key(j), 0),
                                       st["done"].get(_key(j), ""), ORDER[j[0]]))


def _key(job) -> str:
    kind, sc, d = job
    if kind == "sii":
        return f"sii:{d['path']}"
    if kind == "datagovin":
        return f"datagovin:{d['id']}"
    return f"{kind}:{sc}" + (f":{d['rel_id']}" if d else "")


def _label(job) -> str:
    kind, sc, d = job
    where = STATES.get(sc, sc)
    extra = (d.get("name") or d.get("path") or d.get("id")) if d else ""
    return f"{kind:9} {where}" + (f" / {extra}" if extra else "")


def _do(job, st) -> tuple[int, str]:
    kind, sc, d = job
    if kind == "districts":
        st["districts"][sc] = osm.list_districts(sc)
        return len(st["districts"][sc]), "districts"
    if kind == "wikidata":
        return wikidata.fetch_district(sc, d), "temples"
    if kind == "osm":
        return osm.fetch_district(sc, d), "temples"
    if kind == "places":
        return osm.fetch_places(sc, d), "villages/towns"
    if kind == "sii_index":
        st["sii_pages"] = sii.discover()
        return len(st["sii_pages"]), "inscription pages found"
    if kind == "sii":
        return sii.fetch_page(d), "inscriptions"
    if kind == "datagovin":
        return datagovin.fetch(d), "records"
    if kind == "asi":
        return asi.fetch(), "temples"
    return google_places.fetch_district(sc, d), "temples"


def run(minutes: float, states: list[str], export_only: bool = False) -> None:
    start = time.monotonic()
    deadline = start + max(1.0, minutes - EXPORT_RESERVE_MIN) * 60
    st = load_state()
    lock = threading.Lock()
    stats = {"done": 0, "found": 0, "errors": 0}
    running: set[str] = set()
    alive, idle = set(WORKERS), set()

    def worker(name, kinds):
        fails_in_a_row = 0
        try:
            while time.monotonic() < deadline:
                with lock:
                    queue = [j for j in build_queue(st, states, only_kinds=kinds) if _key(j) not in running]
                    job = queue[0] if queue else None
                    if job:
                        running.add(_key(job))
                        idle.discard(name)
                    else:
                        idle.add(name)
                        if alive <= idle:
                            return      # every server is out of work -> nobody can add more
                if job is None:         # other servers may still unlock work (e.g. OSM -> Wikidata)
                    time.sleep(5)
                    continue
                fails_in_a_row = _run_one(job, name, fails_in_a_row)
                if fails_in_a_row >= 5:
                    print(f"{name}: 5 failures in a row — pausing this server for today.", flush=True)
                    return
                time.sleep(PAUSE_BETWEEN_CALLS)
        finally:
            with lock:
                alive.discard(name)
                idle.discard(name)

    def _run_one(job, name, fails_in_a_row):
        print(f"[{(time.monotonic() - start) / 60:5.1f} min] {_label(job)}", flush=True)
        try:
            n, what = _do(job, st)
            print(f"   {n} {what}  ({_label(job).split()[0]})", flush=True)
            with lock:
                if what in ("temples", "inscriptions", "records"):
                    stats["found"] += n
                stats["done"] += 1
                st["done"][_key(job)] = datetime.now().isoformat(timespec="seconds")
                for k in ("failed", "errors", "fail_count"):
                    st.get(k, {}).pop(_key(job), None)
                save_state(st)
            return 0
        except Exception as e:  # keep going; the job stays due and is retried later
            print(f"   FAILED {_key(job)}: {e}", flush=True)
            traceback.print_exc(limit=1)
            with lock:
                stats["errors"] += 1
                now = datetime.now().isoformat(timespec="seconds")
                st.setdefault("failed", {})[_key(job)] = now
                st.setdefault("errors", {})[_key(job)] = str(e)[:300]   # visible in the repo
                st.setdefault("fail_count", {})[_key(job)] = st["fail_count"].get(_key(job), 0) + 1
                save_state(st)
            return fails_in_a_row + 1
        finally:
            with lock:
                running.discard(_key(job))

    if not export_only:
        threads = [threading.Thread(target=worker, args=(n, k), daemon=True) for n, k in WORKERS.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=max(0, deadline - time.monotonic()) + 120)
    done_jobs, found, errors = stats["done"], stats["found"], stats["errors"]

    save_state(st)
    print("Merging sources…")
    records = build_master()
    path = write_excel(records)
    write_geojson(records)
    remaining = len(build_queue(st, list(STATES) + list(GLOBAL)))
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
    ap.add_argument("--states", default=",".join(list(STATES) + list(GLOBAL)),
                    help="comma list, e.g. IN-TN,IN-KL (SII = inscription volumes, GOV = data.gov.in)")
    ap.add_argument("--export-only", action="store_true", help="skip collection, rebuild master + Excel")
    a = ap.parse_args()
    states = [s.strip() for s in a.states.split(",") if s.strip() in STATES or s.strip() in GLOBAL]
    run(a.minutes, states, a.export_only)


if __name__ == "__main__":
    main()
