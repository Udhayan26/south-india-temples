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
SEP = " \u00b6 "   # paragraph boundary marker
BLOCKS = ["p", "div", "td", "th", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "center", "blockquote",
          "table", "ul", "ol", "dd", "dt"]
START_RX = re.compile(r"\bNo\s*\.\s*(\d{1,4}[A-Za-z]?)\b")
REF_RX = re.compile(r"\(\s*((?:A\s*\.\s*R|B\s*\.\s*K|M\s*\.\s*E\s*\.\s*R|A\s*\.\s*R\s*\.\s*E|E\s*\.\s*I|C\s*\.\s*P)"
                    r"[\s.]*(?:No|Nos)\s*\.?\s*[\w\-/,& ]{1,40}?\s+of\s+[\d\-]{4,9})\s*\.?\s*\)", re.I)
YEAR_RX = re.compile(r"(\d{3,4})\s*(?:-\d{1,4})?\s*A\.?\s*D\b|A\.?\s*D\.?\s*(\d{3,4})", re.I)
DATE_HINT = re.compile(r"A\.\s*D\b|\bA\.D|\bSaka\b|\bKali\b|\bKollam\b|regnal|\b\d{1,2}(?:st|nd|rd|th) year\b|century", re.I)
SPOT_START = re.compile(r"^(on|in|at|near|round|inside|built|from|below|above|engraved|found|lying)\b", re.I)
PLACE_END = re.compile(r"(district|taluk|taluka|french india|state|agency|presidency|zamindari)\.?$", re.I)
TEMPLE_RX = re.compile(r"(?:of\s+the\s+|the\s+)?((?:[A-Za-z][\w\-']*\s+){0,3}?[A-Za-z][\w\-']*)\s+temple\b", re.I)
TEMPLE_OF_RX = re.compile(r"temple\s+of\s+((?:[A-Z][\w\-']*\s?){1,3})", re.I)
STOPWORDS = {"the", "of", "in", "on", "at", "a", "an", "same", "this", "that", "central", "shrine", "main",
             "big", "small", "ruined", "old", "inner", "outer", "enclosure", "wall", "walls", "base", "north",
             "south", "east", "west", "and", "near", "village", "local", "wall", "gopura", "mandapa"}

DYNASTIES = ["Chola", "Pandya", "Pallava", "Vijayanagara", "Hoysala", "Kakatiya", "Rashtrakuta",
             "Chalukya", "Ganga", "Kadamba", "Nayaka", "Chera", "Reddi", "Yadava", "Sambuvaraya",
             "Bana", "Vaidumba", "Telugu Choda", "Kongu", "Muttaraiya", "Satavahana", "Ikshvaku", "Alupa"]
KING_DYNASTY = [
    (r"rajaraja|rajendra|kulottunga|kulothunga|parantaka|vikrama ?chola|aditya|gandaraditya|uttama ?chola|"
     r"sundara ?chola|rajadhiraja|rajamahendra|parakesari|rajakesari|koparakesari|kopparakesari", "Chola"),
    (r"krishnadeva|krishna ?raya|achyuta|sadasiva|bukka|harihara|devaraya|deva ?raya|virupaksha|"
     r"venkatapati|tirumala ?raya|saluva|narasimha ?raya|immadi|savanna|kampana", "Vijayanagara"),
    (r"sundara ?pandya|maravarman|jatavarman|srivallabha|vira ?pandya|kulasekhara", "Pandya"),
    (r"nandivarman|narasimhavarman|mahendravarman|rajasimha|dantivarman|nripatunga|aparajita|"
     r"paramesvaravarman|kampavarman|kopperunjinga", "Pallava"),
    (r"prataparudra|ganapatideva|rudramba|rudradeva|kakati", "Kakatiya"),
    (r"ballala|vishnuvardhana|hoysala", "Hoysala"),
    (r"kannaradeva|krishna iii|rashtrakuta|govinda iv|amoghavarsha", "Rashtrakuta"),
    (r"vikramaditya|someshvara|someswara|jagadekamalla|trailokyamalla|tribhuvanamalla|chalukya", "Chalukya"),
    (r"nayak|nayaka|tirumala ?nayak|achyutappa|raghunatha", "Nayaka"),
]
LANGS = ["Tamil", "Telugu", "Kannada", "Malayalam", "Sanskrit", "Grantha", "Prakrit"]


def page_text(soup: BeautifulSoup) -> str:
    """Whole page as one string; real paragraph boundaries kept as a marker."""
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    for node in soup.find_all(string=True):
        node.replace_with(re.sub(r"\s+", " ", str(node)))
    for br in soup.find_all("br"):
        br.replace_with(SEP)
    for tag in soup.find_all(BLOCKS):
        tag.insert_before(SEP)
        tag.insert_after(SEP)
    text = soup.get_text("")
    text = text.replace("\u00e2\u0080\u0094", "-").replace("\u00e2", "-")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"(\s*\u00b6\s*)+", SEP, text)


def _lines(soup: BeautifulSoup) -> list[str]:   # kept for debugging
    return [x.strip() for x in page_text(soup).split("\u00b6") if x.strip()]


def _entry_starts(text: str) -> list[re.Match]:
    out = []
    for m in START_RX.finditer(text):
        before = text[max(0, m.start() - 8):m.start()]
        after = text[m.end():m.end() + 12]
        if re.search(r"[RKEI]\s*\.\s*$|\(\s*$|[RKEI]\.\s*\u00b6?\s*$", before):
            continue                      # "(A.R. No. 215 of 1904)" is a reference, not an entry
        if re.match(r"\s*(to|-|and|&)\s*\d", after) or re.match(r"\s*of\s+\d{4}", after):
            continue                      # "No. 199 to 202" (contents) / "No. 5 of 1906"
        out.append(m)
    return out


def _segments(s: str) -> list[str]:
    s = re.sub(r"\[\d+\]", " ", s)
    parts = re.split(r"\u00b6|(?<=[a-zA-Z\)\]])\.\s+(?=[A-Z(])", s)
    return [p.strip(" .-:;,") for p in parts if p and p.strip(" .-:;,")]


def _titlecase(s: str) -> str:
    return s.title() if s.isupper() else s


def _place(seg: str) -> tuple[str, str, str]:
    parts = [p.strip(" .") for p in re.sub(r"\(.*?\)", "", seg).split(",") if p.strip(" .")]
    district = next((re.sub(r"\s*district$", "", p, flags=re.I) for p in parts if re.search(r"district$", p, re.I)), "")
    ti = next((i for i, p in enumerate(parts) if re.search(r"taluka?$", p, re.I)), None)
    taluk = re.sub(r"\s*taluka?$", "", parts[ti], flags=re.I) if ti is not None else ""
    village = parts[0] if parts else ""
    if SPOT_START.match(village) or re.search(r"taluka?$|district$", village, re.I):
        # "ON A STONE ... IN KARIYANGALA, NEAR POLALI, MANGALORE TALUK, ..."
        cand = parts[ti - 1] if ti else ""
        m = re.search(r"\b(?:in|at)\s+([A-Za-z][\w\-]+(?:\s[A-Za-z][\w\-]+)?)\s*$", parts[0], re.I)
        village = (m.group(1) if m else re.sub(r"^near\s+", "", cand, flags=re.I)) if (m or cand) else ""
    if not district:   # "Tribhuvani, Pondicherry, French India" -> a known old district name
        from .inscriptions import old_district
        district = next((p for p in parts[1:] if old_district(p)), "")
    return _titlecase(village), _titlecase(taluk), _titlecase(district)


def _temple(spot: str) -> str:
    m = TEMPLE_OF_RX.search(spot)
    if m:
        name = m.group(1).strip()
    else:
        ms = list(TEMPLE_RX.finditer(spot))
        if not ms:
            return ""
        words = ms[-1].group(1).split()
        mixed = not spot.isupper()
        # keep the capitalised name just before "temple": "prakara Bhaktavatsalesvara" -> "Bhaktavatsalesvara"
        keep = []
        for w in reversed(words):
            if w.lower() in STOPWORDS or (mixed and not w[0].isupper()):
                break
            keep.insert(0, w)
        name = " ".join(keep[-3:])
    name = name.strip(" ,.-")
    return (_titlecase(name) + " temple") if name and len(name) > 2 else ""


def parse_page(soup: BeautifulSoup, page: dict) -> list[dict]:
    text = page_text(soup)
    starts = _entry_starts(text)
    vt = page.get("volume_title", "")
    dynasty = next((d for d in DYNASTIES if d.lower() in vt.lower()), "")
    language = next((l for l in LANGS if l.lower() in vt.lower()), "")
    today = date.today().isoformat()
    out, seen_numbers = [], set()
    for k, m in enumerate(starts):
        number = m.group(1)
        end = starts[k + 1].start() if k + 1 < len(starts) else len(text)
        rest = text[m.end():end][:6000].lstrip(" .\u00b6-:")
        ref = REF_RX.search(rest[:250])
        reference = ""
        if ref and ref.start() < 80:
            reference = re.sub(r"\s+", " ", ref.group(1))
            reference = re.sub(r"A\s*\.\s*R\s*\.?", "A.R.", reference)
            reference = re.sub(r"B\s*\.\s*K\s*\.?", "B.K.", reference)
            rest = rest[ref.end():]
        segs = _segments(rest)
        if not segs or (not reference and not any(SPOT_START.match(x) or PLACE_END.search(x) for x in segs[:3])):
            continue                         # not an inscription heading
        head = segs[:5]
        place_seg = next((x for x in head[:3] if PLACE_END.search(x) and len(x) < 200), "")
        if not place_seg and head and "," in head[0] and len(head[0]) < 90 and not SPOT_START.match(head[0]) \
                and not re.match(r"(this|it|the|records?)\b", head[0], re.I):
            place_seg = head[0]
        spot = next((x for x in head[:4] if SPOT_START.match(x) and len(x) < 260), "")
        if not spot and place_seg and SPOT_START.match(place_seg):
            spot = place_seg
        village, taluk, district = _place(place_seg) if place_seg else ("", "", "")
        if not village and spot:
            mv = re.search(r"\b(?:at|in)\s+([A-Za-z][\w\-]+(?:[\s-][A-Za-z][\w\-]+)?)\s*$", spot, re.I)
            village = _titlecase(mv.group(1)) if mv else ""
        date_seg = next((x for x in head if x not in (spot, place_seg) and DATE_HINT.search(x) and len(x) < 140), "")
        ym = YEAR_RX.search(date_seg)
        year = (ym.group(1) or ym.group(2)) if ym else ""
        king = ""
        if date_seg:   # "Savanna Odeya, 1351 A.D." -> "Savanna Odeya"
            mm = re.search(r",\s*(?=[^,]*(?:\d{3,4}|Saka|Kali|Kollam|regnal|year|century))", date_seg, re.I)
            king = date_seg[:mm.start()].strip(" ,.") if mm else ""
            if len(king.split()) > 6 or re.match(r"(this|the|it|in|on|at|a|an|records?|dated)\b", king, re.I):
                king = ""
        used = {place_seg, spot, date_seg}
        summary = ""
        for x in segs:
            if x not in used and len(x) > 40:
                summary = x[:300]
                break
        row_dynasty = dynasty
        for rx, dyn in KING_DYNASTY:
            if re.search(rx, f"{king} {date_seg} {summary}", re.I):
                row_dynasty = dyn
                break
        key = (number, reference)
        if key in seen_numbers:
            continue
        seen_numbers.add(key)
        out.append(blank_ins(
            title=f"SII Vol. {page['volume']} No. {number}" + (f" — {village}" if village else ""),
            type=ins_type(f"{spot} {summary}"),
            temple_name=_temple(spot) or _temple(place_seg), location_detail=_titlecase(spot)[:300],
            village=village, taluk=taluk, district_old=district, king=king[:120], dynasty=row_dynasty,
            date_text=date_seg[:200], year_ce=year, language=language, reference=reference,
            volume=page["volume"], number=number, summary=summary,
            source="South Indian Inscriptions (whatisindia.com)", source_url=page["url"], last_updated=today,
        ))
    return out


def fetch_page(page: dict) -> int:
    rows = parse_page(_get(page["url"]), page)
    write_ins(RAW / "sii" / f"{page['path']}.csv", rows)
    return len(rows)
