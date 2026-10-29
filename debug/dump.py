import json, sys
sys.path.insert(0, ".")
from temples import sii
pages = json.load(open("data/state.json"))["sii_pages"]
want = ["12/stones_126_to_150", "12/stones_1_to_25", "1/part_1", "11/rashtrakutas", "11/tribhuvanamalla_vikramaditya",
        "19/", "9/", "17/", "26/", "3/", "13/", "2/", "27/"]
picked, seen = [], set()
for w in want:
    for p in pages:
        if p["path"].startswith(w) and p["path"] not in seen:
            picked.append(p); seen.add(p["path"]); break
for p in picked:
    lines = sii._lines(sii._get(p["url"]))
    starts = [i for i, l in enumerate(lines) if sii.NO_RX.match(l)]
    with open(f"debug/{p['path'].replace('/', '__')}.txt", "w") as fh:
        fh.write(f"# {p['url']}\n# volume_title: {p['volume_title']}\n# lines: {len(lines)}  NO_RX starts: {len(starts)}\n")
        fh.write("\n".join(lines[:160]))
    import time; time.sleep(8)
