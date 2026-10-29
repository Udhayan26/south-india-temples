"""Check what a website allows and offers before writing a collector for it.

For each URL: robots.txt verdict for our user agent, HTTP status, type, size,
title, headings, forms (fields + select options), and links that look like
temple lists / downloads. Only this summary is saved (data/probe/report.json).

    python -m temples.probe                 # URLs from data/probe/targets.txt
    python -m temples.probe URL [URL ...]
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.robotparser
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .common import DATA, session

OUT = DATA / "probe" / "report.json"
INTERESTING = re.compile(r"temple|devasth|kovil|list|district|download|pdf|xls|csv|search|directory|"
                         r"institution|kml|kmz|export|monument|ಪಟ್ಟಿ|ಘೋಷ್ವಾರೆ|ಸಂಸ್ಥೆ|பட்டியல்|కోవెల|ദേവസ്വം", re.I)
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


def robots_ok(url: str) -> tuple[bool, str]:
    root = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    if root not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = session().get(root + "/robots.txt", timeout=30)
            text = r.text if r.status_code == 200 else ""
        except Exception as e:
            text = ""
        rp.parse(text.splitlines())
        rp._raw = text[:3000]
        _robots[root] = rp
    rp = _robots[root]
    return rp.can_fetch(session().headers["User-Agent"], url), getattr(rp, "_raw", "")


def probe(url: str) -> dict:
    """url, or "POST url|a=1&b=2" to submit a form."""
    data = None
    if url.startswith("POST "):
        url, _, form = url[5:].partition("|")
        from urllib.parse import parse_qsl
        data = dict(parse_qsl(form, keep_blank_values=True))
    ok, raw = robots_ok(url)
    rec = {"url": url, "post": data, "robots_allows": ok, "robots_txt": raw}
    if not ok:
        return rec
    try:
        r = (session().post(url, data=data, timeout=90) if data is not None
             else session().get(url, timeout=60, allow_redirects=True))
    except Exception as e:
        rec["error"] = str(e)[:300]
        return rec
    ctype = r.headers.get("content-type", "")
    rec.update({"status": r.status_code, "final_url": r.url, "content_type": ctype, "bytes": len(r.content)})
    if "pdf" in ctype:
        import io
        import pdfplumber
        pages = []
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            rec["pdf_pages"] = len(pdf.pages)
            for i, pg in enumerate(pdf.pages):
                if i not in (0, 1, 3, 4, 5) and i % 12:
                    continue
                tables = pg.extract_tables()
                pages.append({"page": i + 1, "text": (pg.extract_text() or "")[:600],
                              "tables": [t[:8] for t in tables[:2]]})
        rec["pdf_sample"] = pages[:20]
        return rec
    if "html" not in ctype and "xml" not in ctype:
        rec["first_bytes"] = r.content[:200].decode("latin-1")
        return rec
    soup = BeautifulSoup(r.text, "html.parser")
    rec["title"] = " ".join((soup.title.string or "").split()) if soup.title else ""
    rec["headings"] = [" ".join(h.get_text(" ").split())[:120] for h in soup.find_all(["h1", "h2", "h3"])][:20]
    forms = []
    for f in soup.find_all("form")[:6]:
        fields = []
        for el in f.find_all(["input", "select", "textarea"]):
            d = {"tag": el.name, "name": el.get("name"), "type": el.get("type")}
            if el.name == "select":
                opts = [(o.get("value"), o.get_text(" ").strip()) for o in el.find_all("option")]
                d["options"] = len(opts)
                d["sample_options"] = opts[:40]
            fields.append(d)
        forms.append({"action": urljoin(r.url, f.get("action") or ""), "method": (f.get("method") or "get").upper(),
                      "fields": fields[:30]})
    rec["forms"] = forms
    links = []
    for a in soup.find_all("a", href=True):
        txt = " ".join(a.get_text(" ").split())[:100]
        href = urljoin(r.url, a["href"])
        if INTERESTING.search(txt + " " + href):
            links.append({"text": txt, "href": href})
    rec["links"] = links[:200]
    tables = []
    for t in soup.find_all("table")[:8]:
        rows = t.find_all("tr")
        tables.append({"rows": len(rows), "sample": [[" ".join(c.get_text(" ").split())[:50] for c in tr.find_all(["th", "td"])][:14]
                                                      for tr in rows[:6]],
                       "row_links": [urljoin(r.url, a["href"]) for a in t.find_all("a", href=True)][:10]})
    rec["tables"] = tables
    # endpoints referenced from scripts (AJAX data sources)
    scripts = " ".join(t.get_text(" ") for t in soup.find_all("script") if not t.get("src"))
    rec["script_srcs"] = [urljoin(r.url, t["src"]) for t in soup.find_all("script", src=True)][:30]
    rec["script_endpoints"] = sorted(set(re.findall(r"""["']([\w./\-]+\.(?:php|json|aspx|do|jsp)[^"'\s]*)["']""", scripts)))[:60]
    snips = []
    for m in re.finditer(r"(\$\.(?:ajax|post|get)|fetch\(|url\s*:|DataTable\(|ajax\s*:)", scripts):
        snips.append(" ".join(scripts[max(0, m.start() - 150):m.start() + 450].split()))
    rec["script_snippets"] = snips[:12]
    full = " ".join(soup.get_text(" ").split())
    rec["text_start"] = full[:1500]
    for word in ("Terms of Use", "Copyright Policy", "Copyright", "Hyperlinking Policy"):
        i = full.find(word, 1500)
        if i > 0:
            rec.setdefault("policy_text", {})[word] = full[i:i + 2500]
    return rec


def main(urls: list[str]) -> None:
    if not urls:
        urls = [l.strip() for l in (DATA / "probe" / "targets.txt").read_text().splitlines()
                if l.strip() and not l.startswith("#")]
    out = {"checked_at": datetime.now().isoformat(timespec="seconds"), "results": []}
    for u in urls:
        print("probe", u, flush=True)
        out["results"].append(probe(u))
        time.sleep(3)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
