"""South Indian Inscriptions (SII) volumes — online edition at whatisindia.com.

The SII volumes (Archaeological Survey of India) print each inscription under a
heading such as:

    No. 1
    (A. R. No. 503 of 1906.)
    Mopur, Pulivendla Taluk, Cuddapah District.
    On the left side of the wall at the entrance into the mandapa ... Bhairavesvara temple.
    Savanna Odeya, 1351 A.D.

Only these index fields (number, reference, village, taluk, district, place in the
temple, king, date) and a one-sentence summary are kept, with a link back to the
page for the full text. robots.txt is checked before every request and pages are
fetched slowly (one job = one page, with the collector's pause between jobs).

Jobs: "sii_index" discovers the volumes and their pages; "sii" parses one page.
"""
from __future__ import annotations

import re
import urllib.robotparser
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .common import RAW, session, request_with_retry
from .inscriptions import blank_ins, ins_type, write_ins

BASE = "https://www.whatisindia.com/inscriptions/south_indian_inscriptions/"
SEED_VOLUMES = [f"{BASE}volume_{n}/index.html" for n in (1, 2, 3, 16)]
SKIP_PAGES = re.compile(r"(index|preface|prefac|introd|intro|contents|topo|plates|addend|errat|abbrev|"
                        r"copyright|glossary|appendix)", re.I)

_robots: urllib.robotparser.RobotFileParser | None = None


def _allowed(url: str) -> bool:
    global _robots
    if _robots is None:
        _robots = urllib.robotparser.RobotFileParser()
        root = f"{urlparse(url).scheme}://{urlparse(url).netloc}/robots.txt"
        try:
            r = session().get(root, timeout=30)
            _robots.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            _robots.parse([])
    return _robots.can_fetch(session().headers["User-Agent"], url)


def _get(url: str) -> BeautifulSoup:
    if not _allowed(url):
        raise PermissionError(f"robots.txt disallows {url}")
    r = request_with_retry(session(), "GET", url, timeout=60, tries=3)
    r.encoding = r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def discover() -> list[dict]:
    """Find every volume and the inscription pages inside it."""
    volumes: dict[str, str] = {}
    todo = list(SEED_VOLUMES)
    seen = set()
    while todo:
        url = todo.pop()
        if url in seen:
            continue
        seen.add(url)
        try:
            soup = _get(url)
        except Exception as e:
            print(f"   skip {url}: {e}")
            continue
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"]).split("#")[0]
            if re.search(r"/south_indian_inscriptions/volume_\w+/index\.html?$", full) and full not in seen:
                todo.append(full)
        title = " ".join((soup.title.string or "").split()) if soup.title else ""
        h = soup.find(["h1", "h2", "h3"])
        volumes[url] = " ".join(h.get_text(" ").split()) if h else title
    pages = []
    for vol_url, vol_title in sorted(volumes.items()):
        try:
            soup = _get(vol_url)
        except Exception:
            continue
        vol_dir = vol_url.rsplit("/", 1)[0] + "/"
        vol = re.search(r"volume_(\w+)/", vol_url).group(1)
        for a in soup.find_all("a", href=True):
            full = urljoin(vol_url, a["href"]).split("#")[0]
            name = full.rsplit("/", 1)[-1]
            if full.startswith(vol_dir) and name.endswith((".html", ".htm")) and not SKIP_PAGES.search(name):
                pages.append({"url": full, "volume": vol, "volume_title": vol_title,
                              "path": f"{vol}/{name.rsplit('.', 1)[0]}"})
    uniq = {p["url"]: p for p in pages}
    return sorted(uniq.values(), key=lambda p: p["path"])


# ------------------------------------------------------------------ parsing
NO_RX = re.compile(r"^No\.?\s*(\d+[A-Za-z]?)\b\.?\s*(.*)$")
AR_RX = re.compile(r"A\.?\s*R\.?\s*(?:E\.?\s*)?No\.?\s*([\w/\-]+(?:\s*(?:and|&|,)\s*[\w/\-]+)*)\s+of\s+(\d{4})", re.I)
YEAR_RX = re.compile(r"(\d{3,4})\s*(?:-\d{1,4})?\s*A\.?\s*D\b|A\.?\s*D\.?\s*(\d{3,4})", re.I)
DATE_HINT = re.compile(r"A\.\s*D\b|\bA\.D\.|\bSaka\b|\bS\.\s*\d|\bKali\b|\bKollam\b|regnal|\byear\b|century", re.I)
SPOT_RX = re.compile(r"temple|shrine|slab|pillar|wall|rock|mandapa|gopura|stone|plate|basement|base|"
                     r"prakara|tank|cave|boulder|well|hill|beam|lintel", re.I)
TEMPLE_RX = re.compile(r"((?:[A-Z][\w\-']+\s?){1,4})\s+temple|temple\s+of\s+((?:[A-Z][\w\-']+\s?){1,4})")

DYNASTIES = ["Chola", "Pandya", "Pallava", "Vijayanagara", "Hoysala", "Kakatiya", "Rashtrakuta",
             "Chalukya", "Ganga", "Kadamba", "Nayaka", "Chera", "Reddi", "Yadava", "Sambuvaraya",
             "Bana", "Vaidumba", "Telugu Choda", "Kongu", "Muttaraiya", "Satavahana", "Ikshvaku"]
KING_DYNASTY = [
    (r"rajaraja|rajendra|kulottunga|kulothunga|parantaka|vikrama ?chola|aditya|gandaraditya|uttama ?chola|"
     r"sundara ?chola|rajadhiraja|rajamahendra|parakesari|rajakesari|koparakesari|kopparakesari", "Chola"),
    (r"krishnadeva|krishna ?raya|achyuta|sadasiva|bukka|harihara|devaraya|deva ?raya|virupaksha|"
     r"venkatapati|tirumala ?raya|saluva|narasimha ?raya|immadi|savanna|kampana", "Vijayanagara"),
    (r"sundara ?pandya|maravarman|jatavarman|srivallabha|vira ?pandya|kulasekhara", "Pandya"),
    (r"nandivarman|narasimhavarman|mahendravarman|rajasimha|dantivarman|nripatunga|aparajita|"
     r"paramesvaravarman|kampavarman", "Pallava"),
    (r"prataparudra|ganapatideva|rudramba|rudradeva|kakati", "Kakatiya"),
    (r"ballala|vishnuvardhana|hoysala", "Hoysala"),
    (r"kannaradeva|krishna iii|rashtrakuta", "Rashtrakuta"),
    (r"vikramaditya|someshvara|someswara|jagadekamalla|trailokyamalla|chalukya", "Chalukya"),
    (r"nayak|nayaka|tirumala ?nayak|achyutappa|raghunatha", "Nayaka"),
]
LANGS = ["Tamil", "Telugu", "Kannada", "Malayalam", "Sanskrit", "Grantha", "Prakrit"]


def _lines(soup: BeautifulSoup) -> list[str]:
    for br in soup.find_all("br"):
        br.replace_with("\n")
    text = soup.get_text("\n")
    return [" ".join(l.split()) for l in text.splitlines() if l.strip()]


def parse_page(soup: BeautifulSoup, page: dict) -> list[dict]:
    lines = _lines(soup)
    starts = [i for i, l in enumerate(lines) if NO_RX.match(l) and len(l) < 160]
    vt = page.get("volume_title", "")
    dynasty = next((d for d in DYNASTIES if d.lower() in vt.lower()), "")
    language = next((l for l in LANGS if l.lower() in vt.lower()), "")
    today = date.today().isoformat()
    out = []
    for k, i in enumerate(starts):
        block = lines[i:starts[k + 1] if k + 1 < len(starts) else len(lines)]
        m = NO_RX.match(block[0])
        number, rest = m.group(1), m.group(2)
        head = ([rest] if rest else []) + block[1:9]
        head_text = " ".join(head)
        ar = AR_RX.search(head_text)
        place_i = next((j for j, l in enumerate(head) if re.search(r"\bDistrict\b|\bTaluk\b|\bTaluka\b|\bState\b", l)), None)
        village = taluk = district = ""
        if place_i is not None:
            parts = [p.strip(" .") for p in re.sub(r"\(.*?\)", "", head[place_i]).split(",") if p.strip(" .")]
            village = parts[0] if parts else ""
            taluk = next((re.sub(r"\s*Taluka?$", "", p) for p in parts if re.search(r"Taluka?$", p)), "")
            district = next((re.sub(r"\s*District$", "", p) for p in parts if p.endswith("District")), "")
            if village.endswith(("Taluk", "District")):
                village = ""
        after = head[(place_i + 1) if place_i is not None else 0:]
        spot = next((l for l in after if SPOT_RX.search(l) and not AR_RX.search(l)), "")
        date_line = next((l for l in after if DATE_HINT.search(l) and l != spot and len(l) < 200), "")
        tm = TEMPLE_RX.search(spot) or TEMPLE_RX.search(head_text)
        temple = ""
        if tm:
            temple = (tm.group(1) or tm.group(2) or "").strip()
            temple = re.sub(r"^(the|of|in|on|near|at)\s+", "", temple, flags=re.I) + " temple"
        ym = YEAR_RX.search(date_line)
        year = (ym.group(1) or ym.group(2)) if ym else ""
        king = ""
        if date_line:   # "Savanna Odeya, 1351 A.D." -> "Savanna Odeya"
            mm = re.search(r",\s*(?=[^,]*(?:\d{3,4}|Saka|Kali|Kollam|regnal|year|century))", date_line, re.I)
            king = date_line[:mm.start()].strip(" ,.") if mm else ""
        for rx, dyn in KING_DYNASTY:
            if re.search(rx, king + " " + date_line, re.I):
                row_dynasty = dyn
                break
        else:
            row_dynasty = dynasty
        used = {head[place_i]} if place_i is not None else set()
        used |= {spot, date_line, block[0]}
        body = [l for l in block[1:] if l not in used and not AR_RX.fullmatch(l.strip("() ."))]
        summary = ""
        for l in body:
            if len(l) > 40 and not AR_RX.search(l):
                summary = re.split(r"(?<=[.!?])\s", l)[0][:300]
                break
        out.append(blank_ins(
            title=f"SII Vol. {page['volume']} No. {number}" + (f" — {village}" if village else ""),
            type=ins_type(head_text + " " + summary),
            temple_name=temple, location_detail=spot[:300], village=village, taluk=taluk,
            district_old=district, king=king[:120], dynasty=row_dynasty, date_text=date_line[:200],
            year_ce=year, language=language,
            reference=f"A.R. No. {ar.group(1)} of {ar.group(2)}" if ar else "",
            volume=page["volume"], number=number, summary=summary,
            source="South Indian Inscriptions (whatisindia.com)", source_url=page["url"], last_updated=today,
        ))
    return out


def fetch_page(page: dict) -> int:
    rows = parse_page(_get(page["url"]), page)
    write_ins(RAW / "sii" / f"{page['path']}.csv", rows)
    return len(rows)
