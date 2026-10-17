"""
Build the website from the files in content/ and data/publications.json.

    pip install markdown pyyaml
    python scripts/build.py

The finished site is written to _site/. Open _site/index.html in a browser to preview.
GitHub runs this automatically on every change (.github/workflows/site.yml).

A mistake in one content file never breaks the whole site: that file is skipped
and a warning is printed (and shown on the GitHub Actions run page).
"""
import datetime
import html
import json
import pathlib
import re
import shutil
import sys

import markdown
import yaml
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
TEMPLATES = ROOT / "templates"
OUT = ROOT / "_site"

PEOPLE_GROUPS = [
    ("postdoc", "Postdocs"),
    ("staff", "Staff"),
    ("grad", "Graduate students"),
    ("undergrad", "Honors undergraduates"),
]
DISSERTATIONS = []  # filled in main() from data/dissertations.json
AUTO_COVERS = []    # filled in main() from data/covers.json (weekly cover check)
SHOWN_COVERS = {}   # doi -> (image, label) for every cover on the site

ALUMNI_CATEGORIES = [  # (key, heading); order on the page
    ("postdoc", "Postdoctoral researchers"),
    ("phd", "Ph.D. graduates"),
    ("ms", "MS"),
    ("grad", "Graduate students"),
    ("undergrad", "Honors undergraduates"),
    ("staff", "Staff and visiting scholars"),
]
RECENT_YEARS = 5  # publication years shown before the "Show earlier" button

warnings = []


def warn(path, msg):
    rel = path.relative_to(ROOT) if isinstance(path, pathlib.Path) else path
    warnings.append(f"{rel}: {msg}")
    print(f"::warning file={rel}::{msg}")


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def md(text):
    return markdown.markdown(text or "", extensions=["extra", "sane_lists", "smarty"])


def read_md(path):
    """Return (front_matter_dict, body_markdown) or None if the file is broken."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    meta, body = {}, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            warn(path, f"skipped: the settings at the top of the file have a formatting error ({e.problem}, line {e.problem_mark.line + 2 if e.problem_mark else '?'} of the file).")
            return None
        if not isinstance(meta, dict):
            warn(path, "skipped: the settings at the top of the file aren't in 'label: value' form.")
            return None
        body = m.group(2)
    return meta, body.strip()


def items(folder):
    """All usable .md files in a folder, skipping templates (files starting with _)."""
    d = CONTENT / folder
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        if p.name.startswith("_"):
            continue
        r = read_md(p)
        if r:
            out.append((p, r[0], r[1]))
    return out


def image(path_str, source_file):
    """Return a usable image path, or '' (with a warning) if the file doesn't exist."""
    if not path_str:
        return ""
    p = ROOT / path_str
    for folder in ("images", "images/news", "images/people"):   # the folder was left off the file name
        if not p.is_file() and (ROOT / folder / path_str).is_file():
            return f"{folder}/" + str(path_str).lstrip("/")
    if not p.is_file():
        warn(source_file, f"image '{path_str}' not found; check the file name and that it's uploaded.")
        return ""
    return path_str


def initials(name):
    parts = [w for w in re.sub(r",.*$", "", name).split() if w[0].isalpha()]
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "?"


def figure_html(path_str, source_file, alt=""):
    """SVG figures are inlined so they follow the site's colors (and dark mode);
    other images use a normal <img>."""
    path_str = image(path_str, source_file)
    if not path_str:
        return ""
    if path_str.lower().endswith(".svg"):
        svg = (ROOT / path_str).read_text(encoding="utf-8")
        svg = re.sub(r"<\?xml.*?\?>|<!DOCTYPE.*?>", "", svg, flags=re.S).strip()
        return svg
    return f'<img src="{esc(path_str)}" alt="{esc(alt)}" loading="lazy">'



def pick_figure(m):
    """Use the paper figure if it has been uploaded; otherwise the drawn fallback (no warning)."""
    for key, cap, credit in (("figure", "caption", True), ("fallback_figure", "fallback_caption", False)):
        f = m.get(key)
        if f and (ROOT / f).is_file():
            return f, m.get(cap) or m.get("caption", ""), (m.get("figure_credit", "") if credit else ""), (m.get("figure_doi", "") if credit else "")
    if m.get("figure") and not m.get("fallback_figure"):
        return m["figure"], m.get("caption", ""), m.get("figure_credit", ""), m.get("figure_doi", "")
    return "", "", "", ""


def doi_url(doi):
    doi = str(doi).strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi, flags=re.I)
    return "https://doi.org/" + doi, doi


def collaborators_html(value):
    if not value:
        return ""
    if isinstance(value, str):
        return f'<p class="collab">Collaborators: {esc(value)}</p>'
    items_ = []
    for c in value:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        name = esc(c["name"])
        if c.get("url"):
            name = f'<a href="{esc(c["url"])}">{name}</a>'
        aff = f' <span class="aff">({esc(c["affiliation"])})</span>' if c.get("affiliation") else ""
        items_.append(f"<li>{name}{aff}</li>")
    return f'<div class="collab"><h4>Collaborators</h4><ul>{"".join(items_)}</ul></div>' if items_ else ""


def key_papers_html(dois, pubs_by_doi, path):
    if not dois:
        return ""
    rows = []
    for d in dois:
        url, bare = doi_url(d)
        p = pubs_by_doi.get(bare.lower())
        if p:
            meta = ", ".join(x for x in [p.get("venue", ""), str(p.get("year", ""))] if x)
            rows.append(f'<li><a href="{esc(url)}">{esc(p["title"])}</a><span class="src">{esc(meta)}</span></li>')
        else:
            warn(path, f"paper {bare} is not in the publication list yet; showing the DOI only.")
            rows.append(f'<li><a href="{esc(url)}">doi:{esc(bare)}</a></li>')
    return f'<div class="key-papers"><h4>Key papers</h4><ul>{"".join(rows)}</ul></div>'


def lattice_svg():
    """Background pattern for the banner: hexagons with circuit traces, echoing the logo."""
    import math
    import random
    rnd = random.Random(7)
    r = 44
    w, h = r * math.sqrt(3), r * 1.5
    hexes, traces, nodes, gold = [], [], [], []
    trace_paths, hex_paths = [], []
    k = 0
    for row in range(-1, 8):
        for col in range(-1, 16):
            cx = col * w + (w / 2 if row % 2 else 0)
            cy = row * h
            pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(-90, 270, 60)]
            hexes.append("M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + " Z")
            hex_paths.append((cx, cy, hexes[-1]))
            if rnd.random() < 0.55:
                for _ in range(rnd.choice([1, 2])):
                    i = rnd.randrange(6)
                    (ax, ay), (bx, by) = pts[i], pts[(i + 1) % 6]
                    t = rnd.choice([0.3, 0.5, 0.7])
                    sx, sy = ax + (bx - ax) * t, ay + (by - ay) * t
                    dx, dy = cx - sx, cy - sy
                    m1x, m1y = sx + dx * 0.35, sy + dy * 0.35
                    turn = rnd.choice([-1, 1]) * math.radians(60)
                    ang = math.atan2(dy, dx) + turn
                    ex, ey = m1x + math.cos(ang) * r * 0.32, m1y + math.sin(ang) * r * 0.32
                    k += 1
                    d = f"M{sx:.1f} {sy:.1f} L{m1x:.1f} {m1y:.1f} L{ex:.1f} {ey:.1f}"
                    traces.append(f'<path class="tr" style="--d:{(k % 23) * 0.05:.2f}s" d="{d}"/>')
                    trace_paths.append((sx, d))
                    nodes.append(f'<circle class="nd" style="--d:{(k % 23) * 0.05 + 0.5:.2f}s" cx="{ex:.1f}" cy="{ey:.1f}" r="3"/>')
    for (row, col, i) in [(1, 12, 1), (3, 13, 4), (4, 11, 0), (2, 14, 2)]:
        cx = col * w + (w / 2 if row % 2 else 0)
        cy = row * h
        a = math.radians(-90 + 60 * i)
        gold.append(f'<circle class="gd" cx="{cx + r * math.cos(a):.1f}" cy="{cy + r * math.sin(a):.1f}" r="8"/>')
    # Ongoing motion: gold "signals" run along circuit traces and around a few hexagons, each on
    # its own slow cycle, so the pattern keeps changing. Only where the pattern is visible (right side).
    pulses = []
    visible_traces = [d for x, d in trace_paths if x > 760]
    for j, d in enumerate(rnd.sample(visible_traces, min(16, len(visible_traces)))):
        pulses.append(f'<path class="pl" pathLength="100" style="--pd:{rnd.uniform(0, 9):.1f}s;--pt:{rnd.uniform(5, 9):.1f}s" d="{d}"/>')
    visible_hexes = [d for cx, cy, d in hex_paths if cx > 800 and -20 < cy < 480]
    for d in rnd.sample(visible_hexes, min(6, len(visible_hexes))):
        pulses.append(f'<path class="pl ring" pathLength="100" style="--pd:{rnd.uniform(0, 12):.1f}s;--pt:{rnd.uniform(9, 14):.1f}s" d="{d}"/>')
    return (f'<svg class="lattice" viewBox="0 0 1100 460" preserveAspectRatio="xMaxYMid slice" aria-hidden="true" focusable="false">'
            f'<path class="hx" d="{" ".join(hexes)}"/>{"".join(traces)}{"".join(pulses)}{"".join(nodes)}{"".join(gold)}</svg>')


# ---------------------------------------------------------------- sections

def research_areas():
    areas = []
    for path, m, b in items("research"):
        if not m.get("title"):
            warn(path, "skipped: needs a 'title:' line.")
            continue
        slug = re.sub(r"^\d+-", "", path.stem)
        areas.append((path, m, b, slug))
    return areas


def build_home(site, pubs, areas):
    meta, body = read_md(CONTENT / "home.md") or ({}, "")
    latest = "".join(
        f'<li><a href="{esc(p["doi"] or "#publications")}">{esc(p["title"])}</a>'
        f'<span class="src">{esc(p.get("venue", ""))}{", " if p.get("venue") else ""}{esc(p["year"])}</span></li>'
        for p in [q for q in pubs if is_paper(q)][:4]
    )
    tiles = "".join(
        f'<a class="tile" href="#research" data-target="area-{esc(slug)}">'
        f'<div class="tile-fig">{figure_html(pick_figure(m)[0], path, pick_figure(m)[1]) if pick_figure(m)[0] else ""}</div>'
        f'<span class="tile-title">{esc(m["title"])}</span></a>'
        for path, m, b, slug in areas
    )
    news_html = latest_news_html(news_items())
    if news_html:
        news_html += instagram_html(site, "More on Instagram")
    return f'''
  <section class="page" id="home">
    <div class="split">
      <div class="intro">{md(body)}</div>
      <aside class="latest-box">
        <h3>Latest papers</h3>
        <ul class="latest">{latest}</ul>
        <a class="more-link" href="#publications">All publications</a>
      </aside>
    </div>
    {f'<h2 class="section-title">Research</h2><div class="tiles">{tiles}</div>' if tiles else ""}
    {news_html}
  </section>'''


def acs_issue_cover_urls(c):
    """Addresses where ACS keeps an issue's cover, worked out from the issue link
    (e.g. https://pubs.acs.org/apcach/issue/3/1). Visitors' browsers load the image directly
    from ACS, so nothing is downloaded during the build. Tried in order; the first that loads wins."""
    m = re.match(r"https?://pubs\.acs\.org/(?:toc/)?([a-z]+)/(?:issue/)?(\d+)/(\d+)", str(c.get("issue") or ""))
    if not m:
        return []
    code, vol, num = m.groups()
    urls = [f"https://pubs.acs.org/pb-assets/images/_journalCovers/{code}/{code}_v{int(vol):03d}i{int(num):03d}.jpg"]
    years = re.findall(r"(?:19|20)\d\d", f'{c.get("year", "")} {c.get("label", "")}')
    if years:
        stem = f"{code}.{years[0]}.{vol}.issue-{num}"
        urls.append(f"https://pubs.acs.org/cms/10.1021/{stem}/asset/{stem}.largecover.jpg")
    return urls


def fetch_cover(c, path):
    """Get a cover image from the web (an image address, or the journal's issue page) and save it
    into the built site. Runs on GitHub, which can reach journal websites."""
    import hashlib
    import urllib.request
    src = str(c.get("image") or "")
    issue = str(c.get("issue") or "")
    key = hashlib.md5((src or issue).encode()).hexdigest()[:12]
    rel = f"images/covers/auto-{key}.jpg"
    if (OUT / rel).exists():
        return rel
    ua = {"User-Agent": "Mozilla/5.0 (evanseck-group-website build)", "Accept": "text/html,image/*,*/*"}
    try:
        if not src and issue:
            page = urllib.request.urlopen(urllib.request.Request(issue, headers=ua), timeout=25).read().decode("utf-8", "replace")
            m = (re.search(r'<img[^>]+src="([^"]*(?:largecover|cover)[^"]*\.(?:jpe?g|png|gif))"', page, re.I)
                 or re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page, re.I))
            if not m:
                warn(path, f"no cover image found on {issue}; paste the image address under image: instead.")
                return ""
            src = urllib.parse.urljoin(issue, html.unescape(m.group(1)))
        data = urllib.request.urlopen(urllib.request.Request(src, headers=ua), timeout=25).read()
        (OUT / "images" / "covers").mkdir(parents=True, exist_ok=True)
        (OUT / rel).write_bytes(data)
        return rel
    except Exception as e:
        warn(path, f"could not download cover ({e}); upload the image to images/covers/ instead.")
        return ""


def web_copy(img, width=600):
    """Large uploaded images get a small copy for the web page (originals are kept).
    iPhone photos (.heic), which most browsers can't show, are always converted to .jpg."""
    src = ROOT / img
    heic = src.suffix.lower() in (".heic", ".heif")
    if not src.is_file() or (src.stat().st_size < 400_000 and not heic):
        return img
    try:
        from PIL import Image, ImageOps
        if heic:
            import pillow_heif
            pillow_heif.register_heif_opener()
    except ImportError:
        if heic:
            print(f"  {img}: .heic photos need pillow-heif (see the workflow file); skipped.")
            return ""
        return img
    rel = f"images/web/{re.sub(r'[^A-Za-z0-9._-]+', '-', src.stem)}-{width}.jpg"
    dst = OUT / rel
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((width, width * 2))
            im.save(dst, "JPEG", quality=85, optimize=True, progressive=True)
    return rel


def cover_card(img, label, backup, parent, paper_url):
    """Cover image linked to the journal issue page (its 'parent site'); paper link below."""
    alt = f"Journal cover: {label}" if label else "Journal cover"
    pic = (f'<img src="{esc(img)}" alt="{esc(alt)}" loading="lazy" referrerpolicy="no-referrer"'
           + (f' data-backup="{esc(backup)}"' if backup else "") + ' onerror="coverFailed(this)">')
    target = parent or paper_url
    if target:
        where = "the journal issue" if parent else "the paper"
        pic = f'<a href="{esc(target)}" aria-label="{esc(alt)}. Opens {where}.">{pic}</a>'
    cap = esc(label)
    if parent and paper_url:
        cap += f'{"<br>" if cap else ""}<a class="cover-paper" href="{esc(paper_url)}">Read the paper</a>'
    return f'<figure class="cover">{pic}{f"<figcaption>{cap}</figcaption>" if cap else ""}</figure>'


def covers_html(covers, pubs_by_doi, path):
    """Journal covers featuring the group's work: thumbnail linked to the paper."""
    if not covers:
        return ""
    cards = []
    for c in covers:
        if not isinstance(c, dict):
            continue
        backup = ""
        if str(c.get("image") or "").startswith("http"):
            img = c["image"]                      # loaded by the visitor's browser
        elif c.get("issue") and not c.get("image"):
            options = acs_issue_cover_urls(c)
            img = options[0] if options else fetch_cover(c, path)
            backup = options[1] if len(options) > 1 else ""
        else:
            img = image(c.get("image"), path)
        if not img:
            continue
        url, bare = doi_url(c["doi"]) if c.get("doi") else ("", "")
        p = pubs_by_doi.get(bare.lower()) if bare else None
        label = c.get("label") or (", ".join(x for x in [p.get("venue", ""), str(p.get("year", ""))] if x) if p else "")
        parent = web(c.get("link") or c.get("issue") or "")
        if not img.startswith("http"):
            img = web_copy(img)
        if bare:
            SHOWN_COVERS.setdefault(bare.lower(), (img, label, backup, parent))
        cards.append(cover_card(img, label, backup, parent, url))
    if not cards:
        return ""
    return f'<div class="covers"><h4>Journal covers</h4><div class="cover-row">{"".join(cards)}</div></div>'


NEWS_CATEGORIES = [("accomplishment", "Accomplishments"), ("instagram", "Instagram"), ("seminar", "Seminar reviews"),
                   ("news", "Group news")]
NEWS_TAGS = {"accomplishment": "Accomplishment", "seminar": "Seminar review", "news": "Group news",
             "instagram": "Instagram"}


def news_items():
    out = []
    for path, m, b in items("news"):
        if not m.get("title"):
            warn(path, "skipped: news needs a 'title:' line.")
            continue
        d = m.get("date")
        month_only = bool(re.fullmatch(r"\d{4}-\d{2}", str(d)))   # "2023-01" shows as "January 2023"
        try:
            d = d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d) + ("-01" if month_only else ""))
        except ValueError:
            warn(path, "skipped: 'date:' should look like 2026-10-15 (or 2026-10 for a month).")
            continue
        cat = str(m.get("category", "news")).strip().lower()
        if cat not in dict(NEWS_CATEGORIES):
            warn(path, f"category '{cat}' unknown; using 'news' (use accomplishment, seminar or news).")
            cat = "news"
        photos = [ph for ph in (m.get("photos") or []) if image(ph, path)]
        out.append({"path": path, "slug": "news-" + path.stem, "m": m, "body": b, "date": d, "month_only": month_only,
                    "cat": cat, "photos": photos})
    out += duquesne_news_items()
    out += grs_items(out)
    out += instagram_items()
    return sorted(out, key=lambda n: n["date"], reverse=True)


def instagram_items():
    """Posts from the lab's Instagram account (scripts/update_instagram.py), as photo tiles that open
    the post on Instagram."""
    f = ROOT / "data" / "instagram.json"
    if not f.exists():
        return []
    data = json.loads(f.read_text(encoding="utf-8"))
    out = []
    for p in data.get("posts", []):
        try:
            d = datetime.date.fromisoformat(p.get("date", ""))
        except ValueError:
            continue
        cap = (p.get("caption") or "").strip()
        first = re.split(r"(?<=[.!?])\s|\n", cap, maxsplit=1)[0].strip() if cap else ""
        title = first if first and len(first) <= 110 else (first[:107].rsplit(" ", 1)[0] + "…" if first else "Instagram post")
        m = {"title": title, "link": p["permalink"], "link_text": "View on Instagram",
             "source": "@" + data["username"] if data.get("username") else "Instagram"}
        out.append({"path": f, "slug": "ig-" + p["id"], "m": m, "body": "", "date": d, "month_only": False,
                    "cat": "instagram", "photos": [p["image"]] if p.get("image") and (ROOT / p["image"]).is_file() else [],
                    "auto": True})
    return out


def grs_items(existing):
    """Graduate Research Symposium years found by scripts/update_grs.py (data/grs.json), shown as
    seminar blocks. A post you write for the same year takes its place. Photos named
    images/news/grs-<year>-*.jpg (or .png) are added automatically."""
    f = ROOT / "data" / "grs.json"
    if not f.exists():
        return []
    cfg = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8")).get("grs", {})
    hide = {str(y) for y in cfg.get("hide_years", [])}
    written = {n["date"].year for n in existing
               if n["cat"] == "seminar" and "graduate research symposium" in (n["m"]["title"] + " " + str(n["m"].get("event", ""))).lower()}
    out = []
    for year, e in sorted(json.loads(f.read_text(encoding="utf-8")).get("years", {}).items()):
        if year in hide or int(year) in written:
            continue
        try:
            d = datetime.date.fromisoformat(e.get("date") or "")
            month_only = False
        except ValueError:
            d, month_only = datetime.date(int(year), 3, 1), True
        won = {a["presenter"]: a for a in e.get("awards", []) if a.get("presenter")}
        pres = []
        for p in e.get("presentations", []):
            row = {"presenter": p["presenter"], "title": p.get("title", ""), "format": p.get("format", "")}
            if p["presenter"] in won:
                row["award"] = won.pop(p["presenter"]).get("award") or "Award"
            pres.append(row)
        for a in won.values():   # award winners not found in the booklet
            pres.append({"presenter": a["presenter"], "title": a.get("title", ""), "award": a.get("award") or "Award"})
        links = [{"text": t, "url": e["links"][k]} for k, t in
                 [("program", "Program booklet (PDF)"), ("awards", "Award winners (PDF)"),
                  ("schedule", "Schedule (PDF)"), ("forager", "Presentations on Symposium by ForagerOne")]
                 if e.get("links", {}).get(k)]
        n_talk = sum(1 for p in pres if "Oral" in p.get("format", ""))
        n_award = sum(1 for p in pres if p.get("award"))
        words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
        say = lambda k: words[k] if k < len(words) else str(k)
        body = ("Duquesne's annual Graduate Research Symposium brings graduate students from every school together "
                "to share their research.")
        if pres:
            body += (f" At the {year} symposium, {say(len(pres))} group member{'s' if len(pres) != 1 else ''} "
                     f"presented {'their' if len(pres) != 1 else 'their'} research"
                     + (f", {say(n_talk)} in the oral sessions" if n_talk else "") + ".")
        if n_award:
            body += f" {say(n_award).capitalize()} of them received {'awards' if n_award != 1 else 'an award'}."
        photos = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "images" / "news").glob(f"grs-{year}-*")
                        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".heic"))
        m = {"title": f"Graduate Research Symposium {year}",
             "event": "Duquesne University Graduate Research Symposium (GRS)", "event_url": e.get("page", ""),
             "location": "Duquesne University", "header_image": e.get("image", ""),
             "header_caption": "Photo: Duquesne University" if e.get("image") else "",
             "presentations": pres, "links": links}
        out.append({"path": f, "slug": f"grs-{year}", "m": m, "body": body, "date": d, "month_only": month_only,
                    "cat": "seminar", "photos": photos})
    return out


def duquesne_news_items():
    """Stories found on Duquesne's own news pages and in the Duquesne Times (scripts/update_news.py)."""
    import hashlib
    f = ROOT / "data" / "duquesne-news.json"
    if not f.exists():
        return []
    out = []
    years = int(json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8"))
                .get("duquesne_news", {}).get("years", 3))
    since = datetime.date.today() - datetime.timedelta(days=round(365.25 * years))
    for i in json.loads(f.read_text(encoding="utf-8")).get("items", []):
        try:
            d = datetime.date.fromisoformat(i["date"])
        except (KeyError, ValueError):
            continue
        if d < since:
            continue                                    # older than the window (default 3 years)
        times = "Times" in i.get("source", "")
        m = {"title": i["title"], "link": i["url"],
             "link_text": "Read it in the Duquesne Times" if times else
                          ("See Duquesne's grants list" if "grants" in i["url"] else "Read the story"),
             "source": i.get("source", "Duquesne University")}
        body = re.sub(r"([\\`*_\[\]#<>])", r"\\\1", i.get("excerpt", ""))
        if times and body == re.sub(r"([\\`*_\[\]#<>])", r"\\\1", i["title"]):
            body = ""                                   # short newsletter notes: the title says it all
        out.append({"path": None, "slug": "dq-" + hashlib.md5((i["url"] + i.get("key", "")).encode()).hexdigest()[:10],
                    "m": m, "body": body, "date": d, "month_only": i.get("precision") == "month",
                    "undated": not i.get("dated", True) or i.get("precision") == "year",
                    "remote_photo": i.get("image", ""), "auto": True,
                    "cat": "accomplishment", "photos": []})
    return out


def news_date(n):
    if n.get("undated"):
        return ""
    d = n["date"]
    return f"{d.strftime('%B')} {d.year}" if n.get("month_only") else f"{d.strftime('%B')} {d.day}, {d.year}"


def instagram_html(site, lead="Follow along on Instagram"):
    """Links to the Duquesne Instagram accounts that feature the group (site.yml: instagram)."""
    links = []
    for a in site.get("instagram") or []:
        if isinstance(a, dict) and a.get("url"):
            handle = "@" + a["url"].rstrip("/").rsplit("/", 1)[-1]
            links.append(f'<a href="{esc(web(a["url"]))}"><span class="ig-name">{esc(a.get("name", handle))}</span>'
                         f' <span class="ig-handle">{esc(handle)}</span></a>')
    if not links:
        return ""
    lead = f'<span class="ig-lead">{esc(lead)}</span>' if lead else ""
    return f'<p class="ig-strip">{lead}{"".join(links)}</p>'


def fetch_story_photo(url):
    """Save a story's photo (from Duquesne's site) into the built site as a small square-ish copy.
    Runs on GitHub during the build; if the photo can't be fetched, the tile shows the lab mark instead."""
    import hashlib
    import urllib.request
    rel = f"images/announcements/{hashlib.md5(url.encode()).hexdigest()[:12]}.jpg"
    if (OUT / rel).exists():
        return rel
    try:
        from PIL import Image
        import io
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (evanseck-group-website build)"})
        data = urllib.request.urlopen(req, timeout=20).read()
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((900, 900))
            (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
            im.save(OUT / rel, "JPEG", quality=84, optimize=True, progressive=True)
        return rel
    except Exception as e:
        print(f"  announcement photo not saved ({e}): {url}")
        return ""


ANN = {}   # site.yml "announcements" settings (photos to use; set in main)


def announcement_photo(n):
    """(image path for the tile and the full view, photo credit) for one announcement.
    Order: a photo chosen in site.yml for this announcement, the post's own photo, the story's
    photo from Duquesne's site, then one of the fill-in photos from site.yml (taking turns)."""
    text = (n["m"]["title"] + " " + n["m"].get("link", "") + " " + n.get("body", "")).lower()
    for p in ANN.get("photos") or []:
        if isinstance(p, dict) and p.get("match") and p.get("image") and str(p["match"]).lower() in text:
            if image(p["image"], CONTENT / "site.yml"):
                return web_copy(image(p["image"], CONTENT / "site.yml"), 900), ""
    if n["photos"]:
        return web_copy(n["photos"][0], 900), ""
    hdr = str(n["m"].get("header_image") or "") if n["cat"] == "seminar" else ""
    if hdr:
        return ((fetch_story_photo(hdr) or hdr) if hdr.startswith("http") else web_copy(image(hdr, n["path"]), 900)), ""
    if n["cat"] == "seminar" and n["m"].get("logo") and image(n["m"]["logo"], n["path"]):
        return web_copy(n["m"]["logo"], 900), ""
    if n.get("remote_photo"):
        got = fetch_story_photo(n["remote_photo"]) or n["remote_photo"]   # else visitors load it from Duquesne
        return got, f'Photo: {n["m"].get("source", "Duquesne University")}'
    fill = [f for f in (ANN.get("fill_in_photos") or []) if image(f, CONTENT / "site.yml")]
    if fill:
        import hashlib
        pick = fill[int(hashlib.md5(n["slug"].encode()).hexdigest(), 16) % len(fill)]
        return web_copy(pick, 900), ""
    return "", ""


def news_tile(n):
    m = n["m"]
    img, _ = n["_photo"]
    pic = (f'<img src="{esc(img)}" alt="" loading="lazy" referrerpolicy="no-referrer" '
           f'onerror="this.outerHTML=\'<span class=an-blank><img src=images/logo-mark-dark.png alt></span>\'">' if img else
           f'<span class="an-blank" aria-hidden="true"><img src="images/logo-mark-dark.png" alt=""></span>')
    when = news_date(n)
    tag = "" if n["cat"] == "accomplishment" else f'<span class="an-tag">{esc(NEWS_TAGS[n["cat"]])}</span>'
    inner = (f'{pic}{tag}'
             f'<span class="an-cap">{f"<span class=an-date>{esc(when)}</span>" if when else ""}'
             f'<span class="an-title">{esc(m["title"])}</span>'
             f'{f"<span class=an-src>{esc(m["source"])} ↗</span>" if n.get("auto") else ""}</span>')
    if n.get("seminar_link"):   # Home page: a seminar tile jumps to its block on the Announcements tab
        return (f'<a class="an-tile{"" if img else " no-photo"}" href="#announcements" data-target="{esc(n["slug"])}" '
                f'data-cat="{n["cat"]}">{inner}</a>')
    if n.get("auto"):     # stories from Duquesne's site: the tile is a link to the original post
        return (f'<a class="an-tile{"" if img else " no-photo"}" href="{esc(web(m["link"]))}" target="_blank" '
                f'rel="noopener" data-cat="{n["cat"]}">{inner}</a>')
    return (f'<button type="button" class="an-tile{"" if img else " no-photo"}" data-open="{esc(n["slug"])}" '
            f'data-cat="{n["cat"]}" aria-haspopup="dialog">{inner}</button>')


def news_article(n):
    """The full announcement, shown in a pop-up when its tile is clicked (and listed in full if
    JavaScript is off)."""
    m = n["m"]
    meta = []
    if n["cat"] == "seminar":
        who = ", ".join(x for x in [m.get("speaker", ""), m.get("speaker_affiliation", "")] if x)
        where = ", ".join(x for x in [m.get("event", ""), m.get("location", "")] if x)
        if who:
            meta.append(f'<span>Speaker: {esc(who)}</span>')
        if where:
            meta.append(f'<span>{esc(where)}</span>')
    if m.get("source"):
        meta.append(f'<span class="nw-from">From {esc(m["source"])}</span>')
    if m.get("author"):
        meta.append(f'<span>Written by {esc(m["author"])}</span>')
    img, credit = n["_photo"]
    fig = ""
    if img:
        cap = m.get("photo_caption") or credit
        fig = (f'<figure class="an-photo"><img src="{esc(img)}" alt="{esc(m.get("photo_alt") or m["title"])}" loading="lazy">'
               f'{f"<figcaption>{esc(cap)}</figcaption>" if cap else ""}</figure>')
    extra = "".join(f'<img src="{esc(web_copy(ph, 600))}" alt="" loading="lazy">' for ph in n["photos"][1:])
    link = f'<p class="an-link"><a href="{esc(web(m["link"]))}">{esc(m.get("link_text") or "More")}</a></p>' if m.get("link") else ""
    when = news_date(n)
    return (f'<article class="an-post" id="{esc(n["slug"])}" data-cat="{n["cat"]}">{fig}<div class="an-body">'
            f'<p class="nw-top">{f"<time datetime={n["date"].isoformat()}>{when}</time>" if when else ""}'
            f'{"" if n["cat"] == "accomplishment" else f"<span class=\'nw-tag nw-{n["cat"]}\'>{esc(NEWS_TAGS[n["cat"]])}</span>"}</p>'
            f'<h3>{esc(m["title"])}</h3>{f"<p class=nw-meta>{"".join(meta)}</p>" if meta else ""}'
            f'{md(n["body"])}{f"<div class=an-more-photos>{extra}</div>" if extra else ""}{link}</div></article>')


SPEAKER_ROLES = [("keynote", "Keynote"), ("invited", "Invited talk"), ("student", "Student talk")]


def seminar_speakers(m):
    """Guest speakers, highlighted and linked to their own websites. Keynote and invited speakers
    get a card each; student talks are listed compactly behind a "Student talks" toggle."""
    people = [p for p in (m.get("speakers") or []) if isinstance(p, dict) and p.get("name")]
    if not people and m.get("speaker"):        # older single-speaker posts
        people = [{"name": m["speaker"], "affiliation": m.get("speaker_affiliation", ""),
                   "url": m.get("speaker_url", ""), "talk": m.get("talk", ""), "role": "invited"}]
    if not people:
        return ""

    def name(p):
        return (f'<a href="{esc(web(p["url"]))}">{esc(p["name"])}</a>' if p.get("url") else esc(p["name"]))

    cards, students = "", ""
    for p in people:
        role = str(p.get("role", "invited")).lower()
        if role == "student":
            students += (f'<li><span class="sp-name">{name(p)}</span>'
                         f'{f" <span class=sp-aff>{esc(p["affiliation"])}</span>" if p.get("affiliation") else ""}'
                         f'{f"<span class=sp-talk>{esc(p["talk"])}</span>" if p.get("talk") else ""}</li>')
        else:
            label = dict(SPEAKER_ROLES).get(role, "")
            cards += (f'<li class="sp-card{" is-keynote" if role == "keynote" else ""}">'
                      f'{f"<span class=sp-role>{esc(label)}</span>" if label and len(people) > 1 else ""}'
                      f'<span class="sp-name">{name(p)}</span>'
                      f'{f"<span class=sp-aff>{esc(p["affiliation"])}</span>" if p.get("affiliation") else ""}'
                      f'{f"<span class=sp-talk>{esc(p["talk"])}</span>" if p.get("talk") else ""}</li>')
    out = f'<ul class="sp-cards">{cards}</ul>' if cards else ""
    if students:
        n_st = students.count("<li>")
        out += (f'<details class="sp-students"><summary>Student talks ({n_st})</summary>'
                f'<ul>{students}</ul></details>')
    return f'<div class="sm-speakers">{out}</div>'


def group_presentations(m):
    """The group's own talks and posters at an event, with any awards highlighted."""
    rows = [p for p in (m.get("presentations") or []) if isinstance(p, dict) and p.get("presenter")]
    if not rows:
        return ""
    cards = ""
    for p in rows:
        who = (f'<a href="{esc(web(p["url"]))}">{esc(p["presenter"])}</a>' if p.get("url") else esc(p["presenter"]))
        cards += (f'<li class="sp-card{" is-award" if p.get("award") else ""}">'
                  f'{f"<span class=sp-award>Award · {esc(p["award"])}</span>" if p.get("award") else ""}'
                  f'{f"<span class=sp-role>{esc(p["format"])}</span>" if p.get("format") else ""}'
                  f'<span class="sp-name">{who}</span>'
                  f'{f"<span class=sp-talk>{esc(p["title"])}</span>" if p.get("title") else ""}'
                  f'{f"<span class=sp-aff>{esc(p["note"])}</span>" if p.get("note") else ""}</li>')
    return f'<div class="sm-speakers"><h4 class="sm-sub">From our group</h4><ul class="sp-cards">{cards}</ul></div>'


def seminar_block(n):
    """One seminar the group attended: a wide block whose strip starts with the seminar's logo,
    followed by the group's photos (scrolls sideways when there are many), then the review."""
    m = n["m"]
    logo = image(m["logo"], n["path"]) if m.get("logo") else ""
    strip = ""
    hdr = str(m.get("header_image") or "")
    if hdr:     # an event banner or photo, from the web (saved during the build) or from images/news/
        src = (fetch_story_photo(hdr) or hdr) if hdr.startswith("http") else web_copy(image(hdr, n["path"]), 1400)
        if src:
            cap = m.get("header_caption") or ""
            strip += (f'<button type="button" class="sm-pic" data-full="{esc(src)}" data-cap="{esc(cap)}" '
                      f'aria-label="Event photo, larger"><img src="{esc(src)}" alt="{esc(m.get("header_alt") or m["title"])}" '
                      f'loading="lazy" referrerpolicy="no-referrer" onerror="this.closest(\'.sm-pic\').remove()"></button>')
    if logo:
        strip += (f'<button type="button" class="sm-pic sm-logo" data-full="{esc(logo)}" aria-label="Seminar logo, larger">'
                  f'<img src="{esc(web_copy(logo, 600))}" alt="{esc(m.get("logo_alt") or (m.get("event") or "Seminar") + " logo")}" loading="lazy"></button>')
    caps = m.get("photo_captions") or []
    for i, ph in enumerate(n["photos"]):
        if not web_copy(ph, 600):
            continue                                    # e.g. a .heic photo that couldn't be converted
        cap = caps[i] if i < len(caps) and caps[i] else (m.get("photo_caption") or "")
        strip += (f'<button type="button" class="sm-pic" data-full="{esc(web_copy(ph, 1400))}" data-cap="{esc(cap)}" '
                  f'aria-label="Photo {i + 1}, larger"><img src="{esc(web_copy(ph, 600))}" '
                  f'alt="{esc(cap or m.get("photo_alt") or "Group photo at the seminar")}" loading="lazy"></button>')
    where = ", ".join(x for x in [m.get("event", ""), m.get("location", "")] if x)
    when = news_date(n)
    meta = " · ".join(esc(x) for x in [when, where] if x)
    if m.get("event_url") and m.get("event"):
        meta = meta.replace(esc(m["event"]), f'<a href="{esc(web(m["event_url"]))}">{esc(m["event"])}</a>', 1)
    speakers = seminar_speakers(m) + group_presentations(m)
    body = md(n["body"])
    link = f'<p class="an-link"><a href="{esc(web(m["link"]))}">{esc(m.get("link_text") or "More")}</a></p>' if m.get("link") else ""
    more = [l for l in (m.get("links") or []) if isinstance(l, dict) and l.get("url")]
    if more:
        link += ('<ul class="sm-links">' + "".join(
            f'<li><a href="{esc(web(l["url"]))}">{esc(l.get("text") or l["url"])}</a></li>' for l in more) + "</ul>")
    # Photos first; the seminar title below them opens the review, speakers and links.
    return (f'<article class="sm-block" id="{esc(n["slug"])}">'
            f'{f"<div class=sm-strip>{strip}</div>" if strip else ""}'
            f'<details class="sm-details"><summary class="sm-summary"><span class="sm-sum-text">'
            f'<span class="sm-title">{esc(m["title"])}</span>{f"<span class=sm-meta>{meta}</span>" if meta else ""}'
            f'</span><span class="sm-chev" aria-hidden="true"></span></summary><div class="sm-text">'
            f'{body}{speakers}'
            f'{f"<p class=sm-by>Review by {esc(m["author"])}</p>" if m.get("author") else ""}{link}</div></details></article>')


def build_news(news, site=None):
    seminars = [n for n in news if n["cat"] == "seminar"]
    news = [n for n in news if n["cat"] != "seminar"]
    for n in news:
        n["_photo"] = announcement_photo(n)
    used = [c for c, _ in NEWS_CATEGORIES if any(n["cat"] == c for n in news)]
    chips = ""
    if len(used) > 1:
        chips = ('<div class="nw-filter" role="group" aria-label="Show announcements by type">'
                 '<button type="button" data-filter="all" aria-pressed="true">All</button>'
                 + "".join(f'<button type="button" data-filter="{c}" aria-pressed="false">{esc(l)}</button>'
                           for c, l in NEWS_CATEGORIES if c in used) + "</div>")
    if news:
        body = (f'<div class="an-grid">{"".join(news_tile(n) for n in news)}</div>'
                f'<div class="an-posts">{"".join(news_article(n) for n in news)}</div>')
    else:
        body = '<p class="note">Announcements will appear here.</p>'
    sem = ""
    if seminars:
        sem = (f'<h3 class="sm-head" id="seminar-reviews">Seminar reviews</h3>'
               f'<div class="sm-list">{"".join(seminar_block(n) for n in seminars)}</div>')
    return f'''
  <section class="page" id="announcements">
    <h2>Announcements</h2>
    {instagram_html(site or {}, "")}
    {chips}
    {body}
    {sem}
    <dialog class="an-dialog" id="an-dialog" aria-label="Announcement">
      <button type="button" class="an-close" aria-label="Close">×</button>
      <div class="an-dialog-body"></div>
    </dialog>
  </section>'''


def latest_news_html(news, count=4):
    if not news:
        return ""
    for n in news[:count]:
        n.setdefault("_photo", announcement_photo(n))
        if n["cat"] == "seminar":
            n["seminar_link"] = True
    return (f'<div class="section-head"><h2 class="section-title">Announcements</h2>'
            f'<a class="more-link" href="#announcements">All announcements</a></div>'
            f'<div class="an-grid an-home">{"".join(news_tile(n) for n in news[:count])}</div>')


def logo_signals_svg():
    """Gold signals running along the logo's own circuit lines (traced by scripts/trace_logo.py)."""
    f = ROOT / "data" / "logo-circuit.json"
    if not f.exists():
        return ""
    import random
    data = json.loads(f.read_text(encoding="utf-8"))
    rnd = random.Random(7)
    paths = ""
    for p in data.get("paths", []):
        length = max(p["length"], 1)
        dash = min(35, 100 * 38 / length)                 # each signal is about 38 px long
        travel = length / 95                               # seconds to cross, at ~95 px per second
        cycle = travel / 0.4 + rnd.uniform(0, 3)
        paths += (f'<path class="ls" pathLength="100" d="{p["d"]}" style="stroke-dasharray:{dash:.1f} 300;'
                  f'--lt:{cycle:.1f}s;--ld:{rnd.uniform(0, 6):.1f}s"/>')
    pads = "".join(f'<circle class="lg" cx="{c["x"]}" cy="{c["y"]}" r="{c["r"]}" style="--ld:{i * 1.7:.1f}s"/>'
                   for i, c in enumerate(data.get("pads", [])))
    return (f'<svg class="logo-signals" viewBox="0 0 {data["width"]} {data["height"]}" aria-hidden="true" focusable="false">'
            f'{paths}{pads}</svg>')


def dept_html(d):
    """A department line: plain text, or {name, url} to make it a link."""
    if isinstance(d, dict):
        name = esc(d.get("name", ""))
        return f'<a href="{esc(web(d["url"]))}">{name}</a>' if d.get("url") else name
    return esc(d)


BEE_SVG = ('<svg viewBox="0 0 40 32" focusable="false">'
           '<ellipse class="bee-wing w1" cx="17" cy="9" rx="6" ry="8" transform="rotate(-25 17 9)"/>'
           '<ellipse class="bee-wing w2" cx="24" cy="9" rx="5" ry="7" transform="rotate(20 24 9)"/>'
           '<ellipse class="bee-body" cx="20" cy="19" rx="11" ry="7.5"/>'
           '<path class="bee-band" d="M15 12.5 Q13.5 19 15 25.5 M21 11.6 Q19.5 19 21 26.4"/>'
           '<circle class="bee-head" cx="32" cy="18" r="4.5"/><circle class="bee-eye" cx="33.5" cy="17" r="1"/>'
           '<path class="bee-line" d="M34 14 Q36 9 39 8 M9 19 L5 19"/></svg>')


def credits_html():
    """Small "Site credits" corner in the footer, from content/credits.yml."""
    f = CONTENT / "credits.yml"
    if not f.exists():
        return ""
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        warn(f, f"could not read the credits file ({e}); check the indentation.")
        return ""
    rows = ""
    for c in data.get("credits") or []:
        if not isinstance(c, dict) or not c.get("what") or not c.get("who"):
            continue
        # Several names and links can share one entry:
        #   who: OpenAlex, Crossref        (or a list)
        #   link: https://openalex.org, https://www.crossref.org
        def as_list(v):
            return [x.strip() for x in (v if isinstance(v, list) else str(v).split(",")) if str(x).strip()]
        links = as_list(c["link"]) if c.get("link") else []
        names = as_list(c["who"]) if len(links) > 1 or isinstance(c["who"], list) else [str(c["who"])]
        if len(links) > 1 and len(links) == len(names):
            who = ", ".join(f'<a href="{esc(web(l))}">{esc(n)}</a>' for n, l in zip(names, links))
        elif links:
            who = f'<a href="{esc(web(links[0]))}">{esc(", ".join(names))}</a>'
        else:
            who = esc(", ".join(names))
        if c.get("bee"):    # a tiny bee lives at the corner of this name and goes out for short flights
            who = f'<span class="bee-home">{who}<span class="bee" aria-hidden="true">{BEE_SVG}</span></span>'
        rows += f'<div class="cr-row"><dt>{esc(c["what"])}</dt><dd>{who}</dd></div>'
    if not rows:
        return ""
    return (f'<aside class="credits" aria-labelledby="credits-h"><h2 id="credits-h">Site credits</h2>'
            f'<dl>{rows}</dl></aside>')


def build_research(areas, pubs_by_doi):
    meta, body = read_md(CONTENT / "research.md") or ({}, "")
    out = []
    for path, m, b, slug in areas:
        f, cap, credit, fdoi = pick_figure(m)
        fig = figure_html(f, path, cap) if f else ""
        fig_html = ""
        if fig:
            if fdoi:
                url, _ = doi_url(fdoi)
                fig = f'<a class="fig-link" href="{esc(url)}" aria-label="Read the paper this figure comes from">{fig}</a>'
            parts = [esc(cap)] if cap else []
            if credit:
                parts.append(f'<span class="credit">{esc(credit)}</span>')
            fig_html = f'<figure class="area-fig">{fig}{"<figcaption>" + " ".join(parts) + "</figcaption>" if parts else ""}</figure>'
        covers = list(m.get("covers") or [])
        listed = {doi_url(c.get("doi", ""))[1].lower() for c in covers if isinstance(c, dict)}
        area_dois = {doi_url(d)[1].lower() for d in (m.get("papers") or [])}
        covers += [{"image": c["image"], "doi": c["doi"], "label": c.get("label", "")}
                   for c in AUTO_COVERS if c["doi"] in area_dois and c["doi"] not in listed]
        extra = collaborators_html(m.get("collaborators")) + key_papers_html(m.get("papers"), pubs_by_doi, path)
        cov = covers_html(covers, pubs_by_doi, path)
        if fig_html:   # covers sit in the figure column, beside the key papers
            side = f'<div class="area-side">{fig_html}{cov}</div>'
        else:
            extra += cov
            side = ""
        out.append(f'<article class="area{" has-fig" if fig else ""}" id="area-{esc(slug)}">'
                   f'<div class="area-text"><h3>{esc(m["title"])}</h3>{md(b)}{extra}</div>{side}</article>')
    return f'''
  <section class="page" id="research">
    <h2>{esc(meta.get("title", "Research"))}</h2>
    <div class="page-intro">{md(body)}</div>
    {"".join(out)}
  </section>'''


PROFILE_FIELDS = [  # (field in the person's file, link label, how to turn the value into a web address)
    ("orcid", "ORCID", lambda v: v if v.startswith("http") else "https://orcid.org/" + v),
    ("scholar", "Google Scholar", lambda v: v),
    ("researchgate", "ResearchGate", lambda v: v),
    ("linkedin", "LinkedIn", lambda v: v),
    ("website", "Website", lambda v: v),
]


def web(url):
    """Accept 'www.linkedin.com/in/x' as well as full addresses."""
    url = str(url or "").strip()
    if url and not re.match(r"^(https?:|mailto:|#|/|images/|theses/)", url, re.I) and "." in url.split("/")[0]:
        url = "https://" + url
    return url


def person_links(m, auto_orcid=""):
    links = []
    if m.get("email"):
        links.append(f'<a href="mailto:{esc(m["email"])}">{esc(m["email"])}</a>')
    seen = set()
    for field, label, to_url in PROFILE_FIELDS:
        value = str(m.get(field) or (auto_orcid if field == "orcid" else "") or "").strip()
        if value:
            url = web(to_url(value))
            seen.add(url)
            links.append(f'<a href="{esc(url)}" aria-label="{esc(label)} profile of {esc(m.get("name", ""))}">{esc(label)}</a>')
    for l in m.get("links") or []:
        if isinstance(l, dict) and l.get("url") and web(l["url"]) not in seen:
            links.append(f'<a href="{esc(web(l["url"]))}">{esc(l.get("label", l["url"]))}</a>')
    return " · ".join(links)


def orcid_from_papers(m, papers):
    """The ORCID recorded for this person on their papers, if every match agrees."""
    keys = {name_keys(m["name"])} | {name_keys(a) for a in (m.get("published_as") or [])}
    found = set()
    for p in papers:
        for a, o in zip(p.get("authors", []), p.get("orcids", []) or []):
            k = name_keys(a)
            if o and k and any(k[0] == s and k[1] == f for s, f in keys if s):
                found.add(o)
    return found.pop() if len(found) == 1 else ""


def alumni_category(m):
    deg = re.sub(r"[^a-z]", "", str(m.get("degree", "")).lower())
    if deg in ("phd", "doctorate"):
        return "phd"
    if deg in ("ms", "ma", "msc", "masters", "master"):
        return "ms"
    if deg in ("bs", "ba", "bsc", "undergrad", "undergraduate"):
        return "undergrad"
    if deg in ("postdoc", "postdoctoral"):
        return "postdoc"
    g = str(m.get("group", "")).strip().lower()
    return {"postdoc": "postdoc", "grad": "grad", "undergrad": "undergrad", "staff": "staff"}.get(g, "staff")


def plain(text):
    """Lowercase, no accents, normal hyphens: 'Córdova' -> 'cordova', 'Schulze–Fiehn' -> 'schulze-fiehn'."""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[\u2010-\u2015]", "-", text).lower()


def name_keys(name):
    """(surname, first initial) for matching an author name like 'Adam H. Kensinger'."""
    name = re.sub(r"\(.*?\)", " ", re.sub(r"[\u2010-\u2015]", "-", str(name)))   # drop '(Clifford)'
    name = re.sub(r"^(dr|prof)\.?\s+", "", plain(name).strip())
    words = [w.strip(".,") for w in re.sub(r",.*$", "", name).split() if w.strip(".,")]
    return (words[-1], words[0][0]) if words else None


def is_paper(p):
    """Journal papers only. Meeting abstracts (pages like '359a', FASEB '.s1.' supplements,
    'Abstracts of papers'), and records without a journal or DOI are listed separately."""
    pages = str(p.get("pages", "")).strip()
    doi = str(p.get("doi", "")).lower()
    venue = str(p.get("venue", "")).lower()
    if not doi or not venue:
        return False
    if p.get("type") and p["type"] not in ("article", "review", "letter", "book-chapter"):
        return False          # meeting abstracts, preprints, dissertations, reports ...
    if re.search(r"rxiv|research square|preprints\.org|ssrn", venue):
        return False
    if re.fullmatch(r"\d+a(\s*[–-]\s*\d+a)?", pages):
        return False
    if re.search(r"\.s\d+\.|/s\d+\.|supplement", doi) or "abstracts of papers" in venue or "osti" in venue:
        return False
    return True


def papers_for(m, pubs, pi_keys):
    keys = {name_keys(m["name"])}
    for alt in m.get("published_as") or []:
        keys.add(name_keys(alt))
    keys.discard(None)
    keys -= pi_keys
    found = []
    for p in pubs:
        if not is_paper(p):
            continue
        for a in p.get("authors", []):
            k = name_keys(a)
            if k and any(k[0] == s and k[1] == f for s, f in keys):
                found.append(p)
                break
    return found


def years_text(m):
    start, end = str(m.get("start") or "").strip(), str(m.get("end") or "").strip()
    if start and end:
        return f"{start}–{end}"
    return end or start or str(m.get("years") or "").strip()


def load_dissertations():
    f = ROOT / "data" / "dissertations.json"
    return json.loads(f.read_text(encoding="utf-8")).get("dissertations", []) if f.exists() else []


def dissertation_for(m, dissertations):
    """The archive record whose author matches this person ('Last, First' in the archive)."""
    keys = {name_keys(m["name"])} | {name_keys(a) for a in (m.get("published_as") or [])}
    for d in dissertations:
        a = str(d.get("author", ""))
        if "," in a:
            last, first = a.split(",", 1)
            a = f"{first.strip()} {last.strip()}"
        k = name_keys(a)
        if k and k in keys:
            return d
    return None


def alumni_html(alumni, pubs, pi_keys):
    """Past members: one tab per category; each person is a row that expands for details."""
    if not alumni:
        return ""
    cats = {k: [] for k, _ in ALUMNI_CATEGORIES}
    for path, m, b in alumni:
        cats[alumni_category(m)].append((path, m, b))

    def sort_key(x):
        e = re.findall(r"\d{4}", str(x[1].get("end") or x[1].get("years") or ""))
        return (-(int(e[-1]) if e else 0), x[1]["name"])

    tabs, panels = [], []
    for key, heading in ALUMNI_CATEGORIES:
        if not cats[key]:
            continue
        rows = []
        for path, m, b in sorted(cats[key], key=sort_key):
            year = esc(str(m.get("end") or years_text(m) or ""))
            now = f'<span class="al-now">{esc(m["now"])}</span>' if m.get("now") else ""
            summary = (f'<summary><span class="al-name">{esc(m["name"])}</span>'
                       f'<span class="al-year">{year}</span>{now}</summary>')
            facts = []
            if key in ("phd", "ms"):
                when = str(m.get("defended") or "")
                if when and when != str(m.get("end")):
                    facts.append(f"Defended {esc(when)}")
            elif m.get("major") and m["major"] != "Chemistry B.S.":
                facts.append(esc(m["major"]))
            if m.get("co_advisor"):
                facts.append(f'Co-advised with {esc(m["co_advisor"])}')
            facts = [f for f in facts if f]
            thesis = ""
            local_pdf = ROOT / "theses" / (path.stem + ".pdf")
            if not m.get("thesis_url") and local_pdf.is_file():
                m = dict(m)
                m["thesis_url"] = f"theses/{path.stem}.pdf"
            elif m.get("thesis_url") and not re.match(r"^https?:", str(m["thesis_url"])) and not str(m["thesis_url"]).startswith("www."):
                if not (ROOT / str(m["thesis_url"])).is_file():
                    warn(path, f"thesis file '{m['thesis_url']}' not found; upload it to the theses folder.")
            if not m.get("thesis_url") or not m.get("thesis"):
                d = dissertation_for(m, DISSERTATIONS)
                if d:
                    m = dict(m)
                    m.setdefault("thesis_url", d["url"])
                    if not m.get("thesis_url"):
                        m["thesis_url"] = d["url"]
                    if not m.get("thesis"):
                        m["thesis"] = d.get("title", "")
            if m.get("thesis"):
                label = esc(m.get("thesis_type") or ("Dissertation" if key == "phd" else "Thesis"))
                t = f'&ldquo;{esc(m["thesis"])}&rdquo;'
                if m.get("thesis_url"):
                    t = f'<a href="{esc(web(m["thesis_url"]))}">{t}</a>'
                thesis = f'<p class="al-thesis"><span class="al-label">{label}</span> {t}</p>'
            story = md(b) if b else ""
            ps = [] if m.get("published_as") is False else papers_for(m, pubs, pi_keys)
            links = person_links(m, orcid_from_papers(m, ps))
            if m.get("published_as"):
                facts.append("Published as " + ", ".join(esc(x) for x in m["published_as"]))
            papers = ""
            if ps:
                lis = "".join(
                    (f'<li><a href="{esc(p["doi"])}">{esc(p["title"])}</a>' if p.get("doi") else f'<li>{esc(p["title"])}')
                    + f' <span class="src">{esc(p.get("venue", ""))}{", " if p.get("venue") else ""}{esc(p["year"])}</span></li>'
                    for p in ps)
                papers = f'<div class="al-papers"><span class="al-label">Papers</span><ul>{lis}</ul></div>'
            body = (f'<div class="al-body">'
                    f'{"<p class=al-facts>" + " &nbsp;|&nbsp; ".join(facts) + "</p>" if facts else ""}'
                    f'{thesis}{story}{f"<p>{links}</p>" if links else ""}{papers}</div>')
            rows.append(f'<li><details>{summary}{body}</details></li>')
        tab_id = f"past-{key}"
        tabs.append(f'<button type="button" role="tab" id="tab-{tab_id}" aria-controls="{tab_id}" aria-selected="false">'
                    f'{esc(heading)}</button>')
        panels.append(f'<div class="past-panel" role="tabpanel" id="{tab_id}" aria-labelledby="tab-{tab_id}">'
                      f'<h4 class="al-cat">{esc(heading)}</h4><ul class="alumni-list">{"".join(rows)}</ul></div>')
    tablist = f'<div class="past-tabs" role="tablist" aria-label="Past members by degree">{"".join(tabs)}</div>' if len(tabs) > 1 else ""
    return (f'<section class="past" aria-labelledby="past-title"><h3 class="past-title" id="past-title">Past members</h3>'
            f'{tablist}{"".join(panels)}</section>')


def dogs_html(m, path):
    """A small, slowly changing portrait of the PI's dogs under his photo (front matter "dogs:").
    The portraits fade from one dog to the next every few seconds; they stand still for visitors
    who ask their device for reduced motion."""
    art = json.loads((ROOT / "templates" / "dog-art.json").read_text(encoding="utf-8"))
    entries = m.get("dogs") or []
    if entries == "illustrated":                  # drawings of common rescue-dog types, no photos needed
        entries = [{"art": k} for k in art]
    dogs = []
    for d in entries:
        d = d if isinstance(d, dict) else {"image": d}
        name = str(d.get("name", "")).strip()
        if d.get("art") in art:
            dogs.append(("art", art[d["art"]]["svg"], name, art[d["art"]]["label"]))
            continue
        img = image(d.get("image"), path)
        if img:
            dogs.append(("photo", web_copy(img, 500), name, ""))
    if not dogs:
        return ""
    frames = ""
    for i, (kind, src, name, kind_label) in enumerate(dogs):
        pic = (src.replace('aria-hidden="true"', f'aria-label="{esc(name or kind_label)}"') if kind == "art"
               else f'<img src="{esc(src)}" alt="{esc(name or "One of the lab dogs")}" loading="lazy">')
        frames += f'<figure class="dog{" is-art" if kind == "art" else ""}">{pic}</figure>'
    label = m.get("dogs_label") or "The lab's rescue dogs"
    return f'<div class="dogs" role="img" aria-label="{esc(label)}"><div class="dog-frame">{frames}</div></div>'


def build_people(people, alumni, pubs):
    pi_html, groups = "", {g: [] for g, _ in PEOPLE_GROUPS}
    pi_keys = set()
    for path, m, b in people:
        g = str(m.get("group", "")).strip().lower()
        photo = image(m.get("photo"), path)
        if g == "pi":
            pi_keys.add(name_keys(m["name"]))
            img = (f'<img class="photo" src="{esc(photo)}" alt="{esc(m["name"])}" style="aspect-ratio:4/5">' if photo
                   else f'<div class="photo" aria-hidden="true" style="aspect-ratio:4/5">{initials(m["name"])}</div>')
            role = "<br>".join(esc(x) for x in str(m.get("role", "")).strip().splitlines())
            links = person_links(m)
            pi_html += f'''
    <div class="pi">
      <div class="pi-side">{img}{dogs_html(m, path)}</div>
      <div>
        <h3>{esc(m["name"])}</h3>
        <p class="role">{role}</p>
        {md(b)}
        {f"<p>{links}</p>" if links else ""}
      </div>
    </div>'''
        elif g == "alumni":
            alumni.append((path, m, b))  # older style: alumni marked in the main folder
        elif g in groups:
            img = (f'<img class="photo" src="{esc(photo)}" alt="{esc(m["name"])}">' if photo
                   else f'<div class="photo" aria-hidden="true">{initials(m["name"])}</div>')
            role = esc(m.get("role", ""))
            since = f' <span class="since">(joined {esc(m["start"])})</span>' if m.get("start") else ""
            proj = f'<p class="role">{esc(m["project"])}</p>' if m.get("project") else ""
            bio = f'<div class="bio">{md(b)}</div>' if b else ""
            links = person_links(m)
            groups[g].append((m.get("order", 99), m["name"], f'''
        <div class="person">{img}<h4>{esc(m["name"])}</h4><p class="role">{role}{since}</p>{proj}{bio}{f'<p class="role">{links}</p>' if links else ""}</div>'''))
        else:
            warn(path, f"skipped: 'group:' must be one of pi, postdoc, staff, grad, undergrad (found '{g}').")

    out = [pi_html]
    for g, label in PEOPLE_GROUPS:
        if groups[g]:
            cards = "".join(c for _, _, c in sorted(groups[g], key=lambda x: (x[0], x[1])))
            out.append(f'<p class="group-label">{label}</p><div class="people">{cards}</div>')
    out.append(alumni_html(alumni, pubs, pi_keys))
    return f'''
  <section class="page" id="people">
    <h2>People</h2>
    {"".join(out)}
  </section>'''


def name_matchers(people, config):
    """Who gets bolded in author lists: everyone with a people file, plus highlight_names."""
    matchers = []
    for _, m, _ in people:
        words = [w.strip(".").lower() for w in re.sub(r",.*$", "", m["name"]).split()]
        if words:
            matchers.append((words[-1], words[0][0]))
    for n in config.get("highlight_names", []):
        matchers.append((n.lower(), ""))
    return matchers


def is_member(author, matchers):
    words = [w.strip(".,").lower() for w in author.replace(",", " ").split()]
    if not words:
        return False
    for surname, first in matchers:
        if surname in words and (not first or any(w.startswith(first) for w in words if w != surname)):
            return True
    return False


def cite(p, matchers):
    authors = ", ".join(f"<b>{esc(a)}</b>" if is_member(a, matchers) else esc(a) for a in p.get("authors", []))
    c = f'<span class="t">{esc(p["title"])}</span>{authors}. '
    if p.get("venue"):
        c += f"<i>{esc(p['venue'])}</i> "
    c += esc(p["year"])
    if p.get("volume"):
        c += f", {esc(p['volume'])}"
    if p.get("pages"):
        c += f", {esc(p['pages'])}"
    c += "."
    if p.get("doi"):
        c += f' <a class="doi" href="{esc(p["doi"])}">doi:{esc(doi_url(p["doi"])[1])}</a>'
        if doi_url(p["doi"])[1].lower() in SHOWN_COVERS:
            c += ' <span class="cover-badge">Cover</span>'
    return f"<li>{c}</li>"


def register_auto_covers(pubs):
    """Automatic covers that are not under a research area still go in the gallery."""
    by_doi = {doi_url(p["doi"])[1].lower(): p for p in pubs if p.get("doi")}
    for c in AUTO_COVERS:
        p = by_doi.get(c["doi"])
        label = c.get("label") or (f'{p.get("venue", "")}, {p.get("year", "")}' if p else "")
        SHOWN_COVERS.setdefault(c["doi"], (web_copy(c["image"]), label, "", web(c.get("source", ""))))
    return ""


def covers_gallery():
    if not SHOWN_COVERS:
        return ""
    cards = "".join(cover_card(img, label, backup, parent, f"https://doi.org/{d}")
                    for d, (img, label, backup, parent) in SHOWN_COVERS.items())
    return f'<div class="covers pub-covers"><h3>Journal covers</h3><div class="cover-row">{cards}</div></div>'


def build_publications(all_pubs, matchers):
    pubs = [p for p in all_pubs if is_paper(p)]
    other = [p for p in all_pubs if not is_paper(p)]
    by_year = {}
    for p in pubs:
        by_year.setdefault(p["year"], []).append(p)
    years = sorted(by_year, reverse=True)
    if not years:
        return '<section class="page" id="publications"><h2>Publications</h2><p>Coming soon.</p></section>'
    cutoff = years[0] - RECENT_YEARS + 1
    blocks, earlier = [], 0
    for y in years:
        lis = [cite(p, matchers) for p in by_year[y]]
        is_earlier = y < cutoff
        earlier += len(by_year[y]) if is_earlier else 0
        blocks.append(f'<div class="yr{" earlier" if is_earlier else ""}"><span>{y}</span><ol class="pubs">{"".join(lis)}</ol></div>')
    button = ""
    if earlier:
        button = (f'<p><button type="button" id="pub-more" class="more-btn" aria-expanded="false" '
                  f'data-show="Show {earlier} earlier publications ({years[-1]}–{cutoff - 1})" '
                  f'data-hide="Hide earlier publications">Show {earlier} earlier publications ({years[-1]}–{cutoff - 1})</button></p>')
    return f'''
  <section class="page" id="publications">
    <h2>Publications</h2>
    <p class="note">Group members are shown in bold.</p>
    {covers_gallery()}
    <div id="pub-list">{"".join(blocks)}</div>
    {button}
    {f'<details class="other-pubs"><summary>Conference abstracts and other items</summary><ol class="pubs">{"".join(cite(p, matchers) for p in other)}</ol></details>' if other else ""}
  </section>'''


THEORY_ICONS = {
    # small line drawings for the exploration cards; the gold line moves gently (still for reduced motion)
    "hills": ('<svg viewBox="0 0 64 40" aria-hidden="true"><g class="ti-k" style="--k:0"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.16 3.5 7.22 4 8.25 4.5 9.27 5 10.27 5.5 11.25 6 12.21 6.5 13.16 7 14.08 7.5 14.99 8 15.88 8.5 16.75 9 17.60 9.5 18.44 10 19.25 10.5 20.05 11 20.83 11.5 21.59 12 22.33 12.5 23.06 13 23.76 13.5 24.45 14 25.12 14.5 25.77 15 26.40 15.5 27.02 16 27.61 16.5 28.19 17 28.74 17.5 29.28 18 29.79 18.5 30.29 19 30.76 19.5 31.21 20 31.64 20.5 32.03 21 32.40 21.5 32.74 22 33.04 22.5 33.30 23 33.53 23.5 33.71 24 33.84 24.5 33.92 25 33.96 25.5 33.94 26 33.88 26.5 33.77 27 33.62 27.5 33.44 28 33.24 28.5 33.02 29 32.80 29.5 32.58 30 32.39 30.5 32.23 31 32.10 31.5 32.03 32 32.00 32.5 32.03 33 32.10 33.5 32.23 34 32.39 34.5 32.58 35 32.80 35.5 33.02 36 33.24 36.5 33.44 37 33.62 37.5 33.77 38 33.88 38.5 33.94 39 33.96 39.5 33.92 40 33.84 40.5 33.71 41 33.53 41.5 33.30 42 33.04 42.5 32.74 43 32.40 43.5 32.03 44 31.64 44.5 31.21 45 30.76 45.5 30.29 46 29.79 46.5 29.28 47 28.74 47.5 28.19 48 27.61 48.5 27.02 49 26.40 49.5 25.77 50 25.12 50.5 24.45 51 23.76 51.5 23.06 52 22.33 52.5 21.59 53 20.83 53.5 20.05 54 19.25 54.5 18.44 55 17.60 55.5 16.75 56 15.88 56.5 14.99 57 14.08 57.5 13.16 58 12.21 58.5 11.25 59 10.27 59.5 9.27 60 8.25 60.5 7.22 61 6.16 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M19.5 31.21 L20 31.64 L20.5 32.03 L21 32.40 L21.5 32.74 L22 33.04 L22.5 33.30 L23 33.53 L23.5 33.71 L24 33.84 L24.5 33.92 L25 33.96 L25.5 33.94 L26 33.88 L26.5 33.77 L27 33.62 L27.5 33.44 L28 33.24 L28.5 33.02 L29 32.80 L29.5 32.58 L30 32.39 L30.5 32.23 L31 32.10 L31.5 32.03 L32 32.00 L32.5 32.03 L33 32.10 L33.5 32.23 L34 32.39 L34.5 32.58 L35 32.80 L35.5 33.02 L36 33.24 L36.5 33.44 L37 33.62 L37.5 33.77 L38 33.88 L38.5 33.94 L39 33.96 L39.5 33.92 L40 33.84 L40.5 33.71 L41 33.53 L41.5 33.30 L42 33.04 L42.5 32.74 L43 32.40 L43.5 32.03 L44 31.64 L44.5 31.21"/><circle class="ti-walker" cx="30.8" cy="29.8" r="2.3"/></g><g class="ti-k" style="--k:1"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.16 3.5 7.22 4 8.25 4.5 9.27 5 10.27 5.5 11.25 6 12.21 6.5 13.16 7 14.08 7.5 14.99 8 15.88 8.5 16.75 9 17.60 9.5 18.44 10 19.25 10.5 20.05 11 20.82 11.5 21.58 12 22.31 12.5 23.03 13 23.72 13.5 24.39 14 25.04 14.5 25.65 15 26.24 15.5 26.80 16 27.32 16.5 27.80 17 28.24 17.5 28.63 18 28.98 18.5 29.27 19 29.52 19.5 29.70 20 29.83 20.5 29.91 21 29.94 21.5 29.92 22 29.86 22.5 29.77 23 29.65 23.5 29.52 24 29.37 24.5 29.23 25 29.10 25.5 28.98 26 28.88 26.5 28.80 27 28.76 27.5 28.75 28 28.77 28.5 28.83 29 28.92 29.5 29.05 30 29.21 30.5 29.41 31 29.64 31.5 29.90 32 30.20 32.5 30.52 33 30.86 33.5 31.21 34 31.57 34.5 31.94 35 32.29 35.5 32.63 36 32.94 36.5 33.22 37 33.46 37.5 33.65 38 33.79 38.5 33.88 39 33.92 39.5 33.89 40 33.82 40.5 33.69 41 33.52 41.5 33.30 42 33.04 42.5 32.74 43 32.40 43.5 32.03 44 31.64 44.5 31.21 45 30.76 45.5 30.29 46 29.79 46.5 29.28 47 28.74 47.5 28.19 48 27.61 48.5 27.02 49 26.40 49.5 25.77 50 25.12 50.5 24.45 51 23.76 51.5 23.06 52 22.33 52.5 21.59 53 20.83 53.5 20.05 54 19.25 54.5 18.44 55 17.60 55.5 16.75 56 15.88 56.5 14.99 57 14.08 57.5 13.16 58 12.21 58.5 11.25 59 10.27 59.5 9.27 60 8.25 60.5 7.22 61 6.16 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M13.5 24.39 L14 25.04 L14.5 25.65 L15 26.24 L15.5 26.80 L16 27.32 L16.5 27.80 L17 28.24 L17.5 28.63 L18 28.98 L18.5 29.27 L19 29.52 L19.5 29.70 L20 29.83 L20.5 29.91 L21 29.94 L21.5 29.92 L22 29.86 L22.5 29.77 L23 29.65 L23.5 29.52 L24 29.37 L24.5 29.23 L25 29.10 L25.5 28.98 L26 28.88 L26.5 28.80 L27 28.76 L27.5 28.75 L28 28.77 L28.5 28.83 L29 28.92 L29.5 29.05 L30 29.21 L30.5 29.41 L31 29.64 L31.5 29.90 L32 30.20 L32.5 30.52 L33 30.86 L33.5 31.21 L34 31.57 L34.5 31.94 L35 32.29 L35.5 32.63 L36 32.94 L36.5 33.22 L37 33.46 L37.5 33.65 L38 33.79 L38.5 33.88"/><circle class="ti-walker" cx="34.4" cy="29.6" r="2.3"/></g><g class="ti-k" style="--k:2"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.16 3.5 7.22 4 8.25 4.5 9.27 5 10.27 5.5 11.25 6 12.21 6.5 13.16 7 14.08 7.5 14.99 8 15.88 8.5 16.75 9 17.60 9.5 18.44 10 19.25 10.5 20.05 11 20.82 11.5 21.58 12 22.31 12.5 23.03 13 23.72 13.5 24.39 14 25.04 14.5 25.65 15 26.24 15.5 26.80 16 27.32 16.5 27.80 17 28.24 17.5 28.63 18 28.98 18.5 29.27 19 29.51 19.5 29.70 20 29.83 20.5 29.91 21 29.94 21.5 29.92 22 29.86 22.5 29.76 23 29.64 23.5 29.50 24 29.35 24.5 29.20 25 29.05 25.5 28.92 26 28.79 26.5 28.69 27 28.60 27.5 28.53 28 28.48 28.5 28.44 29 28.42 29.5 28.40 30 28.40 30.5 28.39 31 28.39 31.5 28.40 32 28.40 32.5 28.40 33 28.39 33.5 28.39 34 28.40 34.5 28.40 35 28.42 35.5 28.44 36 28.48 36.5 28.53 37 28.60 37.5 28.69 38 28.79 38.5 28.92 39 29.05 39.5 29.20 40 29.35 40.5 29.50 41 29.64 41.5 29.76 42 29.86 42.5 29.92 43 29.94 43.5 29.91 44 29.83 44.5 29.70 45 29.51 45.5 29.27 46 28.98 46.5 28.63 47 28.24 47.5 27.80 48 27.32 48.5 26.80 49 26.24 49.5 25.65 50 25.04 50.5 24.39 51 23.72 51.5 23.03 52 22.31 52.5 21.58 53 20.82 53.5 20.05 54 19.25 54.5 18.44 55 17.60 55.5 16.75 56 15.88 56.5 14.99 57 14.08 57.5 13.16 58 12.21 58.5 11.25 59 10.27 59.5 9.27 60 8.25 60.5 7.22 61 6.16 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M25.5 28.92 L26 28.79 L26.5 28.69 L27 28.60 L27.5 28.53 L28 28.48 L28.5 28.44 L29 28.42 L29.5 28.40 L30 28.40 L30.5 28.39 L31 28.39 L31.5 28.40 L32 28.40 L32.5 28.40 L33 28.39 L33.5 28.39 L34 28.40 L34.5 28.40 L35 28.42 L35.5 28.44 L36 28.48 L36.5 28.53 L37 28.60 L37.5 28.69 L38 28.79 L38.5 28.92 L39 29.05 L39.5 29.20 L40 29.35 L40.5 29.50 L41 29.64 L41.5 29.76 L42 29.86 L42.5 29.92 L43 29.94 L43.5 29.91 L44 29.83 L44.5 29.70 L45 29.51 L45.5 29.27 L46 28.98 L46.5 28.63 L47 28.24 L47.5 27.80 L48 27.32 L48.5 26.80 L49 26.24 L49.5 25.65 L50 25.04 L50.5 24.39"/><circle class="ti-walker" cx="28.4" cy="26.1" r="2.3"/></g><g class="ti-k" style="--k:3"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.16 3.5 7.22 4 8.25 4.5 9.27 5 10.27 5.5 11.25 6 12.21 6.5 13.16 7 14.08 7.5 14.99 8 15.88 8.5 16.75 9 17.60 9.5 18.44 10 19.25 10.5 20.05 11 20.82 11.5 21.58 12 22.31 12.5 23.03 13 23.72 13.5 24.39 14 25.03 14.5 25.64 15 26.22 15.5 26.77 16 27.27 16.5 27.74 17 28.15 17.5 28.52 18 28.82 18.5 29.05 19 29.22 19.5 29.31 20 29.33 20.5 29.27 21 29.12 21.5 28.90 22 28.61 22.5 28.26 23 27.84 23.5 27.38 24 26.89 24.5 26.39 25 25.88 25.5 25.38 26 24.92 26.5 24.50 27 24.14 27.5 23.84 28 23.62 28.5 23.48 29 23.42 29.5 23.44 30 23.54 30.5 23.70 31 23.93 31.5 24.21 32 24.52 32.5 24.86 33 25.22 33.5 25.58 34 25.94 34.5 26.28 35 26.62 35.5 26.93 36 27.23 36.5 27.52 37 27.78 37.5 28.04 38 28.29 38.5 28.53 39 28.76 39.5 28.98 40 29.19 40.5 29.39 41 29.56 41.5 29.71 42 29.82 42.5 29.89 43 29.92 43.5 29.90 44 29.82 44.5 29.70 45 29.51 45.5 29.27 46 28.98 46.5 28.63 47 28.24 47.5 27.80 48 27.32 48.5 26.80 49 26.24 49.5 25.65 50 25.04 50.5 24.39 51 23.72 51.5 23.03 52 22.31 52.5 21.58 53 20.82 53.5 20.05 54 19.25 54.5 18.44 55 17.60 55.5 16.75 56 15.88 56.5 14.99 57 14.08 57.5 13.16 58 12.21 58.5 11.25 59 10.27 59.5 9.27 60 8.25 60.5 7.22 61 6.16 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M16.5 27.74 L17 28.15 L17.5 28.52 L18 28.82 L18.5 29.05 L19 29.22 L19.5 29.31 L20 29.33 L20.5 29.27 L21 29.12 L21.5 28.90 L22 28.61 L22.5 28.26 L23 27.84 L23.5 27.38 L24 26.89 L24.5 26.39 L25 25.88 L25.5 25.38 L26 24.92 L26.5 24.50 L27 24.14 L27.5 23.84 L28 23.62 L28.5 23.48 L29 23.42 L29.5 23.44 L30 23.54 L30.5 23.70 L31 23.93 L31.5 24.21 L32 24.52 L32.5 24.86 L33 25.22 L33.5 25.58 L34 25.94 L34.5 26.28 L35 26.62 L35.5 26.93 L36 27.23 L36.5 27.52 L37 27.78 L37.5 28.04 L38 28.29 L38.5 28.53 L39 28.76 L39.5 28.98 L40 29.19 L40.5 29.39 L41 29.56 L41.5 29.71"/><circle class="ti-walker" cx="36.8" cy="25.4" r="2.3"/></g><g class="ti-k" style="--k:4"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.16 3.5 7.22 4 8.25 4.5 9.27 5 10.27 5.5 11.25 6 12.21 6.5 13.16 7 14.08 7.5 14.99 8 15.88 8.5 16.75 9 17.60 9.5 18.44 10 19.25 10.5 20.05 11 20.82 11.5 21.58 12 22.31 12.5 23.03 13 23.72 13.5 24.39 14 25.03 14.5 25.64 15 26.22 15.5 26.77 16 27.27 16.5 27.74 17 28.15 17.5 28.51 18 28.82 18.5 29.05 19 29.22 19.5 29.31 20 29.32 20.5 29.25 21 29.10 21.5 28.88 22 28.57 22.5 28.20 23 27.76 23.5 27.27 24 26.73 24.5 26.17 25 25.58 25.5 25.00 26 24.42 26.5 23.85 27 23.32 27.5 22.82 28 22.37 28.5 21.97 29 21.62 29.5 21.32 30 21.08 30.5 20.89 31 20.75 31.5 20.67 32 20.65 32.5 20.67 33 20.75 33.5 20.89 34 21.08 34.5 21.32 35 21.62 35.5 21.97 36 22.37 36.5 22.82 37 23.32 37.5 23.85 38 24.42 38.5 25.00 39 25.58 39.5 26.17 40 26.73 40.5 27.27 41 27.76 41.5 28.20 42 28.57 42.5 28.88 43 29.10 43.5 29.25 44 29.32 44.5 29.31 45 29.22 45.5 29.05 46 28.82 46.5 28.51 47 28.15 47.5 27.74 48 27.27 48.5 26.77 49 26.22 49.5 25.64 50 25.03 50.5 24.39 51 23.72 51.5 23.03 52 22.31 52.5 21.58 53 20.82 53.5 20.05 54 19.25 54.5 18.44 55 17.60 55.5 16.75 56 15.88 56.5 14.99 57 14.08 57.5 13.16 58 12.21 58.5 11.25 59 10.27 59.5 9.27 60 8.25 60.5 7.22 61 6.16 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M22.5 28.20 L23 27.76 L23.5 27.27 L24 26.73 L24.5 26.17 L25 25.58 L25.5 25.00 L26 24.42 L26.5 23.85 L27 23.32 L27.5 22.82 L28 22.37 L28.5 21.97 L29 21.62 L29.5 21.32 L30 21.08 L30.5 20.89 L31 20.75 L31.5 20.67 L32 20.65 L32.5 20.67 L33 20.75 L33.5 20.89 L34 21.08 L34.5 21.32 L35 21.62 L35.5 21.97 L36 22.37 L36.5 22.82 L37 23.32 L37.5 23.85 L38 24.42 L38.5 25.00 L39 25.58 L39.5 26.17 L40 26.73 L40.5 27.27 L41 27.76 L41.5 28.20 L42 28.57 L42.5 28.88 L43 29.10 L43.5 29.25 L44 29.32 L44.5 29.31 L45 29.22 L45.5 29.05 L46 28.82 L46.5 28.51 L47 28.15 L47.5 27.74"/><circle class="ti-walker" cx="26.0" cy="22.1" r="2.3"/></g><g class="ti-k" style="--k:5"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.16 3.5 7.22 4 8.25 4.5 9.27 5 10.27 5.5 11.25 6 12.21 6.5 13.15 7 14.07 7.5 14.98 8 15.86 8.5 16.72 9 17.56 9.5 18.38 10 19.17 10.5 19.93 11 20.66 11.5 21.36 12 22.02 12.5 22.64 13 23.21 13.5 23.74 14 24.21 14.5 24.62 15 24.97 15.5 25.26 16 25.47 16.5 25.62 17 25.69 17.5 25.70 18 25.64 18.5 25.52 19 25.34 19.5 25.12 20 24.86 20.5 24.56 21 24.24 21.5 23.91 22 23.57 22.5 23.23 23 22.90 23.5 22.57 24 22.27 24.5 21.98 25 21.71 25.5 21.46 26 21.24 26.5 21.04 27 20.86 27.5 20.70 28 20.57 28.5 20.46 29 20.37 29.5 20.30 30 20.26 30.5 20.24 31 20.25 31.5 20.29 32 20.35 32.5 20.45 33 20.59 33.5 20.77 34 20.99 34.5 21.26 35 21.57 35.5 21.94 36 22.35 36.5 22.81 37 23.31 37.5 23.85 38 24.41 38.5 24.99 39 25.58 39.5 26.17 40 26.73 40.5 27.27 41 27.76 41.5 28.20 42 28.57 42.5 28.88 43 29.10 43.5 29.25 44 29.32 44.5 29.31 45 29.22 45.5 29.05 46 28.82 46.5 28.51 47 28.15 47.5 27.74 48 27.27 48.5 26.77 49 26.22 49.5 25.64 50 25.03 50.5 24.39 51 23.72 51.5 23.03 52 22.31 52.5 21.58 53 20.82 53.5 20.05 54 19.25 54.5 18.44 55 17.60 55.5 16.75 56 15.88 56.5 14.99 57 14.08 57.5 13.16 58 12.21 58.5 11.25 59 10.27 59.5 9.27 60 8.25 60.5 7.22 61 6.16 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M9.5 18.38 L10 19.17 L10.5 19.93 L11 20.66 L11.5 21.36 L12 22.02 L12.5 22.64 L13 23.21 L13.5 23.74 L14 24.21 L14.5 24.62 L15 24.97 L15.5 25.26 L16 25.47 L16.5 25.62 L17 25.69 L17.5 25.70 L18 25.64 L18.5 25.52 L19 25.34 L19.5 25.12 L20 24.86 L20.5 24.56 L21 24.24 L21.5 23.91 L22 23.57 L22.5 23.23 L23 22.90 L23.5 22.57 L24 22.27 L24.5 21.98 L25 21.71 L25.5 21.46 L26 21.24 L26.5 21.04 L27 20.86 L27.5 20.70 L28 20.57 L28.5 20.46 L29 20.37 L29.5 20.30 L30 20.26 L30.5 20.24 L31 20.25 L31.5 20.29 L32 20.35 L32.5 20.45 L33 20.59 L33.5 20.77 L34 20.99 L34.5 21.26"/><circle class="ti-walker" cx="39.2" cy="23.5" r="2.3"/></g><g class="ti-k" style="--k:6"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.16 3.5 7.22 4 8.25 4.5 9.27 5 10.27 5.5 11.25 6 12.21 6.5 13.15 7 14.07 7.5 14.98 8 15.86 8.5 16.72 9 17.56 9.5 18.38 10 19.17 10.5 19.93 11 20.66 11.5 21.36 12 22.02 12.5 22.64 13 23.21 13.5 23.74 14 24.21 14.5 24.62 15 24.97 15.5 25.26 16 25.47 16.5 25.62 17 25.69 17.5 25.70 18 25.64 18.5 25.52 19 25.34 19.5 25.12 20 24.86 20.5 24.56 21 24.24 21.5 23.91 22 23.57 22.5 23.23 23 22.90 23.5 22.57 24 22.27 24.5 21.98 25 21.71 25.5 21.46 26 21.24 26.5 21.03 27 20.85 27.5 20.69 28 20.55 28.5 20.43 29 20.33 29.5 20.24 30 20.18 30.5 20.12 31 20.09 31.5 20.07 32 20.06 32.5 20.07 33 20.09 33.5 20.12 34 20.18 34.5 20.24 35 20.33 35.5 20.43 36 20.55 36.5 20.69 37 20.85 37.5 21.03 38 21.24 38.5 21.46 39 21.71 39.5 21.98 40 22.27 40.5 22.57 41 22.90 41.5 23.23 42 23.57 42.5 23.91 43 24.24 43.5 24.56 44 24.86 44.5 25.12 45 25.34 45.5 25.52 46 25.64 46.5 25.70 47 25.69 47.5 25.62 48 25.47 48.5 25.26 49 24.97 49.5 24.62 50 24.21 50.5 23.74 51 23.21 51.5 22.64 52 22.02 52.5 21.36 53 20.66 53.5 19.93 54 19.17 54.5 18.38 55 17.56 55.5 16.72 56 15.86 56.5 14.98 57 14.07 57.5 13.15 58 12.21 58.5 11.25 59 10.27 59.5 9.27 60 8.25 60.5 7.22 61 6.16 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M29.5 20.24 L30 20.18 L30.5 20.12 L31 20.09 L31.5 20.07 L32 20.06 L32.5 20.07 L33 20.09 L33.5 20.12 L34 20.18 L34.5 20.24 L35 20.33 L35.5 20.43 L36 20.55 L36.5 20.69 L37 20.85 L37.5 21.03 L38 21.24 L38.5 21.46 L39 21.71 L39.5 21.98 L40 22.27 L40.5 22.57 L41 22.90 L41.5 23.23 L42 23.57 L42.5 23.91 L43 24.24 L43.5 24.56 L44 24.86 L44.5 25.12 L45 25.34 L45.5 25.52 L46 25.64 L46.5 25.70 L47 25.69 L47.5 25.62 L48 25.47 L48.5 25.26 L49 24.97 L49.5 24.62 L50 24.21 L50.5 23.74 L51 23.21 L51.5 22.64 L52 22.02 L52.5 21.36 L53 20.66 L53.5 19.93 L54 19.17 L54.5 18.38"/><circle class="ti-walker" cx="23.6" cy="20.2" r="2.3"/></g><g class="ti-k" style="--k:7"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.16 3.5 7.22 4 8.25 4.5 9.27 5 10.27 5.5 11.25 6 12.21 6.5 13.15 7 14.07 7.5 14.98 8 15.86 8.5 16.72 9 17.56 9.5 18.38 10 19.17 10.5 19.93 11 20.66 11.5 21.36 12 22.02 12.5 22.64 13 23.21 13.5 23.74 14 24.21 14.5 24.62 15 24.97 15.5 25.26 16 25.47 16.5 25.61 17 25.68 17.5 25.68 18 25.62 18.5 25.49 19 25.30 19.5 25.06 20 24.77 20.5 24.44 21 24.08 21.5 23.69 22 23.28 22.5 22.84 23 22.39 23.5 21.93 24 21.45 24.5 20.96 25 20.46 25.5 19.95 26 19.43 26.5 18.91 27 18.39 27.5 17.87 28 17.37 28.5 16.90 29 16.45 29.5 16.06 30 15.71 30.5 15.43 31 15.23 31.5 15.10 32 15.06 32.5 15.10 33 15.23 33.5 15.43 34 15.71 34.5 16.06 35 16.45 35.5 16.90 36 17.37 36.5 17.87 37 18.39 37.5 18.91 38 19.43 38.5 19.95 39 20.46 39.5 20.96 40 21.45 40.5 21.93 41 22.39 41.5 22.84 42 23.28 42.5 23.69 43 24.08 43.5 24.44 44 24.77 44.5 25.06 45 25.30 45.5 25.49 46 25.62 46.5 25.68 47 25.68 47.5 25.61 48 25.47 48.5 25.26 49 24.97 49.5 24.62 50 24.21 50.5 23.74 51 23.21 51.5 22.64 52 22.02 52.5 21.36 53 20.66 53.5 19.93 54 19.17 54.5 18.38 55 17.56 55.5 16.72 56 15.86 56.5 14.98 57 14.07 57.5 13.15 58 12.21 58.5 11.25 59 10.27 59.5 9.27 60 8.25 60.5 7.22 61 6.16 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M19.5 25.06 L20 24.77 L20.5 24.44 L21 24.08 L21.5 23.69 L22 23.28 L22.5 22.84 L23 22.39 L23.5 21.93 L24 21.45 L24.5 20.96 L25 20.46 L25.5 19.95 L26 19.43 L26.5 18.91 L27 18.39 L27.5 17.87 L28 17.37 L28.5 16.90 L29 16.45 L29.5 16.06 L30 15.71 L30.5 15.43 L31 15.23 L31.5 15.10 L32 15.06 L32.5 15.10 L33 15.23 L33.5 15.43 L34 15.71 L34.5 16.06 L35 16.45 L35.5 16.90 L36 17.37 L36.5 17.87 L37 18.39 L37.5 18.91 L38 19.43 L38.5 19.95 L39 20.46 L39.5 20.96 L40 21.45 L40.5 21.93 L41 22.39 L41.5 22.84 L42 23.28 L42.5 23.69 L43 24.08 L43.5 24.44 L44 24.77 L44.5 25.06"/><circle class="ti-walker" cx="41.6" cy="20.6" r="2.3"/></g><g class="ti-k" style="--k:8"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.15 3.5 7.20 4 8.23 4.5 9.24 5 10.23 5.5 11.19 6 12.13 6.5 13.03 7 13.91 7.5 14.76 8 15.57 8.5 16.33 9 17.06 9.5 17.73 10 18.35 10.5 18.91 11 19.41 11.5 19.85 12 20.22 12.5 20.52 13 20.75 13.5 20.92 14 21.03 14.5 21.09 15 21.10 15.5 21.07 16 21.00 16.5 20.92 17 20.82 17.5 20.72 18 20.62 18.5 20.53 19 20.44 19.5 20.37 20 20.31 20.5 20.26 21 20.21 21.5 20.16 22 20.10 22.5 20.03 23 19.93 23.5 19.81 24 19.65 24.5 19.45 25 19.21 25.5 18.94 26 18.62 26.5 18.27 27 17.89 27.5 17.49 28 17.08 28.5 16.68 29 16.29 29.5 15.94 30 15.63 30.5 15.37 31 15.19 31.5 15.07 32 15.04 32.5 15.09 33 15.22 33.5 15.43 34 15.71 34.5 16.05 35 16.45 35.5 16.90 36 17.37 36.5 17.87 37 18.39 37.5 18.91 38 19.43 38.5 19.95 39 20.46 39.5 20.96 40 21.45 40.5 21.93 41 22.39 41.5 22.84 42 23.28 42.5 23.69 43 24.08 43.5 24.44 44 24.77 44.5 25.06 45 25.30 45.5 25.49 46 25.62 46.5 25.68 47 25.68 47.5 25.61 48 25.47 48.5 25.26 49 24.97 49.5 24.62 50 24.21 50.5 23.74 51 23.21 51.5 22.64 52 22.02 52.5 21.36 53 20.66 53.5 19.93 54 19.17 54.5 18.38 55 17.56 55.5 16.72 56 15.86 56.5 14.98 57 14.07 57.5 13.15 58 12.21 58.5 11.25 59 10.27 59.5 9.27 60 8.25 60.5 7.22 61 6.16 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M5.5 11.19 L6 12.13 L6.5 13.03 L7 13.91 L7.5 14.76 L8 15.57 L8.5 16.33 L9 17.06 L9.5 17.73 L10 18.35 L10.5 18.91 L11 19.41 L11.5 19.85 L12 20.22 L12.5 20.52 L13 20.75 L13.5 20.92 L14 21.03 L14.5 21.09 L15 21.10 L15.5 21.07 L16 21.00 L16.5 20.92 L17 20.82 L17.5 20.72 L18 20.62 L18.5 20.53 L19 20.44 L19.5 20.37 L20 20.31 L20.5 20.26 L21 20.21 L21.5 20.16 L22 20.10 L22.5 20.03 L23 19.93 L23.5 19.81 L24 19.65 L24.5 19.45 L25 19.21 L25.5 18.94 L26 18.62 L26.5 18.27 L27 17.89 L27.5 17.49 L28 17.08 L28.5 16.68 L29 16.29 L29.5 15.94 L30 15.63 L30.5 15.37"/><circle class="ti-walker" cx="22.0" cy="17.8" r="2.3"/></g><g class="ti-k last" style="--k:9"><path class="ti-fill-a" d="M2 4.00 2.5 5.09 3 6.15 3.5 7.20 4 8.23 4.5 9.24 5 10.23 5.5 11.19 6 12.13 6.5 13.03 7 13.91 7.5 14.76 8 15.57 8.5 16.33 9 17.06 9.5 17.73 10 18.35 10.5 18.91 11 19.41 11.5 19.85 12 20.22 12.5 20.52 13 20.75 13.5 20.92 14 21.03 14.5 21.09 15 21.10 15.5 21.07 16 21.00 16.5 20.92 17 20.82 17.5 20.72 18 20.62 18.5 20.53 19 20.44 19.5 20.37 20 20.31 20.5 20.26 21 20.21 21.5 20.16 22 20.10 22.5 20.03 23 19.93 23.5 19.81 24 19.65 24.5 19.45 25 19.21 25.5 18.94 26 18.62 26.5 18.27 27 17.89 27.5 17.49 28 17.08 28.5 16.68 29 16.29 29.5 15.94 30 15.62 30.5 15.37 31 15.18 31.5 15.06 32 15.02 32.5 15.06 33 15.18 33.5 15.37 34 15.62 34.5 15.94 35 16.29 35.5 16.68 36 17.08 36.5 17.49 37 17.89 37.5 18.27 38 18.62 38.5 18.94 39 19.21 39.5 19.45 40 19.65 40.5 19.81 41 19.93 41.5 20.03 42 20.10 42.5 20.16 43 20.21 43.5 20.26 44 20.31 44.5 20.37 45 20.44 45.5 20.53 46 20.62 46.5 20.72 47 20.82 47.5 20.92 48 21.00 48.5 21.07 49 21.10 49.5 21.09 50 21.03 50.5 20.92 51 20.75 51.5 20.52 52 20.22 52.5 19.85 53 19.41 53.5 18.91 54 18.35 54.5 17.73 55 17.06 55.5 16.33 56 15.57 56.5 14.76 57 13.91 57.5 13.03 58 12.13 58.5 11.19 59 10.23 59.5 9.24 60 8.23 60.5 7.20 61 6.15 61.5 5.09 62 4.00 L62 4.00 61.5 5.09 61 6.16 60.5 7.22 60 8.25 59.5 9.27 59 10.27 58.5 11.25 58 12.21 57.5 13.16 57 14.08 56.5 14.99 56 15.88 55.5 16.75 55 17.60 54.5 18.44 54 19.25 53.5 20.05 53 20.83 52.5 21.59 52 22.33 51.5 23.06 51 23.76 50.5 24.45 50 25.12 49.5 25.77 49 26.40 48.5 27.02 48 27.61 47.5 28.19 47 28.75 46.5 29.29 46 29.81 45.5 30.32 45 30.80 44.5 31.27 44 31.72 43.5 32.15 43 32.56 42.5 32.96 42 33.33 41.5 33.69 41 34.03 40.5 34.35 40 34.65 39.5 34.94 39 35.20 38.5 35.45 38 35.68 37.5 35.89 37 36.08 36.5 36.26 36 36.41 35.5 36.55 35 36.67 34.5 36.77 34 36.85 33.5 36.92 33 36.96 32.5 36.99 32 37.00 31.5 36.99 31 36.96 30.5 36.92 30 36.85 29.5 36.77 29 36.67 28.5 36.55 28 36.41 27.5 36.26 27 36.08 26.5 35.89 26 35.68 25.5 35.45 25 35.20 24.5 34.94 24 34.65 23.5 34.35 23 34.03 22.5 33.69 22 33.33 21.5 32.96 21 32.56 20.5 32.15 20 31.72 19.5 31.27 19 30.80 18.5 30.32 18 29.81 17.5 29.29 17 28.75 16.5 28.19 16 27.61 15.5 27.02 15 26.40 14.5 25.77 14 25.12 13.5 24.45 13 23.76 12.5 23.06 12 22.33 11.5 21.59 11 20.83 10.5 20.05 10 19.25 9.5 18.44 9 17.60 8.5 16.75 8 15.88 7.5 14.99 7 14.08 6.5 13.16 6 12.21 5.5 11.25 5 10.27 4.5 9.27 4 8.25 3.5 7.22 3 6.16 2.5 5.09 2 4.00Z"/><path class="ti-new" d="M33.5 15.37 L34 15.62 L34.5 15.94 L35 16.29 L35.5 16.68 L36 17.08 L36.5 17.49 L37 17.89 L37.5 18.27 L38 18.62 L38.5 18.94 L39 19.21 L39.5 19.45 L40 19.65 L40.5 19.81 L41 19.93 L41.5 20.03 L42 20.10 L42.5 20.16 L43 20.21 L43.5 20.26 L44 20.31 L44.5 20.37 L45 20.44 L45.5 20.53 L46 20.62 L46.5 20.72 L47 20.82 L47.5 20.92 L48 21.00 L48.5 21.07 L49 21.10 L49.5 21.09 L50 21.03 L50.5 20.92 L51 20.75 L51.5 20.52 L52 20.22 L52.5 19.85 L53 19.41 L53.5 18.91 L54 18.35 L54.5 17.73 L55 17.06 L55.5 16.33 L56 15.57 L56.5 14.76 L57 13.91 L57.5 13.03 L58 12.13 L58.5 11.19"/><circle class="ti-walker" cx="60.5" cy="2.2" r="2.3"/></g><path class="ti-base" d="M2 4.00 L2.5 5.09 L3 6.16 L3.5 7.22 L4 8.25 L4.5 9.27 L5 10.27 L5.5 11.25 L6 12.21 L6.5 13.16 L7 14.08 L7.5 14.99 L8 15.88 L8.5 16.75 L9 17.60 L9.5 18.44 L10 19.25 L10.5 20.05 L11 20.83 L11.5 21.59 L12 22.33 L12.5 23.06 L13 23.76 L13.5 24.45 L14 25.12 L14.5 25.77 L15 26.40 L15.5 27.02 L16 27.61 L16.5 28.19 L17 28.75 L17.5 29.29 L18 29.81 L18.5 30.32 L19 30.80 L19.5 31.27 L20 31.72 L20.5 32.15 L21 32.56 L21.5 32.96 L22 33.33 L22.5 33.69 L23 34.03 L23.5 34.35 L24 34.65 L24.5 34.94 L25 35.20 L25.5 35.45 L26 35.68 L26.5 35.89 L27 36.08 L27.5 36.26 L28 36.41 L28.5 36.55 L29 36.67 L29.5 36.77 L30 36.85 L30.5 36.92 L31 36.96 L31.5 36.99 L32 37.00 L32.5 36.99 L33 36.96 L33.5 36.92 L34 36.85 L34.5 36.77 L35 36.67 L35.5 36.55 L36 36.41 L36.5 36.26 L37 36.08 L37.5 35.89 L38 35.68 L38.5 35.45 L39 35.20 L39.5 34.94 L40 34.65 L40.5 34.35 L41 34.03 L41.5 33.69 L42 33.33 L42.5 32.96 L43 32.56 L43.5 32.15 L44 31.72 L44.5 31.27 L45 30.80 L45.5 30.32 L46 29.81 L46.5 29.29 L47 28.75 L47.5 28.19 L48 27.61 L48.5 27.02 L49 26.40 L49.5 25.77 L50 25.12 L50.5 24.45 L51 23.76 L51.5 23.06 L52 22.33 L52.5 21.59 L53 20.83 L53.5 20.05 L54 19.25 L54.5 18.44 L55 17.60 L55.5 16.75 L56 15.88 L56.5 14.99 L57 14.08 L57.5 13.16 L58 12.21 L58.5 11.25 L59 10.27 L59.5 9.27 L60 8.25 L60.5 7.22 L61 6.16 L61.5 5.09 L62 4.00"/></svg>'),
    "band": '<svg viewBox="0 0 64 40" aria-hidden="true"><g class="ti-ctr"><ellipse cx="10" cy="30" rx="4.5" ry="3"/><ellipse cx="10" cy="30" rx="8" ry="5.2"/><ellipse cx="54" cy="10" rx="4.5" ry="3"/><ellipse cx="54" cy="10" rx="8" ry="5.2"/><path d="M27.63 33.30 L28.04 32.79 L28.45 32.28 L28.86 31.78 L29.28 31.29 L29.70 30.81 L30.13 30.35 L30.57 29.91 L31.03 29.49 L31.49 29.11 L31.97 28.76 L32.47 28.45 L32.99 28.18 L33.53 27.97 L34.10 27.79 L34.68 27.66 L35.27 27.56 L35.88 27.50 L36.51 27.45 L37.14 27.43 L37.77 27.43 L38.42 27.43 L39.07 27.45 L39.72 27.47 L40.37 27.51"/><path d="M23.63 24.49 L24.28 24.53 L24.93 24.55 L25.58 24.57 L26.23 24.57 L26.86 24.57 L27.49 24.55 L28.12 24.50 L28.73 24.44 L29.32 24.34 L29.90 24.21 L30.47 24.03 L31.01 23.82 L31.53 23.55 L32.03 23.24 L32.51 22.89 L32.97 22.51 L33.43 22.09 L33.87 21.65 L34.30 21.19 L34.72 20.71 L35.14 20.22 L35.55 19.72 L35.96 19.21 L36.37 18.70"/><path d="M27.78 34.83 L28.24 34.34 L28.70 33.85 L29.17 33.38 L29.65 32.92 L30.14 32.47 L30.63 32.05 L31.13 31.65 L31.65 31.27 L32.17 30.91 L32.71 30.58 L33.26 30.28 L33.82 30.01 L34.40 29.76 L34.98 29.55 L35.59 29.36 L36.20 29.20 L36.82 29.06 L37.46 28.95 L38.10 28.85 L38.76 28.78 L39.42 28.72 L40.08 28.68 L40.75 28.65 L41.43 28.63"/><path d="M22.57 23.37 L23.25 23.35 L23.92 23.32 L24.58 23.28 L25.24 23.22 L25.90 23.15 L26.54 23.05 L27.18 22.94 L27.80 22.80 L28.41 22.64 L29.02 22.45 L29.60 22.24 L30.18 21.99 L30.74 21.72 L31.29 21.42 L31.83 21.09 L32.35 20.73 L32.87 20.35 L33.37 19.95 L33.86 19.53 L34.35 19.08 L34.83 18.62 L35.30 18.15 L35.76 17.66 L36.22 17.17"/></g><polyline class="ti-band ti-band-a" points="10.0 30.0 17.3 26.7 24.7 23.3 32.0 20.0 39.3 16.7 46.7 13.3 54.0 10.0"/><polyline class="ti-band ti-band-b" points="10.0 30.0 17.3 29.7 24.7 28.5 32.0 26.0 39.3 21.9 46.7 16.3 54.0 10.0"/><circle class="ti-bead end" cx="10.0" cy="30.00" r="2.6" style="--dy:0.00px"/><circle class="ti-bead" cx="17.3" cy="26.67" r="2" style="--dy:3.00px"/><circle class="ti-bead" cx="24.7" cy="23.33" r="2" style="--dy:5.20px"/><circle class="ti-bead top" cx="32.0" cy="20.00" r="2" style="--dy:6.00px"/><circle class="ti-bead" cx="39.3" cy="16.67" r="2" style="--dy:5.20px"/><circle class="ti-bead" cx="46.7" cy="13.33" r="2" style="--dy:3.00px"/><circle class="ti-bead end" cx="54.0" cy="10.00" r="2.6" style="--dy:0.00px"/></svg>',  # nudged elastic band: a straight band relaxes onto the minimum-energy path
    "wave": ('<svg viewBox="0 0 64 40" aria-hidden="true"><path class="ti-base" d="M2 20 Q10 4 18 20 T34 20 T50 20 T66 20"/>'
             '<path class="ti-fill" d="M2 20 Q10 34 18 20 T34 20 T50 20 T66 20"/></svg>'),
    "orbital": ('<svg viewBox="0 0 64 40" aria-hidden="true"><ellipse class="ti-base" cx="32" cy="20" rx="26" ry="8"/>'
                '<ellipse class="ti-fill" cx="32" cy="20" rx="26" ry="8" transform="rotate(60 32 20)"/>'
                '<circle class="ti-dot" cx="32" cy="20" r="3"/></svg>'),
    "network": ('<svg viewBox="0 0 64 40" aria-hidden="true"><path class="ti-base" d="M8 30 L24 10 L40 26 L56 8 M24 10 L56 8 M8 30 L40 26"/>'
                '<circle class="ti-dot" cx="8" cy="30" r="3"/><circle class="ti-dot" cx="24" cy="10" r="3"/>'
                '<circle class="ti-dot" cx="40" cy="26" r="3"/><circle class="ti-dot" cx="56" cy="8" r="3"/></svg>'),
}
TAB_ICON = ('<svg class="tab-glyph" viewBox="0 0 24 24" aria-hidden="true">'
            '<path class="bulb" d="M9 17.5 C9 14.5 6 13 6 9.5 A6 6 0 0 1 18 9.5 C18 13 15 14.5 15 17.5 Z"/>'
            '<path d="M9.5 20 H14.5 M10.5 22.5 H13.5"/>'
            '<path class="fil" d="M10 14 L11 10.5 L12 13 L13 10.5 L14 14"/></svg>')


def build_teaching():
    meta, body = read_md(CONTENT / "teaching.md") or ({}, "")
    title = meta.get("title", "Theory at Work")
    invite = meta.get("invite", "")
    cards = ""
    for x in meta.get("explorations") or []:
        if not isinstance(x, dict) or not x.get("title") or not x.get("url"):
            continue
        icon = THEORY_ICONS.get(str(x.get("icon", "hills")), THEORY_ICONS["hills"])
        cards += (f'<a class="th-card" href="{esc(web(x["url"]))}" target="_blank" rel="noopener">'
                  f'<span class="th-icon">{icon}</span><span class="th-body">'
                  f'{f"<span class=th-tag>{esc(x["tag"])}</span>" if x.get("tag") else ""}'
                  f'<span class="th-title">{esc(x["title"])}</span>'
                  f'{f"<span class=th-text>{esc(x["text"])}</span>" if x.get("text") else ""}'
                  f'{f"<span class=th-by>{esc(x["by"])}</span>" if x.get("by") else ""}'
                  f'<span class="th-open">Open the interactive page ↗</span></span></a>')
    shown = cards.count('class="th-card"')
    if shown and shown % 3:
        cards += '<div class="th-card th-soon" aria-hidden="true"><span>More explorations on the way</span></div>' * (3 - shown % 3)
    link = meta.get("philosophy_link", "")
    quotes = ""
    for q in meta.get("quotes") or []:
        q = q if isinstance(q, dict) else {"text": q}
        if not q.get("text"):
            continue
        href = web(q.get("link") or link)
        inner = f'<p>“{esc(str(q["text"]).strip("“”\" "))}”</p>'
        quotes += (f'<li><a class="th-quote" href="{esc(href)}" target="_blank" rel="noopener">{inner}</a></li>'
                   if href else f'<li><div class="th-quote">{inner}</div></li>')
    src = meta.get("philosophy_source", "")
    src_html = (f'<p class="th-src">From <a href="{esc(web(link))}" target="_blank" rel="noopener">{esc(src)}</a></p>'
                if src and link else (f'<p class="th-src">{esc(src)}</p>' if src else ""))
    programs = "".join(f"<li>{esc(x)}</li>" for x in meta.get("programs") or [])
    aside = f'<aside class="aside"><h3>Programs</h3><ul>{programs}</ul></aside>' if programs else ""
    return f'''
  <section class="page" id="teaching">
    <h2 class="sr-only">{esc(title)}</h2>
    {f'<p class="th-invite">{esc(invite)}</p>' if invite else ""}
    {f'<p class="lede">{esc(meta["lede"])}</p>' if meta.get("lede") else ""}
    {md(body) if body.strip() else ""}
    {f'<div class="th-cards">{cards}</div>' if cards else ""}
    <div class="split th-split">
      <div>
        {f'<h3 class="th-head">Teaching philosophy</h3><ul class="th-quotes">{quotes}</ul>{src_html}' if quotes else ""}
      </div>
      {aside}
    </div>
  </section>'''


def build_join(site):
    meta, body = read_md(CONTENT / "join.md") or ({}, "")
    c = site.get("contact") or {}
    lines = [esc(c.get("name", ""))] + [esc(x) for x in c.get("address") or []]
    contact_bits = []
    if c.get("email"):
        contact_bits.append(f'<a href="mailto:{esc(c["email"])}">{esc(c["email"])}</a>')
    if c.get("phone"):
        contact_bits.append(esc(c["phone"]))
    lines.append(" · ".join(contact_bits))
    return f'''
  <section class="page" id="join">
    <h2>{esc(meta.get("title", "Join us"))}</h2>
    <div class="split">
      <div class="prose">{md(body)}</div>
      <aside class="aside"><h3>Contact</h3><address>{"<br>".join(l for l in lines if l)}</address></aside>
    </div>
  </section>'''


# ---------------------------------------------------------------- page

SCRIPT = """
  const pages = [...document.querySelectorAll('.page')];
  const links = [...document.querySelectorAll('.tabs a')];
  const siteName = document.body.dataset.siteName;
  function show() {
    const id = (location.hash || '#home').slice(1).replace(/^news$/, 'announcements');
    const target = pages.find(p => p.id === id) || pages[0];
    pages.forEach(p => p.classList.toggle('active', p === target));
    links.forEach(a => {
      if (a.getAttribute('href') === '#' + target.id) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    const h = target.querySelector('h2');
    document.title = (target.id === 'home' || !h ? '' : h.textContent + ' | ') + siteName;
  }
  let pendingTarget = null;
  document.querySelectorAll('[data-target]').forEach(a => a.addEventListener('click', () => {
    pendingTarget = a.dataset.target;
  }));
  window.addEventListener('hashchange', () => {
    show();
    const el = pendingTarget && document.getElementById(pendingTarget);
    pendingTarget = null;
    if (el && el.hidden) { const all = document.querySelector('.nw-filter [data-filter="all"]'); if (all) all.click(); }
    if (el) { const d = el.querySelector('.sm-details'); if (d) d.open = true; }
    if (el) el.scrollIntoView({ block: 'start' });
    else if (location.hash && location.hash !== '#home') window.scrollTo(0, document.querySelector('.tabs').offsetTop);
    else window.scrollTo(0, 0);
  });
  show();

  // Past members tabs: show one panel at a time (all panels show if JavaScript is off)
  const pastTabs = [...document.querySelectorAll('.past-tabs [role="tab"]')];
  function selectPast(tab) {
    pastTabs.forEach(t => {
      const on = t === tab;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    });
  }
  pastTabs.forEach((t, i) => {
    t.addEventListener('click', () => selectPast(t));
    t.addEventListener('keydown', e => {
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (d) { const n = pastTabs[(i + d + pastTabs.length) % pastTabs.length]; selectPast(n); n.focus(); }
    });
  });
  if (pastTabs.length) selectPast(pastTabs[0]);

  // News: filter by type
  document.querySelectorAll('.nw-filter button').forEach(b => b.addEventListener('click', () => {
    const f = b.dataset.filter;
    document.querySelectorAll('.nw-filter button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    document.querySelectorAll('#announcements .an-tile, .an-post').forEach(p => { p.hidden = f !== 'all' && p.dataset.cat !== f; });
  }));

  // Announcements: tiles open the full announcement in a pop-up
  const dlg = document.getElementById('an-dialog');
  if (dlg && dlg.showModal) {
    document.body.appendChild(dlg);
    document.documentElement.classList.add('js-an');
    const box = dlg.querySelector('.an-dialog-body');
    document.querySelectorAll('.an-tile').forEach(t => t.addEventListener('click', () => {
      const a = document.getElementById(t.dataset.open);
      if (!a) return;
      box.innerHTML = a.innerHTML;
      dlg.showModal();
      dlg.querySelector('.an-close').focus();
    }));
    document.querySelectorAll('.sm-pic').forEach(b => b.addEventListener('click', () => {
      const img = document.createElement('img');
      img.src = b.dataset.full; img.alt = b.querySelector('img').alt;
      const fig = document.createElement('figure'); fig.className = 'an-photo sm-big' + (b.classList.contains('sm-logo') ? ' is-logo' : '');
      fig.appendChild(img);
      if (b.dataset.cap) { const c = document.createElement('figcaption'); c.textContent = b.dataset.cap; fig.appendChild(c); }
      box.replaceChildren(fig);
      dlg.showModal();
      dlg.querySelector('.an-close').focus();
    }));
    dlg.querySelector('.an-close').addEventListener('click', () => dlg.close());
    dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
  }

  // The PI's dogs: one dog fades in, stays 2 seconds, fades away; the next comes 5 seconds later.
  // (Still, with the first dog showing, for visitors who ask for reduced motion.)
  document.querySelectorAll('.dog-frame').forEach(frame => {
    const dogs = [...frame.querySelectorAll('.dog')];
    if (!dogs.length) return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) { dogs[0].classList.add('on'); return; }
    const FADE = 900, STAY = 2000, GAP = 5000;
    let i = 0;
    const step = () => {
      if (document.hidden || !frame.offsetParent) { setTimeout(step, 1000); return; }
      const d = dogs[i];
      d.classList.add('on');
      setTimeout(() => d.classList.remove('on'), FADE + STAY);
      i = (i + 1) % dogs.length;
      setTimeout(step, FADE + STAY + FADE + GAP);
    };
    setTimeout(step, 1200);
  });

  // A tiny bee leaves the corner of the designer's name, wanders a little and flies back in.
  // A new, different path each time; it rests a few seconds between flights. Still for reduced motion.
  document.querySelectorAll('.bee-home .bee').forEach(bee => {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let visible = false;
    new IntersectionObserver(es => { visible = es[0].isIntersecting; }).observe(bee.parentElement);
    const rnd = (a, b) => a + Math.random() * (b - a);
    const bez = (p0, p1, p2, p3, t) => {
      const u = 1 - t;
      return [u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0],
              u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1]];
    };
    const fly = () => {
      if (!visible || document.hidden) { setTimeout(fly, 1500); return; }
      const W = Math.min(170, window.innerWidth * 0.35), H = 110;
      const far = [rnd(-W, W), rnd(-H, -30)];                    // where the bee turns around
      const a = [[0, 0], [rnd(-W, W) * 0.6, rnd(-H, 10)], [far[0] + rnd(-40, 40), far[1] + rnd(-30, 30)], far];
      const b = [far, [far[0] + rnd(-60, 60), far[1] + rnd(-40, 40)], [rnd(-W, W) * 0.5, rnd(-H, 0)], [0, 0]];
      const dur = rnd(4200, 6200), t0 = performance.now();
      let prev = [0, 0];
      bee.classList.add('flying');
      const tick = now => {
        const t = Math.min(1, (now - t0) / dur);
        const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;   // ease in-out
        const p = e < 0.5 ? bez(...a, e * 2) : bez(...b, e * 2 - 1);
        const wob = Math.sin(now / 90) * 2.2;                                // a bee never flies straight
        const dx = p[0] - prev[0], dy = p[1] - prev[1];
        const flip = dx < 0 ? -1 : 1;
        const tilt = Math.max(-35, Math.min(35, Math.atan2(dy, Math.abs(dx) + 0.001) * 90 / Math.PI));
        const sc = 0.35 + 0.65 * Math.min(1, Math.min(t, 1 - t) * 8);       // small at the corner
        bee.style.transform = `translate(${p[0]}px, ${p[1] + wob}px) scale(${sc * flip}, ${sc}) rotate(${tilt * flip}deg)`;
        bee.style.opacity = Math.min(1, Math.min(t, 1 - t) * 12);
        prev = p;
        if (t < 1) requestAnimationFrame(tick);
        else { bee.classList.remove('flying'); bee.style.opacity = 0; setTimeout(fly, rnd(6000, 9000)); }
      };
      requestAnimationFrame(tick);
    };
    setTimeout(fly, window.BEE_FIRST || 2500);
  });

  const more = document.getElementById('pub-more');
  if (more) more.addEventListener('click', () => {
    const open = document.getElementById('pub-list').classList.toggle('show-earlier');
    more.setAttribute('aria-expanded', String(open));
    more.textContent = open ? more.dataset.hide : more.dataset.show;
  });
"""


def main():
    try:
        site = yaml.safe_load((CONTENT / "site.yml").read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        sys.exit(f"content/site.yml has a formatting error: {e}")
    config = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8"))
    pubs_file = ROOT / "data" / "publications.json"
    pubs = json.loads(pubs_file.read_text(encoding="utf-8")).get("publications", []) if pubs_file.exists() else []
    fixes = config.get("text_fixes") or {}
    def fix(text):
        for bad, good in fixes.items():
            text = text.replace(bad, good)
        return text.replace("\ufffd", "")  # drop any broken characters that remain
    for p in pubs:
        p["title"] = fix(p.get("title", ""))
        p["venue"] = fix(p.get("venue", ""))
        p["authors"] = [fix(a) for a in p.get("authors", [])]

    alumni = []
    for path, m, b in items("people/alumni"):
        if not m.get("name"):
            warn(path, "skipped: needs a 'name:' line.")
            continue
        alumni.append((path, m, b))

    people = []
    for path, m, b in items("people"):
        if not m.get("name"):
            warn(path, "skipped: needs a 'name:' line.")
            continue
        people.append((path, m, b))

    site_file = CONTENT / "site.yml"
    logo = image(site.get("logo"), site_file)
    logo_dark = image(site.get("logo_dark"), site_file) or logo
    icon = image(site.get("icon"), site_file) or logo
    name = site.get("name", "Research Group")
    home_meta = (read_md(CONTENT / "home.md") or ({}, ""))[0]
    lede = home_meta.get("lede", "")
    url = str(site.get("url", "")).rstrip("/") + "/" if site.get("url") else ""
    description = lede or f'{name}: {site.get("tagline", "")}.'

    mark = image(site.get("mark"), site_file)
    mark_dark = image(site.get("mark_dark"), site_file) or mark
    depts = site.get("departments") or ([site["department"]] if site.get("department") else [])
    if isinstance(depts, str):
        depts = [depts]
    hero_mark = (f'<div class="hero-mark" aria-hidden="true"><img class="logo-light" src="{esc(mark)}" alt="">'
                 f'<img class="logo-dark" src="{esc(mark_dark)}" alt="">{logo_signals_svg()}</div>') if mark else ""
    nav_icon = f'<img src="{esc(icon)}" alt="">' if icon else ""
    footer_logo = f'<img src="{esc(logo_dark)}" alt="">' if logo_dark else ""
    uni_dark = image(site.get("university_logo_dark"), site_file) if site.get("university_logo_dark") else ""
    uni_light = image(site.get("university_logo"), site_file) if site.get("university_logo") else ""
    if footer_logo and (uni_dark or uni_light):
        # The lab logo wired to the university logo, with signals passing both ways.
        round_cls = ""
        try:
            from PIL import Image
            with Image.open(ROOT / (uni_dark or uni_light)) as im:
                if 0.9 < im.width / im.height < 1.1:
                    round_cls = " round"                   # a round seal sits on a round white card
        except Exception:
            pass
        uni_img = (f'<img src="{esc(uni_dark)}" alt="{esc(site.get("university_name", "Duquesne University"))}">' if uni_dark else
                   f'<span class="uni-chip{round_cls}"><img src="{esc(uni_light)}" alt="{esc(site.get("university_name", "Duquesne University"))}"></span>')
        uni_url = site.get("university_url")
        uni = f'<a class="uni-logo" href="{esc(web(uni_url))}">{uni_img}</a>' if uni_url else f'<span class="uni-logo">{uni_img}</span>'
        footer_logo = (f'<div class="logo-pair">{footer_logo}'
                       '<svg class="wire" viewBox="0 0 120 64" aria-hidden="true" focusable="false">'
                       '<path class="w" d="M2 24 H38 L50 12 H70 L82 24 H118"/><path class="w" d="M2 40 H30 L42 52 H78 L90 40 H118"/>'
                       '<circle class="wp" cx="2" cy="24" r="3"/><circle class="wp" cx="2" cy="40" r="3"/>'
                       '<circle class="wp" cx="118" cy="24" r="3"/><circle class="wp" cx="118" cy="40" r="3"/>'
                       '<circle class="wn" cx="60" cy="12" r="2.6"/><circle class="wn" cx="60" cy="52" r="2.6"/>'
                       '<path class="ws" pathLength="100" d="M2 24 H38 L50 12 H70 L82 24 H118"/>'
                       '<path class="ws back" pathLength="100" d="M118 40 H90 L78 52 H42 L30 40 H2"/></svg>'
                       f'{uni}</div>')
    social = ""
    if url:
        social = (f'<link rel="canonical" href="{esc(url)}">\n'
                  f'<meta property="og:type" content="website">\n'
                  f'<meta property="og:url" content="{esc(url)}">\n'
                  f'<meta property="og:title" content="{esc(name)}">\n'
                  f'<meta property="og:description" content="{esc(description)}">\n'
                  + (f'<meta property="og:image" content="{esc(url + logo)}">\n' if logo else ""))

    ANN.update(site.get("announcements") or {})
    global DISSERTATIONS, AUTO_COVERS
    DISSERTATIONS = load_dissertations()
    cf = ROOT / "data" / "covers.json"
    AUTO_COVERS = [c for c in (json.loads(cf.read_text(encoding="utf-8")).get("covers", []) if cf.exists() else [])
                   if (ROOT / c.get("image", "")).is_file()]
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    areas = research_areas()
    body = "".join([
        build_home(site, pubs, areas),
        build_research(areas, {doi_url(p['doi'])[1].lower(): p for p in pubs if p.get('doi')}),
        build_people(people, alumni, pubs),
        register_auto_covers(pubs),
        build_publications(pubs, name_matchers(people + alumni, config)),
        build_news(news_items(), site),
        build_teaching(),
        build_join(site),
    ])
    c = site.get("contact") or {}
    address = "<br>".join(esc(x) for x in [c.get("name", "")] + list(c.get("address") or []) if x)
    contact_line = "".join(f"<span>{x}</span>" for x in [
        f'<a href="mailto:{esc(c["email"])}">{esc(c["email"])}</a>' if c.get("email") else "",
        esc(c.get("phone", "")),
        f'<a href="{esc(url)}">{esc(url.split("//")[-1].rstrip("/"))}</a>' if url else ""] if x)

    page = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(name)}</title>
<meta name="description" content="{esc(description)}">
{social}{f'<link rel="icon" href="{esc(icon)}">' if icon else ""}
<link rel="preload" href="fonts/cmu-serif/cmu-serif-500-roman.woff2" as="font" type="font/woff2" crossorigin>
<style>
{(TEMPLATES / "style.css").read_text(encoding="utf-8")}
</style>
<script>
function coverFailed(img) {{
  if (img.dataset.backup) {{ const b = img.dataset.backup; delete img.dataset.backup; img.src = b; return; }}
  const fig = img.closest('figure'); fig.hidden = true;
  const box = fig.closest('.covers');
  if (box && ![...box.querySelectorAll('figure')].some(f => !f.hidden)) box.hidden = true;
}}
</script>
</head>
<body data-site-name="{esc(name)}">
<header class="hero">
  {lattice_svg()}
  <div class="wrap hero-grid">
    {hero_mark}
    <div class="hero-text">
      <h1 class="hero-title">{esc(name)}</h1>
      {f'<p class="hero-lede">{esc(lede)}</p>' if lede else ""}
      <p class="hero-meta">{"<br>".join([esc(site.get("tagline", ""))] + [dept_html(d) for d in depts])}</p>
    </div>
  </div>
</header>
<nav class="tabs" aria-label="Site pages">
  <div class="wrap">
    <a class="tab-icon" href="#home" aria-label="Home">{nav_icon}</a>
    <a href="#home">Home</a>
    <a href="#research">Research</a>
    <a href="#people">People</a>
    <a href="#publications">Publications</a>
    <a href="#announcements">Announcements</a>
    <a class="tab-theory" href="#teaching">{TAB_ICON}Theory at Work</a>
    <a href="#join">Join Us</a>
  </div>
</nav>
<main class="wrap">{body}
</main>
<footer class="site-footer">
  <div class="wrap footer-grid">
    <div class="footer-logo">{footer_logo}</div>
    <address>{address}</address>
    <div class="footer-contact">{contact_line}</div>
  </div>
  <div class="wrap">{credits_html()}</div>
  <div class="wrap"><div class="footer-base">
    <span>{" · ".join(dept_html(d) for d in depts)}</span>
    <span>Updated {datetime.date.today().strftime("%B %-d, %Y")}</span>
  </div></div>
</footer>
<script>{SCRIPT}</script>
</body>
</html>
'''
    (OUT / "index.html").write_text(page, encoding="utf-8")
    for folder in ("images", "fonts", "theses"):
        if (ROOT / folder).exists():
            shutil.copytree(ROOT / folder, OUT / folder, dirs_exist_ok=True)
    (OUT / ".nojekyll").write_text("")

    print(f"Built _site/index.html: {len(people)} people, {len(pubs)} publications.")
    if warnings:
        print(f"{len(warnings)} warning(s); see above.")


if __name__ == "__main__":
    main()
