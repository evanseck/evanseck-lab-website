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


def pdf_lines(url):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(get(url, binary=True)))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    return [l for l in lines if l]


NAME = re.compile(r"^(?:\d{1,2}:\d{2}\s*[–-]\s*\d{1,2}:\d{2}\s+)?([A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]*){1,3})$")
DEPT = re.compile(r"School of|Department|College of|Program\b|Graduate School|^(Chemistry|Biology|Pharmacology|Physics)\b", re.I)
TIME = re.compile(r"^\d{1,2}:\d{2}")
SESSION = re.compile(r"\bSession\s+\w+", re.I)
AWARD = re.compile(r"Award|Excellence|Honorable|Prize|Outstanding|Place\b|Scholarship", re.I)


def is_name(l):
    return bool(NAME.match(l)) and len(l) <= 45 and not DEPT.search(l) and not AWARD.search(l) \
        and "Ph.D" not in l and not SESSION.search(l)


def is_other(l):
    return bool(DEPT.search(l) or TIME.match(l) or SESSION.search(l) or "Advisor" in l or ADVISOR in l
                or re.search(r"Moderator|Ballroom|Room\b|Presentation only|Poster\b", l, re.I))


CONNECT = re.compile(r"\b(with|and|of|the|for|to|in|on|a|an|by|from|at|into|via|as|its|their)$|[:;,–-]$", re.I)


def name_at(lines, j):
    """The presenter's name if line j starts an entry (a name, maybe after a time slot)."""
    m = NAME.match(lines[j])
    if not m or not is_name(lines[j]):
        return ""
    if j > 0 and CONNECT.search(lines[j - 1]):
        return ""                                   # the end of a title that wrapped onto this line
    if not any(DEPT.search(l) or ADVISOR in l or "Advisor" in l for l in lines[j + 1:j + 5]):
        return ""
    return m.group(1).strip()


def entry_bounds(lines, i):
    """(start, end) of the entry around advisor line i: from its presenter's name to the next name."""
    start = next((j for j in range(i - 1, max(-1, i - 10), -1) if name_at(lines, j)), None)
    if start is None:
        return None, None
    end = next((j for j in range(i + 1, min(len(lines), i + 10)) if name_at(lines, j)), min(len(lines), i + 8))
    return start, end


def title_in(lines, start, end):
    run = []
    for l in lines[start + 1:end]:
        if is_other(l):
            if run:
                break
            continue
        if len(l) > 140 or (l.endswith(".") and len(l) > 90):
            if run:
                break                                   # the abstract starts
            continue
        run.append(l)
        if len(run) == 3:
            break
    return re.sub(r"\s+", " ", " ".join(run)).strip()


def presentations(lines):
    out = {}
    session = ""
    for i, l in enumerate(lines):
        s = SESSION.search(l)
        if s and len(l) < 80:
            session = s.group(0)
        if ADVISOR not in l:
            continue
        start, end = entry_bounds(lines, i)
        if start is None:
            continue
        who = name_at(lines, start)
        title = title_in(lines, start, end)
        oral = bool(TIME.match(lines[start])) or any(re.search(r"Presentation only", x, re.I) for x in lines[start:end])
        entry = {"presenter": who, "title": title,
                 "format": f"Oral presentation · {session}" if oral and session else ("Oral presentation" if oral else "Poster"),
                 "context": " / ".join(lines[start:min(end, start + 6)])}
        old = out.get(who)
        if not old or (oral and "Oral" not in old["format"]) or (not old["title"] and title):
            if old and not title:
                entry["title"] = old["title"]
            out[who] = entry
    return list(out.values())


def awards(lines):
    out = []
    for i, l in enumerate(lines):
        if ADVISOR not in l:
            continue
        start, end = entry_bounds(lines, i)
        who = name_at(lines, start) if start is not None else ""
        prize = ""
        for j in list(range(i - 1, max(-1, i - 6), -1)) + list(range(i + 1, min(len(lines), i + 3))):
            if AWARD.search(lines[j]) and len(lines[j]) < 120:
                prize = lines[j]
                break
        if who:
            out.append({"presenter": who, "award": prize, "title": title_in(lines, start, end)})
    return out


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
                  "image": urllib.parse.urljoin(page_url, html.unescape(img.group(1))) if img else entry.get("image", ""),
                  "checked": datetime.date.today().isoformat()})
    if links.get("program"):
        try:
            lines = pdf_lines(links["program"])
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
