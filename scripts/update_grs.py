"""
Keep the group's Graduate Research Symposium (GRS) entries up to date, every year, on their own.

Runs weekly with the other updates (.github/workflows/site.yml), or by hand:
    python scripts/update_grs.py

What it does:
  * reads Duquesne's GRS page (settings: scripts/config.json, "grs") to find the current year's
    program booklet and award list (PDFs), the date, the event photo and the ForagerOne link;
  * finds every presentation advised by Dr. Evanseck (and any award one of them won);
  * saves each year in data/grs.json. Years are kept for good, so past symposia stay on the
    website after Duquesne's page moves on to the next year.

The site shows each year as a block under "Seminar reviews". If you write your own post for a
year (content/news/, title containing "Graduate Research Symposium"), yours is shown instead.
To take a year off the website, add it to "hide_years" in scripts/config.json.
Needs: pypdf (installed by the workflow).
"""
import datetime
import html
import io
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8")).get("grs", {})
OUT = ROOT / "data" / "grs.json"
UA = {"User-Agent": "evanseck-group-website (GitHub Action; yearly symposium check)"}
ADVISOR = CONFIG.get("advisor_surname", "Evanseck")
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def get(url, binary=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def text_of(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def pdf_lines(url, data=None):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data if data is not None else get(url, binary=True)))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    return [l for l in lines if l]


# Two layouts appear in Duquesne's GRS documents:
#   talks (program booklet):   9:00 - / 9:15 / "Name – presentation only" / "Chemistry | School of …" /
#                              "Faculty Advisor/s: …" / title (1-3 lines)
#   posters and award list:    title (1-3 lines, posters may start "Virtual:") / "Name │ Chemistry │ School of …" /
#                              "Faculty Advisor(s): …" / "Abstract: …"   (award lists put the award's name above)
SEP = re.compile(r"\s*[│|]\s*")
TIME = re.compile(r"^\d{1,2}:\d{2}\s*[–-]?\s*$|^\d{1,2}:\d{2}\s*[–-]\s*\d{1,2}:\d{2}")
SESSION = re.compile(r"\bSESSION\s+\w+", re.I)
SUFFIX = re.compile(r"\s*[–-]\s*(presentation only|poster( only)?|in[- ]person.*|virtual.*|live.*)$", re.I)
ADVISOR_LINE = re.compile(r"^Faculty\s+Advisor", re.I)
AWARD = re.compile(r"Award|Excellence|Honorable|Prize|Place\b|Scholarship|Fellowship", re.I)
PERSON = re.compile(r"^[A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]*){1,3}$")


def _stop_above(l):
    """A line that cannot be part of a poster title (so the title above a name line ends there)."""
    return (not l or l.endswith(".") or l.isupper() or ADVISOR_LINE.match(l) or l.lower().startswith("abstract")
            or len(SEP.split(l)) >= 3 or TIME.match(l) or SESSION.search(l))


def _entry(lines, i):
    """The entry whose advisor line is lines[i]: (presenter, title, format, award heading or '')."""
    j = i - 1
    parts = SEP.split(lines[j]) if j >= 0 else []
    if len(parts) >= 3 and PERSON.match(parts[0].strip()):
        # poster / award layout: the title is just above the name line
        name, title, k = parts[0].strip(), [], j - 1
        while k >= 0 and len(title) < 3 and not _stop_above(lines[k]):
            if AWARD.search(lines[k]) and len(lines[k]) < 70 and not title:
                break                                   # the award's name, not the title
            title.insert(0, lines[k])
            k -= 1
        heading = ""
        for m in range(k, max(-1, k - 6), -1):
            if AWARD.search(lines[m]) and len(lines[m]) < 90 and not lines[m].endswith("."):
                heading = lines[m]
                break
        t = re.sub(r"\s+", " ", " ".join(title)).strip()
        fmt = "Virtual poster" if t.lower().startswith("virtual:") else "Poster"
        return name, re.sub(r"^virtual:\s*", "", t, flags=re.I), fmt, heading
    # talk layout: a department line ("Chemistry | School of …"), the name above it, the title below the advisor
    k = j
    while k >= 0 and (len(SEP.split(lines[k])) == 2 or "School of" in lines[k]):
        k -= 1
    if k < 0:
        return "", "", "", ""
    name = SUFFIX.sub("", lines[k]).strip()
    if not PERSON.match(name):
        return "", "", "", ""
    title = []
    for l in lines[i + 1:i + 5]:
        if TIME.match(l) or ADVISOR_LINE.match(l) or len(SEP.split(l)) >= 2 or SESSION.search(l) or SUFFIX.search(l):
            break
        title.append(l)
    session = ""
    for m in range(k, -1, -1):
        s = SESSION.search(lines[m])
        if s:
            session = s.group(0).title().replace("Session", "Session")
            break
    fmt = f"Oral presentation · {session}" if session else "Oral presentation"
    return name, re.sub(r"\s+", " ", " ".join(title)).strip(), fmt, ""


def _group_entries(lines):
    for i, l in enumerate(lines):
        if ADVISOR in l and ADVISOR_LINE.match(l):
            name, title, fmt, heading = _entry(lines, i)
            if name:
                yield name, title, fmt, heading


def presentations(lines):
    """Every presentation advised by Dr. Evanseck. A student with both a talk and a poster is
    listed once, as a talk."""
    out = {}
    for name, title, fmt, _ in _group_entries(lines):
        old = out.get(name)
        if not old or ("Oral" in fmt and "Oral" not in old["format"]):
            out[name] = {"presenter": name, "title": title or (old or {}).get("title", ""), "format": fmt}
        elif not old["title"] and title:
            old["title"] = title
    return list(out.values())


def awards(lines):
    """Awards won by students advised by Dr. Evanseck (from the award-winners list)."""
    return [{"presenter": n, "award": h, "title": t} for n, t, _, h in _group_entries(lines)]


def cover_panel(im):
    """A booklet cover is often a coloured frame around a few panels (banner, text boxes).
    Split the cover at rows of frame colour and keep the panel with the most going on in it."""
    rgb = im.convert("RGB"); w, h = rgb.size; px = rgb.load()
    frame = px[1, 1]
    near = lambda c, ref, t=45: sum(abs(a - b) for a, b in zip(c, ref)) < t
    step = max(1, w // 250)
    xs = range(0, w, step)
    def frac(y, test):
        return sum(1 for x in xs if test(px[x, y])) / len(xs)
    frame_row = [frac(y, lambda c: near(c, frame)) > 0.85 for y in range(h)]
    panels, y = [], 0
    while y < h:
        if frame_row[y]:
            y += 1; continue
        y0 = y
        while y < h and not frame_row[y]:
            y += 1
        if y - y0 > h * 0.08:
            busy = sum(frac(r, lambda c: not (min(c) > 235 or near(c, frame))) for r in range(y0, y, 3)) / len(range(y0, y, 3))
            panels.append((busy, y0, y))
    if not panels:
        return im
    busy, y0, y1 = max(panels)
    if busy < 0.15:
        return im
    col_frame = [sum(1 for r in range(y0, y1, 4) if near(px[x, r], frame)) / len(range(y0, y1, 4)) > 0.85 for x in range(w)]
    inner = [x for x in range(w) if not col_frame[x]]
    x0, x1 = (inner[0], inner[-1] + 1) if inner else (0, w)
    return rgb.crop((x0, y0, x1, y1))


def save_symbol(data, year):
    """The symposium's symbol: the main picture on the program booklet's cover, saved as
    images/grs/<year>-logo.png (shown first in that year's block). If the cover has no separate
    picture, the cover's main panel (e.g. the banner inside a coloured frame) is cut out. A file you upload as images/news/grs-<year>-logo.png
    is used instead."""
    from pypdf import PdfReader
    from PIL import Image
    dst = ROOT / "images" / "grs" / f"{year}-logo.png"
    page = PdfReader(io.BytesIO(data)).pages[0]
    best = None
    for im in page.images:
        try:
            pic = Image.open(io.BytesIO(im.data))
        except Exception:
            continue
        w, h = pic.size
        if w < 150 or h < 150 or max(w, h) / min(w, h) > 3:
            continue                                   # thin rules, small icons
        from PIL import ImageFilter, ImageStat
        g = pic.convert("L").resize((200, max(1, int(200 * h / w))))
        if ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0] < 8 or ImageStat.Stat(g).stddev[0] < 20:
            continue                                   # plain backgrounds and colour gradients
        if not best or w * h > best.size[0] * best.size[1]:
            best = pic
    if best is None:
        try:
            import pypdfium2
            best = cover_panel(pypdfium2.PdfDocument(data)[0].render(scale=2).to_pil())
        except Exception as e:
            print(f"  no symbol found on the booklet cover ({e})")
            return ""
    best = best.convert("RGBA") if best.mode in ("RGBA", "LA", "P") else best.convert("RGB")
    best.thumbnail((1200, 1200))
    dst.parent.mkdir(parents=True, exist_ok=True)
    best.save(dst, "PNG", optimize=True)
    print(f"  GRS {year} symbol saved from the booklet cover: {dst.relative_to(ROOT)}")
    return str(dst.relative_to(ROOT))


def find_date(lines, year):
    m = re.search(rf"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?,?\s*({MONTHS})\s+(\d{{1,2}}),?\s+({year})", " ".join(lines[:80]))
    if m:
        return datetime.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
    return ""


def main():
    page_url = CONFIG.get("page", "https://www.duq.edu/research/graduate-research/graduate-symposium.php")
    state = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"years": {}}
    page = get(page_url)
    m = re.search(r"(20\d\d)\s+Graduate Research Symposium", text_of(page))
    if not m:
        print("No year found on the GRS page; nothing to do.")
        return
    year = m.group(1)
    links = {}
    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        u, t = urllib.parse.urljoin(page_url, html.unescape(href)), text_of(label).lower()
        if u.lower().endswith(".pdf"):
            if ("booklet" in u.lower() or "program" in t) and "program" not in links:
                links["program"] = u
            elif "award" in u.lower() or "winner" in t or "award" in t:
                if "winner" in u.lower() or "winner" in t or "final" in u.lower():
                    links["awards"] = u
            elif "schedule" in u.lower() or "schedule" in t:
                links["schedule"] = u
        elif "foragerone" in u and year in u:
            links["forager"] = u
    img = re.search(r'<img[^>]+src="([^"]*(?:grs|symposium)[^"]*\.(?:jpe?g|png))"', page, re.I)
    entry = state["years"].get(year, {})
    entry.update({"year": year, "page": page_url, "links": links,
                  
                  "checked": datetime.date.today().isoformat()})
    if links.get("program"):
        try:
            booklet = get(links["program"], binary=True)
            lines = pdf_lines(links["program"], booklet)
            if not (ROOT / "images" / "grs" / f"{year}-logo.png").exists():
                try:
                    save_symbol(booklet, year)
                except Exception as e:
                    print(f"  could not take the symbol from the booklet ({e})")
            entry["date"] = find_date(lines, year) or entry.get("date", "")
            pres = presentations(lines)
            if pres:
                entry["presentations"] = pres
            print(f"GRS {year}: {len(pres)} presentation(s) advised by Dr. {ADVISOR}")
        except Exception as e:
            print(f"  could not read the program booklet ({e})")
        time.sleep(1)
    if links.get("awards"):
        try:
            won = awards(pdf_lines(links["awards"]))
            entry["awards"] = won
            print(f"GRS {year}: {len(won)} award(s) to the group")
        except Exception as e:
            print(f"  could not read the award list ({e})")
    if not entry.get("date"):
        d = re.search(rf"({MONTHS})\s+(\d{{1,2}})", text_of(page))
        if d:
            entry["date"] = datetime.datetime.strptime(f"{d.group(1)} {d.group(2)} {year}", "%B %d %Y").date().isoformat()
    state["years"][year] = entry
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the site build over this
        print(f"::warning::GRS check skipped: {e}")
        sys.exit(0)
