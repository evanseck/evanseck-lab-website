"""
Find the group's presentations at ACS regional meetings and add each meeting to "Seminar reviews".

Runs every day with the other updates (.github/workflows/site.yml), or by hand:
    python scripts/update_acs_meetings.py
    python scripts/update_acs_meetings.py path/to/program.pdf      (read one program file directly)

What it does:
  * looks for this year's and next year's ACS regional meeting websites: the ones listed in
    scripts/config.json ("acs_meetings" -> "sites"), plus the usual addresses such as
    acscerm2026.org for every regional meeting;
  * on each website, finds the meeting program (a PDF whose link mentions "program");
  * reads the program and keeps every presentation given by a group member. Programs made by
    ACS's abstract system list talks as "[CE300] Title. Presented by: Name", without affiliations,
    so members are recognized by name: everyone on the People page, plus "members" in the settings;
  * saves each meeting in data/acs_meetings.json. Meetings are kept for good; programs are re-read
    until a month after the meeting, in case a corrected version is posted.

The site shows each meeting as a block under "Seminar reviews". A post you write yourself for the
same meeting (content/news/, with the meeting's short name, e.g. "CERM 2026", in its title) is
shown instead. To take a meeting off the website, add its short name to "hide".
Photos named images/news/<short name>-1.jpg (e.g. cerm-2026-1.jpg) are shown with it. The meeting's
logo is taken from its website (images/acs/<short name>-logo.png); to use another, upload
images/news/<short name>-logo.png.
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
CONFIG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8")).get("acs_meetings", {})
OUT = ROOT / "data" / "acs_meetings.json"
UA = {"User-Agent": "evanseck-group-website (GitHub Action; checks ACS meeting programs)"}
MONTHS = "January February March April May June July August September October November December".split()
REGIONALS = {"cerm": "Central", "glrm": "Great Lakes", "marm": "Middle Atlantic", "mwrm": "Midwest", "nerm": "Northeast",
             "norm": "Northwest", "rmrm": "Rocky Mountain", "serm": "Southeast", "swrm": "Southwest", "wrm": "Western"}


def get(url, binary=False, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        final = r.geturl()
    return (data if binary else data.decode("utf-8", "replace")), final


# ------------------------------------------------------------------ who is in the group
def norm(name):
    name = re.sub(r",.*$", "", name)                      # ", PhD"
    name = re.sub(r"\b(Dr|Prof)\.?\s+", "", name, flags=re.I)
    words = [w for w in re.findall(r"[A-Za-z'’-]+", name) if len(w) > 1]
    return (words[0].lower(), words[-1].lower()) if len(words) >= 2 else None


def members():
    names = list(CONFIG.get("members", []))
    for f in (ROOT / "content" / "people").glob("*.md"):
        if f.name.startswith("_"):
            continue
        m = re.search(r"^name:\s*(.+)$", f.read_text(encoding="utf-8"), re.M)
        if m and "your name" not in m.group(1).lower():
            names.append(m.group(1).strip().strip("\"'"))
    return {norm(n): n for n in names if norm(n)}


# ------------------------------------------------------------------ reading a program
def program_lines(pdf_bytes):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    raw = []
    for p in reader.pages:
        raw += [re.sub(r"\s+", " ", l).strip() for l in (p.extract_text() or "").splitlines()]
    raw = [l for l in raw if l]
    counts = {}
    for l in raw:
        counts[l] = counts.get(l, 0) + 1
    # page headers and footers: lines repeated on most pages (but not "Session type: Oral" and the
    # like), and "… | 12". The header usually holds the meeting's short name, e.g. "CERM 2026".
    many = max(6, len(reader.pages) // 2)
    short = next((l for l in raw[:3] if re.match(r"^[A-Z]{3,5}\s*20\d\d$", l)), "")
    lines = [l for l in raw if (counts[l] < many or ":" in l) and not re.search(r"\|\s*\d+\s*$", l)]
    return lines, short


DAY = re.compile(rf"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+({'|'.join(MONTHS)})\s+(\d{{1,2}}),\s+(\d{{4}})$")
ENTRY = re.compile(r"^(\d{1,2}:\d{2}\s*[AP]M)\s+\[([A-Z]{1,5}\d+)\]\s*(.*)$")


def meeting_info(lines):
    """Short name, full name, dates and place from the first lines of the program."""
    head = " ".join(lines[:15])
    info = {}
    m = re.search(r"\b(20\d\d)\s+([A-Z][A-Za-z ]*?Regional Meeting)", head) or re.search(r"([A-Z][A-Za-z ]*?Regional Meeting)[^0-9]*(20\d\d)", head)
    if m:
        year, name = (m.group(1), m.group(2)) if m.group(1).isdigit() else (m.group(2), m.group(1))
        info["name"] = f"ACS {name.strip()} {year}"
        info["year"] = year
    m = re.search(rf"({'|'.join(MONTHS)})\s+(\d{{1,2}})\s*[-–]\s*(?:({'|'.join(MONTHS)})\s+)?(\d{{1,2}}),\s*(20\d\d)", head)
    if m:
        mo1 = MONTHS.index(m.group(1)) + 1
        mo2 = MONTHS.index(m.group(3)) + 1 if m.group(3) else mo1
        info["start"] = datetime.date(int(m.group(5)), mo1, int(m.group(2))).isoformat()
        info["end"] = datetime.date(int(m.group(5)), mo2, int(m.group(4))).isoformat()
        info["dates"] = f"{m.group(1)} {m.group(2)}–{(m.group(3) + ' ') if m.group(3) else ''}{m.group(4)}, {m.group(5)}"
        info.setdefault("year", m.group(5))
    m = re.search(r"\b([A-Z][a-zA-Z .]+,\s*[A-Z]{2})\b", head)
    if m:
        info["location"] = m.group(1).strip()
    return info


def presentations(lines, people):
    """Every presentation in the program given by a group member."""
    out, day, session, stype, i = [], "", "", "", 0
    while i < len(lines):
        l = lines[i]
        d = DAY.match(l)
        if d:
            day = f"{d.group(1)}, {d.group(2)} {d.group(3)}"
        if l.startswith("Session type:"):
            stype = l.split(":", 1)[1].strip()
            session = lines[i - 1] if i else ""
        e = ENTRY.match(l)
        if not e:
            i += 1
            continue
        when, code, text = e.groups()
        j = i + 1
        while "Presented by:" not in text and j < len(lines) and not ENTRY.match(lines[j]) and j < i + 6:
            text += " " + lines[j]
            j += 1
        if "Presented by:" in text and not text.split("Presented by:", 1)[1].strip() and j < len(lines):
            text += " " + lines[j]
            j += 1
        if "Presented by:" in text:
            title, who = text.split("Presented by:", 1)
            who = who.strip()
            # a name broken over two lines: "Presented by: Kyra" / "Krachinski"
            if len(who.split()) == 1 and j < len(lines) and re.match(r"^[A-Z][\w'’-]+$", lines[j]):
                who += " " + lines[j]
                j += 1
            key = norm(who)
            if key in people:
                title = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s+", " ", title)).strip().rstrip(".")
                title = re.sub(r"(\w) (\d)\+", r"\1\2+", title)        # "Mg 2+" -> "Mg2+"
                out.append({"presenter": people[key], "title": title, "code": code,
                            "format": ("Poster" if "poster" in stype.lower() else "Talk") + (f" · {session}" if session and "poster" not in stype.lower() else ""),
                            "when": f"{day}, {when}" if day else when})
        i = max(j, i + 1)
    return out


# ------------------------------------------------------------------ finding programs
def candidate_sites(today):
    sites = list(CONFIG.get("sites", []))
    for year in (today.year, today.year + 1):
        for abbr in REGIONALS:
            sites += [f"https://www.acs{abbr}{year}.org/", f"https://www.{abbr}{year}.org/"]
    seen, out = set(), []
    for s in sites:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def program_links(page_html, base):
    links = []
    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page_html, re.S | re.I):
        u = urllib.parse.urljoin(base, html.unescape(href))
        text = re.sub(r"<[^>]+>", " ", label).lower()
        if ("program" in text or "program" in u.lower()) and (u.lower().endswith(".pdf") or "/s/" in u or ".pdf" in u.lower()):
            links.append(u)
    return list(dict.fromkeys(links))


def find_logo(page_html, base, short=""):
    """The meeting's logo on its website: a picture whose file name, description or class says
    "logo" (preferring one that also names the meeting), else the page's sharing image."""
    cands = []
    for tag in re.findall(r"<img\b[^>]*>", page_html, re.I):
        src = re.search(r'\b(?:data-src|data-image|src)="([^"]+)"', tag, re.I)
        if not src:
            continue
        url = urllib.parse.urljoin(base, html.unescape(src.group(1)))
        text = (url + " " + " ".join(re.findall(r'\b(?:alt|class|title)="([^"]*)"', tag, re.I))).lower()
        if "logo" in text:
            abbr = short.split()[0].lower() if short else ""
            score = 2 if abbr and abbr in text else 1
            if any(w in text for w in ("acs-central", "acs-logo", "sponsor", "duquesne")):
                score = 0                               # the society's or a sponsor's logo, not the meeting's
            cands.append((score, url))
    cands = [c for c in cands if c[0] > 0]
    if cands:
        return max(cands, key=lambda c: c[0])[1]
    og = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page_html, re.I)
    return urllib.parse.urljoin(base, html.unescape(og.group(1))) if og else ""


def save_logo(url, key):
    """Download the logo to images/acs/<meeting>-logo.png (shown first in the meeting's block)."""
    from PIL import Image
    data, _ = get(url, binary=True, timeout=30)
    im = Image.open(io.BytesIO(data))
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    im.thumbnail((1000, 1000))
    dst = ROOT / "images" / "acs" / f"{key}-logo.png"
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, "PNG", optimize=True)
    print(f"  logo saved: {dst.relative_to(ROOT)}")
    return str(dst.relative_to(ROOT))


def read_program(url_or_bytes, people, source=""):
    data = url_or_bytes if isinstance(url_or_bytes, bytes) else get(url_or_bytes, binary=True, timeout=60)[0]
    lines, short = program_lines(data)
    info = meeting_info(lines)
    if short:
        info["short"] = re.sub(r"^([A-Z]+)\s*(\d{4})$", r"\1 \2", short)
    info["presentations"] = presentations(lines, people)
    info["program"] = source
    return info


def save(state, info, site=""):
    key = (info.get("short") or info.get("name") or info.get("program", "")).lower().replace(" ", "-")
    if not key:
        return
    old = state["meetings"].get(key, {})
    old.update({k: v for k, v in info.items() if v})
    if site:
        old["site"] = site
    old["checked"] = datetime.date.today().isoformat()
    old["key"] = key
    state["meetings"][key] = old
    print(f"{info.get('short') or info.get('name')}: {len(info.get('presentations', []))} presentation(s) by the group")


def main():
    state = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    state.setdefault("meetings", {})
    state.setdefault("programs", {})
    people = members()
    if len(sys.argv) > 1:                                    # a program file given by hand
        p = pathlib.Path(sys.argv[1])
        save(state, read_program(p.read_bytes(), people, sys.argv[2] if len(sys.argv) > 2 else ""))
    else:
        today = datetime.date.today()
        for site in candidate_sites(today):
            try:
                page, final = get(site, timeout=15)
            except Exception:
                continue                                     # no such site (yet)
            for url in program_links(page, final):
                done = state["programs"].get(url, {})
                m = state["meetings"].get(done.get("meeting", ""), {})
                end = m.get("end")
                if end and datetime.date.fromisoformat(end) + datetime.timedelta(days=30) < today:
                    continue                                 # long over; program won't change
                try:
                    info = read_program(url, people, url)
                except Exception as e:
                    print(f"  could not read {url} ({e})")
                    continue
                if not info.get("name") and not info.get("short"):
                    continue
                save(state, info, final)
                key = (info.get("short") or info.get("name")).lower().replace(" ", "-")
                state["programs"][url] = {"meeting": key, "read": today.isoformat()}
                time.sleep(1)
            # the meeting's logo, once, for every meeting found on this website
            for key, m in state["meetings"].items():
                if m.get("site") == final and m.get("presentations") and not (ROOT / "images" / "acs" / f"{key}-logo.png").exists():
                    logo = find_logo(page, final, m.get("short", ""))
                    if logo:
                        try:
                            m["logo"] = save_logo(logo, key)
                        except Exception as e:
                            print(f"  could not save the logo from {logo} ({e})")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the site build over this
        print(f"::warning::ACS meeting check skipped: {e}")
        sys.exit(0)
