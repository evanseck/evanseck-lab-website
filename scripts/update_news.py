"""
Find Duquesne University stories that mention Dr. Evanseck or a current group member, and save
them to data/duquesne-news.json. The site shows them on the News tab (as accomplishments).

Runs weekly with the other updates (.github/workflows/site.yml), or by hand:
    python scripts/update_news.py

Where it looks (settings in scripts/config.json, "duquesne_news"):
  * News pages on www.duq.edu, found through the site's public sitemap (University news,
    School of Science and Engineering news, grants received, ...).
  * Issues of the Duquesne University Times (the faculty and staff newsletter), linked from
    its archive page. These list faculty presentations, publications, grants and awards.

How it works, politely:
  * only pages the sites' robots.txt allows, one at a time, with a pause between requests;
  * each story is read once and remembered, so after the first run only new pages are read
    (listing pages such as "grants received" are re-read monthly).

Names searched for: "Evanseck", plus the full name of each current member in content/people/.
To hide a story, add its address (or a few words from it) to "hide" in scripts/config.json.
Uses only the Python standard library (plus PyYAML, which the site build already installs).
"""
import datetime
import html
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8")).get("duquesne_news", {})
OUT = ROOT / "data" / "duquesne-news.json"
UA = "evanseck-group-website (GitHub Action; weekly check for group news)"
DELAY = float(CONFIG.get("seconds_between_requests", 1.0))
MAX_PAGES = int(CONFIG.get("max_pages_per_run", 600))
MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December|"
          "Jan\\.?|Feb\\.?|Mar\\.?|Apr\\.?|Jun\\.?|Jul\\.?|Aug\\.?|Sept?\\.?|Oct\\.?|Nov\\.?|Dec\\.?")
DATE_RE = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),?\s+(20\d\d)\b")

_robots = {}


def allowed(url):
    host = urllib.parse.urlsplit(url)
    base = f"{host.scheme}://{host.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None          # no robots.txt reachable: treat as allowed
        _robots[base] = rp
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(UA, url)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(4 * (attempt + 1))


def text_of(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_date(s):
    m = DATE_RE.search(s or "")
    if not m:
        return None
    mon = m.group(1).rstrip(".")[:3].title()
    try:
        return datetime.datetime.strptime(f"{mon} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
    except ValueError:
        return None


def member_names():
    """Full names of current members (content/people/*.md), plus the PI's surname."""
    names = list(CONFIG.get("names", ["Evanseck"]))
    for f in sorted((ROOT / "content" / "people").glob("*.md")):
        if f.name.startswith("_"):
            continue
        m = re.match(r"^---\s*\n(.*?)\n---", f.read_text(encoding="utf-8"), re.S)
        try:
            name = (yaml.safe_load(m.group(1)) or {}).get("name", "") if m else ""
        except yaml.YAMLError:
            continue
        name = re.sub(r",.*$", "", str(name)).strip()            # "Jeffrey D. Evanseck, PhD" -> "Jeffrey D. Evanseck"
        parts = name.split()
        if len(parts) >= 2:
            names.append(f"{parts[0]} {parts[-1]}")               # first + last, ignores middle initials
    return sorted(set(names), key=str.lower)


def name_pattern(names):
    alts = []
    for n in names:
        parts = n.split()
        if len(parts) == 1:
            alts.append(re.escape(parts[0]))
        else:   # allow a middle name or initial between first and last
            alts.append(re.escape(parts[0]) + r"(?:\s+[A-Z][\w.'-]*)?\s+" + re.escape(parts[-1]))
    return re.compile(r"\b(" + "|".join(alts) + r")\b", re.I)


def blocks(page, min_len=26):
    """Readable text blocks (paragraphs, list items, table cells) of a page's main content."""
    page = re.sub(r"<(script|style|noscript|header|footer|nav)\b.*?</\1>", " ", page, flags=re.S | re.I)
    m = re.search(r"<main\b.*?</main>", page, re.S | re.I)
    body = m.group(0) if m else page
    parts = re.split(r"</(?:p|li|td|h[1-6]|div)>|<br\s*/?>", body, flags=re.I)
    return [t for t in (text_of(p) for p in parts) if len(t) >= min_len]


def title_of(page):
    for pat in (r"<h1[^>]*>(.*?)</h1>", r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', r"<title>(.*?)</title>"):
        m = re.search(pat, page, re.S | re.I)
        if m and text_of(m.group(1)):
            return re.sub(r"\s*\|\s*Duquesne University.*$", "", text_of(m.group(1)))
    return ""


def photo_of(page, base, near=None):
    """The story's main photo: the image next to the paragraph that mentions us (newsletters),
    else the page's share image (og:image), else the first sizeable image in the story."""
    skip = re.compile(r"logo|icon|sprite|seal|badge|spacer|pixel|social|facebook|twitter|instagram|linkedin|youtube", re.I)

    def ok(src):
        return src and not src.startswith("data:") and not skip.search(src)

    if near:   # newsletter: last image before the paragraph, within the same story block
        words = re.findall(r"\w+", near)[:6]
        loc = re.search(r"(?:\W|<[^>]+>|&\w+;)+".join(map(re.escape, words)), page) if words else None
        i = loc.start() if loc else -1
        if i > 0:
            imgs = [m for m in re.finditer(r'<img[^>]+src="([^"]+)"', page[max(0, i - 4000):i], re.I) if ok(m.group(1))]
            if imgs:
                return urllib.parse.urljoin(base, html.unescape(imgs[-1].group(1)))
        return ""
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page, re.I) or \
        re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', page, re.I)
    if m and ok(m.group(1)):
        return urllib.parse.urljoin(base, html.unescape(m.group(1)))
    main = re.search(r"<main\b.*?</main>", page, re.S | re.I)
    for m in re.finditer(r'<img[^>]+src="([^"]+)"[^>]*>', main.group(0) if main else page, re.I):
        w = re.search(r'width="(\d+)"', m.group(0))
        if ok(m.group(1)) and not (w and int(w.group(1)) < 200):
            return urllib.parse.urljoin(base, html.unescape(m.group(1)))
    return ""


def page_date(page):
    for pat in (r'<meta[^>]+(?:property|name)="(?:article:published_time|date|dcterms.date)"[^>]+content="(\d{4}-\d\d-\d\d)',
                r'<time[^>]+datetime="(\d{4}-\d\d-\d\d)'):
        m = re.search(pat, page, re.I)
        if m:
            return datetime.date.fromisoformat(m.group(1))
    m = re.search(r"<h1\b.*", page, re.S | re.I)          # first written date after the headline
    return parse_date(text_of((m.group(0) if m else page)[:6000]))


def find_mentions(page, pattern):
    hits = []
    for b in blocks(page):
        if pattern.search(b) and b not in hits:
            hits.append(b)
    return hits


def short(s, n):
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def story_items(page, url, pattern, since, today, issue_date, source):
    """A news story (one item) or a newsletter issue (one item per paragraph about us)."""
    hits = find_mentions(page, pattern)
    if not hits:
        return []
    title = title_of(page)
    date = issue_date or page_date(page)
    if date and date < since:
        return []
    if "science-and-engineering" in url:
        source = "School of Science and Engineering"
    # A newsletter mentions many people: keep each paragraph about us as its own item.
    # A news story is about one thing: keep it whole, with the paragraphs that mention us.
    groups = [[h] for h in hits] if issue_date else [hits]
    out = []
    for g in groups:
        out.append({
            "url": url, "key": item_key(g[0]) if issue_date else "", "source": source,
            "title": short(g[0], 110) if issue_date else (title or short(g[0], 110)),
            "date": (date or today).isoformat(), "dated": bool(date), "precision": "day",
            "excerpt": short(" ".join(g), 600),
            "names": sorted({m.group(0) for h in g for m in pattern.finditer(h)}),
            "image": photo_of(page, url, g[0] if issue_date else None),
        })
    return out


def item_key(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())[:40]


MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
               "september", "october", "november", "december"]


def listing_items(page, url, pattern, since, today):
    """Pages that list many entries under year and month headings (e.g. Grants Received):
    one item per entry about us, dated by the headings above it."""
    year = month = None
    out = []
    for b in blocks(page, min_len=4):
        low = b.lower().strip(" :")
        y = re.fullmatch(r"(?:fy\s*)?(20\d\d)", low)
        mm = re.fullmatch(r"(%s)(?:,?\s+(20\d\d))?" % "|".join(MONTH_NAMES), low)
        mm2 = re.fullmatch(r"(20\d\d),?\s+(%s)" % "|".join(MONTH_NAMES), low)
        if y:
            year, month = int(y.group(1)), None
            continue
        if mm:
            month = MONTH_NAMES.index(mm.group(1)) + 1
            year = int(mm.group(2)) if mm.group(2) else year
            continue
        if mm2:
            year, month = int(mm2.group(1)), MONTH_NAMES.index(mm2.group(2)) + 1
            continue
        if not pattern.search(b) or not year:
            continue
        date = datetime.date(year, month or 1, 1)
        if date < since.replace(day=1):
            continue
        amount = re.search(r"\$[\d,]+(?:\.\d+)?", b)
        who = re.split(r",\s*(?:Department|School|College|Center)\b", b)[0].strip()
        title = f"{amount.group(0)} grant to {who}" if amount and len(who) < 120 else short(b, 110)
        out.append({
            "url": url, "key": item_key(b), "source": "Duquesne University, Grants Received"
            if "grants" in url else "Duquesne University",
            "title": title, "date": date.isoformat(), "dated": True,
            "precision": "month" if month else "year",
            "excerpt": short(b, 600),
            "names": sorted({m.group(0) for m in pattern.finditer(b)}),
            "image": "",
        })
    return out


def sitemap_urls():
    out = []
    for sm in CONFIG.get("sitemaps", ["https://www.duq.edu/sitemap.xml"]):
        try:
            root = ET.fromstring(get(sm))
        except Exception as e:
            print(f"  could not read {sm} ({e})")
            continue
        ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        locs = [(l.text or "").strip() for l in root.iter(ns + "loc")]
        if root.tag.endswith("sitemapindex"):          # an index of sitemaps: read each one
            for child in locs:
                try:
                    locs_child = ET.fromstring(get(child))
                    out += [(l.text or "").strip() for l in locs_child.iter(ns + "loc")]
                except Exception as e:
                    print(f"  could not read {child} ({e})")
        else:
            out += locs
    wanted = CONFIG.get("url_contains", [])
    return sorted({u for u in out if any(w in u for w in wanted)} | set(CONFIG.get("always_read", [])))


def times_issues():
    """(date, url) for each Duquesne Times issue linked from its archive page."""
    url = CONFIG.get("times_archive")
    if not url:
        return []
    try:
        page = get(url)
    except Exception as e:
        print(f"  could not read the Times archive ({e})")
        return []
    out = []
    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        d = parse_date(text_of(label))
        if d and ("createsend" in href or "/the-times/" in href):
            out.append((d, html.unescape(href)))
    return out


def main():
    state = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    items = {i["url"] + "#" + i.get("key", ""): i for i in state.get("items", [])}
    checked = state.get("checked", {})
    today = datetime.date.today()
    names = member_names()
    pattern = name_pattern(names)
    recheck = CONFIG.get("recheck_monthly", [])
    years = int(CONFIG.get("years", 3))
    since = today - datetime.timedelta(days=round(365.25 * years))
    print(f"Keeping stories from the last {years} years (since {since.isoformat()}).")
    print("Looking for: " + ", ".join(names))

    todo = []
    for u in sitemap_urls():
        last = checked.get(u)
        monthly = any(r in u for r in recheck)
        if not last or (monthly and (today - datetime.date.fromisoformat(last)).days > 30):
            todo.append((u, None, "Duquesne University"))
    for d, u in times_issues():
        if d >= since and u not in checked:          # older newsletter issues are not read at all
            todo.append((u, d, "Duquesne University Times"))
    print(f"{len(todo)} page(s) to read this run.")
    if len(todo) > MAX_PAGES:
        print(f"Reading the first {MAX_PAGES}; the rest next week.")
        todo = todo[:MAX_PAGES]

    new = 0
    for url, issue_date, source in todo:
        if not allowed(url):
            checked[url] = today.isoformat()
            continue
        try:
            page = get(url)
        except Exception as e:
            print(f"  skipped {url}: {e}")
            continue
        checked[url] = today.isoformat()
        if any(l in url for l in CONFIG.get("listing_pages", [])):
            found = listing_items(page, url, pattern, since, today)
        else:
            found = story_items(page, url, pattern, since, today, issue_date, source)
        for item in found:
            k = item["url"] + "#" + item["key"]
            if k not in items:
                new += 1
                print(f"  found: {item['title'][:80]}  ({url})")
            items[k] = item
        time.sleep(DELAY)

    hide = [h.lower() for h in CONFIG.get("hide", [])]
    kept = [i for i in items.values() if i["date"] >= since.isoformat()
            and not any(h in i["url"].lower() or h in (i["title"] + " " + i["excerpt"]).lower() for h in hide)]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "updated": today.isoformat(),
        "names": names,
        "items": sorted(kept, key=lambda i: i["date"], reverse=True),
        "checked": checked,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{new} new; {len(kept)} Duquesne stories mention the group.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the site build over this
        print(f"::warning::Duquesne news check skipped: {e}")
        sys.exit(0)
