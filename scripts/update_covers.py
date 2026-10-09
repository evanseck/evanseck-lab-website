"""
Find journal covers featuring the group's papers and save them for the website.

Runs weekly after the publication update (.github/workflows/site.yml), or by hand:
    python scripts/update_covers.py

Two sources:
  1. Cover records that publishers register like papers ("Front Cover: ...", "Cover Picture: ...").
     The publication update collects these in data/publications.json ("cover_items").
  2. ACS article pages (DOIs starting 10.1021/). Each paper's page is read once (recent papers
     are re-read monthly for a year) and searched for wording such as "featured on the cover"
     or "supplementary cover". If found, the cover image is taken from the page or its issue.

Cover images are saved in images/covers/auto/ and listed in data/covers.json. To remove a
wrong one, add the paper's DOI to "covers" -> "hide" in scripts/config.json.
Uses only the Python standard library.
"""
import datetime
import hashlib
import html
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8")).get("covers", {})
PUBS = ROOT / "data" / "publications.json"
OUT = ROOT / "data" / "covers.json"
IMG_DIR = ROOT / "images" / "covers" / "auto"
DELAY = float(CONFIG.get("seconds_between_requests", 2.0))
UA = {"User-Agent": "Mozilla/5.0 (compatible; evanseck-group-website weekly cover check)",
      "Accept": "text/html,application/xhtml+xml,image/*;q=0.9,*/*;q=0.8"}

INDICATOR = re.compile(
    r"(featured|highlighted|selected|chosen|appears?)\s+(on|for)\s+the\s+(front |back |inside |supplementary |journal |issue )*cover"
    r"|supplementary\s+(journal\s+)?cover"
    r"|\bcover\s+art\b"
    r"|\b(front|back|inside)\s+cover\b", re.I)


class Blocked(Exception):
    pass


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            return (data, r.geturl()) if binary else (data.decode("utf-8", "replace"), r.geturl())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise Blocked(f"{e.code} from {urllib.parse.urlsplit(url).netloc}")
        raise


def doi_of(d):
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", str(d or ""), flags=re.I).strip().lower()


def title_key(t):
    t = re.sub(r"^\s*[^:]*cover[^:]*:\s*", "", str(t), flags=re.I)  # drop "Front Cover:" prefix
    return re.sub(r"[^a-z0-9]", "", t.lower())


def find_image(page, base):
    """Best cover-looking image on a page."""
    imgs = re.findall(r'<img[^>]+>', page, re.I)
    scored = []
    for tag in imgs:
        src = re.search(r'\s(?:data-)?src="([^"]+)"', tag)
        if not src:
            continue
        s = src.group(1)
        alt = (re.search(r'\salt="([^"]*)"', tag) or [None, ""])[1]
        text = (s + " " + alt).lower()
        if "cover" not in text:
            continue
        score = 3 if "supplementary" in text or "cover art" in text else 2 if "largecover" in text else 1
        scored.append((score, urllib.parse.urljoin(base, html.unescape(s))))
    if scored:
        return max(scored)[1]
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page, re.I)
    return urllib.parse.urljoin(base, html.unescape(m.group(1))) if m else ""


def save_image(url, key):
    data, _ = fetch(url, binary=True)
    if len(data) < 2000:
        raise ValueError("image too small")
    ext = ".png" if data[:4] == b"\x89PNG" else ".gif" if data[:3] == b"GIF" else ".jpg"
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:60] or hashlib.md5(url.encode()).hexdigest()[:10]
    path = IMG_DIR / (name + ext)
    path.write_bytes(data)
    return str(path.relative_to(ROOT))


def main():
    pubs_data = json.loads(PUBS.read_text(encoding="utf-8")) if PUBS.exists() else {}
    papers = pubs_data.get("publications", [])
    items = pubs_data.get("cover_items", [])
    state = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    covers = {c["doi"]: c for c in state.get("covers", [])}
    checked = state.get("checked", {})
    hide = {doi_of(d) for d in CONFIG.get("hide", [])}
    today = datetime.date.today()
    by_title = {title_key(p["title"]): p for p in papers}

    # 1. Publisher cover records
    for it in items:
        key = doi_of(it.get("doi")) or title_key(it["title"])
        if key in checked:
            continue
        article = by_title.get(title_key(it["title"]))
        target = doi_of(article["doi"]) if article else doi_of(it.get("doi"))
        try:
            if it.get("doi"):
                page, final = fetch(it["doi"])
                img = find_image(page, final)
                if img:
                    covers[target] = {"doi": target, "image": save_image(img, target),
                                      "label": f'{it.get("venue", "")}, {it.get("year", "")}'.strip(", "),
                                      "kind": re.split(r":", it["title"])[0].strip(), "source": it["doi"]}
                    print(f"  cover record: {it['title'][:70]}")
            checked[key] = today.isoformat()
        except Blocked as e:
            print(f"  Journal site not allowing automated reading right now ({e}); will try again next week.")
            break
        except Exception as e:
            print(f"  could not read {it.get('doi')}: {e}")
        time.sleep(DELAY)

    # 2. ACS article pages
    if CONFIG.get("check_acs_pages", True):
        for p in papers:
            d = doi_of(p.get("doi"))
            if not d.startswith("10.1021/") or d in covers:
                continue
            last = checked.get(d)
            pub_date = datetime.date.fromisoformat(str(p.get("date", f"{p['year']}-01-01"))[:10])
            recent = (today - pub_date).days < 365
            if last and not (recent and (today - datetime.date.fromisoformat(last)).days > 30):
                continue
            try:
                page, final = fetch("https://pubs.acs.org/doi/" + d)
                checked[d] = today.isoformat()
                if INDICATOR.search(re.sub(r"<[^>]+>", " ", page)):
                    img = find_image(page, final)
                    issue = re.search(r'href="(/toc/[a-z]+/\d+/\d+)"', page)
                    if (not img or "supplementary" not in img.lower()) and issue:
                        time.sleep(DELAY)
                        ipage, ifinal = fetch(urllib.parse.urljoin(final, issue.group(1)))
                        img = find_image(ipage, ifinal) or img
                    if img:
                        covers[d] = {"doi": d, "image": save_image(img, d),
                                     "label": f'{p.get("venue", "")}, {p.get("year", "")}'.strip(", "),
                                     "kind": "Cover", "source": "https://doi.org/" + d}
                        print(f"  ACS cover: {p['title'][:70]}")
            except Blocked as e:
                print(f"  ACS not allowing automated reading right now ({e}); will try again next week.")
                break
            except Exception as e:
                print(f"  could not read {d}: {e}")
            time.sleep(DELAY)

    for d in hide:
        covers.pop(d, None)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"updated": today.isoformat(),
                               "covers": sorted(covers.values(), key=lambda c: c["doi"]),
                               "checked": checked}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(covers)} journal cover(s) on file.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the site build over this
        print(f"::warning::Cover check skipped: {e}")
        sys.exit(0)
