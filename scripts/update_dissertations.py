"""
Find dissertations chaired by Dr. Evanseck in Duquesne's Scholarship Collection and save them to
data/dissertations.json. The site links each past member's thesis to the record found here
(unless their file already has a thesis_url).

Runs weekly with the publication update (.github/workflows/site.yml), or by hand:
    python scripts/update_dissertations.py

How it works, politely:
  * reads the archive's public sitemap (https://dsc.duq.edu/etd/sitemap.xml);
  * opens only dissertation pages it has not checked before (or that changed), one at a time,
    with a pause between requests, and only pages the site's robots.txt allows;
  * remembers what it checked, so after the first run only new dissertations are read.
Uses only the Python standard library. Settings are in scripts/config.json ("dissertations").
"""
import datetime
import html
import json
import pathlib
import re
import sys
import time
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8")).get("dissertations", {})
OUT = ROOT / "data" / "dissertations.json"

BASE = CONFIG.get("base_url", "https://dsc.duq.edu")
SITEMAPS = CONFIG.get("sitemaps", [BASE + "/etd/sitemap.xml"])
ADVISOR = CONFIG.get("advisor_surname", "Evanseck").lower()
DELAY = float(CONFIG.get("seconds_between_requests", 1.0))
MAX_PAGES = int(CONFIG.get("max_pages_per_run", 4000))
UA = "evanseck-group-website (GitHub Action; checks dissertation pages weekly)"

robots = urllib.robotparser.RobotFileParser(BASE + "/robots.txt")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def text_of(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def meta(page, name):
    m = re.search(r'<meta[^>]+name=["\']' + re.escape(name) + r'["\'][^>]*content=["\']([^"\']*)', page, re.I) \
        or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*name=["\']' + re.escape(name) + r'["\']', page, re.I)
    return html.unescape(m.group(1)).strip() if m else ""


def parse(page, url):
    """Pull title, author, year, degree and committee chair(s) from a dissertation page."""
    title = ""
    m = re.search(r'<div[^>]+id=["\']title["\'][^>]*>(.*?)</div>', page, re.S | re.I)
    if m:
        title = text_of(m.group(1))
    if not title:
        m = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
        if m:
            title = re.sub(r'^"|"\s+by\s+.*$', "", text_of(m.group(1))).strip('"')
    if not title:
        title = meta(page, "bepress_citation_title")
    chairs = []
    # bepress pages list advisors as <div id="advisor1"><h4>Committee Chair</h4><p>Name</p></div>
    for m in re.finditer(r'<div[^>]+id=["\']advisor\d*["\'][^>]*>(.*?)</div>', page, re.S | re.I):
        block = m.group(1)
        label = text_of((re.search(r"<h\d[^>]*>(.*?)</h\d>", block, re.S | re.I) or [None, ""])[1])
        name = text_of(re.sub(r"<h\d[^>]*>.*?</h\d>", "", block, flags=re.S | re.I))
        if name:
            chairs.append({"role": label or "Advisor", "name": name})
    if not chairs:  # fallback: any "Committee Chair" heading followed by a name
        for m in re.finditer(r"Committee (?:Co-)?Chair\s*</h\d>\s*<p[^>]*>(.*?)</p>", page, re.S | re.I):
            chairs.append({"role": "Committee Chair", "name": text_of(m.group(1))})
    return {
        "url": url.rstrip("/"),
        "title": title,
        "author": meta(page, "bepress_citation_author"),
        "year": meta(page, "bepress_citation_date")[:4],
        "degree": meta(page, "bepress_citation_dissertation_name"),
        "pdf": meta(page, "bepress_citation_pdf_url"),
        "committee": chairs,
    }


def main():
    state = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"checked": {}, "dissertations": []}
    checked = state.get("checked", {})
    found = {d["url"]: d for d in state.get("dissertations", [])}

    robots.read()
    entries = []
    for sm in SITEMAPS:
        try:
            root = ET.fromstring(get(sm))
        except Exception as e:
            print(f"::warning::Could not read {sm} ({e}); keeping the existing list.")
            continue
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for u in root.findall("s:url", ns):
            loc = (u.findtext("s:loc", default="", namespaces=ns) or "").strip()
            lastmod = (u.findtext("s:lastmod", default="", namespaces=ns) or "").strip()
            if re.search(r"/\d+/?$", loc):
                entries.append((loc, lastmod))
    todo = [(loc, mod) for loc, mod in entries if checked.get(loc) != mod]
    print(f"{len(entries)} dissertation pages in the sitemap; {len(todo)} new or changed since last run.")
    if len(todo) > MAX_PAGES:
        print(f"Checking the first {MAX_PAGES} this run; the rest next time.")
        todo = todo[:MAX_PAGES]

    new = 0
    for i, (loc, mod) in enumerate(todo, 1):
        if not robots.can_fetch(UA, loc):
            checked[loc] = mod
            continue
        try:
            rec = parse(get(loc), loc)
        except Exception as e:
            print(f"  skipped {loc}: {e}")
            continue
        checked[loc] = mod
        if any(ADVISOR in c["name"].lower() for c in rec["committee"]):
            if rec["url"] not in found:
                new += 1
                print(f"  found: {rec['author']} ({rec['year']}) {rec['title'][:70]}")
            found[rec["url"]] = rec
        if i % 200 == 0:
            print(f"  ...{i}/{len(todo)} checked")
        time.sleep(DELAY)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "source": SITEMAPS,
        "updated": datetime.date.today().isoformat(),
        "dissertations": sorted(found.values(), key=lambda d: (d.get("year", ""), d.get("author", "")), reverse=True),
        "checked": checked,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{new} new; {len(found)} dissertations with Dr. Evanseck on the committee in total.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the site build over this
        print(f"::warning::Dissertation update skipped: {e}")
        sys.exit(0)
