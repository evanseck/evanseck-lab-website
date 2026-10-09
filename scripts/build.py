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
    if not p.is_file() and (ROOT / "images" / path_str).is_file():
        return "images/" + str(path_str).lstrip("/")      # "images/" was left off the file name
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
    """Large uploaded images get a small copy for the web page (originals are kept)."""
    src = ROOT / img
    if not src.is_file() or src.stat().st_size < 400_000:
        return img
    try:
        from PIL import Image
    except ImportError:
        return img
    rel = f"images/web/{src.stem}-{width}.jpg"
    dst = OUT / rel
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
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


NEWS_CATEGORIES = [("accomplishment", "Accomplishments"), ("seminar", "Seminar reviews"), ("news", "Group news")]
NEWS_TAGS = {"accomplishment": "Accomplishment", "seminar": "Seminar review", "news": "Group news"}


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
    return sorted(out, key=lambda n: n["date"], reverse=True)


def news_date(n):
    d = n["date"]
    return f"{d.strftime('%B')} {d.year}" if n.get("month_only") else f"{d.strftime('%B')} {d.day}, {d.year}"


def build_news(news):
    if not news:
        posts = '<p class="note">News will appear here.</p>'
    else:
        posts = ""
        for n in news:
            m = n["m"]
            label = NEWS_TAGS[n["cat"]]
            meta = []
            if n["cat"] == "seminar":
                who = ", ".join(x for x in [m.get("speaker", ""), m.get("speaker_affiliation", "")] if x)
                where = ", ".join(x for x in [m.get("event", ""), m.get("location", "")] if x)
                if who:
                    meta.append(f'<span class="nw-who">Speaker: {esc(who)}</span>')
                if where:
                    meta.append(f'<span class="nw-where">{esc(where)}</span>')
            if m.get("author"):
                meta.append(f'<span class="nw-by">Written by {esc(m["author"])}</span>')
            photos = ""
            if n["photos"]:
                first, rest = n["photos"][0], n["photos"][1:]
                cap = f'<figcaption>{esc(m["photo_caption"])}</figcaption>' if m.get("photo_caption") else ""
                thumbs = "".join(f'<a href="{esc(ph)}" target="_blank" rel="noopener"><img src="{esc(web_copy(ph, 400))}" alt="" loading="lazy"></a>' for ph in rest)
                photos = (f'<figure class="nw-photo"><a href="{esc(first)}" target="_blank" rel="noopener">'
                          f'<img src="{esc(web_copy(first, 900))}" alt="{esc(m.get("photo_alt") or m["title"])}" loading="lazy"></a>{cap}'
                          f'{f"<div class=nw-thumbs>{thumbs}</div>" if thumbs else ""}</figure>')
            body = md(n["body"])
            paras = re.findall(r"<p>.*?</p>", body, re.S)
            if n["cat"] == "seminar" and len(paras) > 1:   # long reviews: first paragraph, then "Read the full review"
                body = paras[0] + f'<details class="nw-more"><summary>Read the full review</summary>{body.replace(paras[0], "", 1)}</details>'
            link = f'<p><a href="{esc(web(m["link"]))}">{esc(m.get("link_text") or "More")}</a></p>' if m.get("link") else ""
            posts += (f'<article class="nw-post{" has-photo" if photos else ""}" id="{esc(n["slug"])}" data-cat="{n["cat"]}">'
                      f'<div class="nw-text"><p class="nw-top"><time datetime="{n["date"].isoformat()}">{news_date(n)}</time>'
                      f'<span class="nw-tag nw-{n["cat"]}">{esc(label)}</span></p>'
                      f'<h3>{esc(m["title"])}</h3>{f"<p class=nw-meta>{"".join(meta)}</p>" if meta else ""}{body}{link}</div>'
                      f'{photos}</article>')
    used = [c for c, _ in NEWS_CATEGORIES if any(n["cat"] == c for n in news)]
    chips = ""
    if len(used) > 1:
        chips = ('<div class="nw-filter" role="group" aria-label="Show news by type">'
                 '<button type="button" data-filter="all" aria-pressed="true">All</button>'
                 + "".join(f'<button type="button" data-filter="{c}" aria-pressed="false">{esc(l)}</button>'
                           for c, l in NEWS_CATEGORIES if c in used) + "</div>")
    return f'''
  <section class="page" id="news">
    <h2>News</h2>
    {chips}
    <div class="nw-list">{posts}</div>
  </section>'''


def latest_news_html(news, count=3):
    if not news:
        return ""
    cards = ""
    for n in news[:count]:
        img = f'<img src="{esc(web_copy(n["photos"][0], 400))}" alt="" loading="lazy">' if n["photos"] else ""
        teaser = re.sub(r"<[^>]+>", "", md(n["body"]).split("</p>")[0])
        teaser = (teaser[:150].rsplit(" ", 1)[0] + "…") if len(teaser) > 150 else teaser
        cards += (f'<a class="nw-card" href="#news" data-target="{esc(n["slug"])}">'
                  f'{f"<span class=nw-card-img>{img}</span>" if img else ""}'
                  f'<span class="nw-card-text"><span class="nw-top"><time>{news_date(n)}</time>'
                  f'<span class="nw-tag nw-{n["cat"]}">{esc(NEWS_TAGS[n["cat"]])}</span></span>'
                  f'<span class="nw-card-title">{esc(n["m"]["title"])}</span>'
                  f'<span class="nw-card-teaser">{esc(html.unescape(teaser))}</span></span></a>')
    return (f'<div class="section-head"><h2 class="section-title">News</h2><a class="more-link" href="#news">All news</a></div>'
            f'<div class="nw-cards">{cards}</div>')


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
        who = esc(c["who"])
        if c.get("link"):
            who = f'<a href="{esc(web(c["link"]))}">{who}</a>'
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
      {img}
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


def build_teaching():
    meta, body = read_md(CONTENT / "teaching.md") or ({}, "")
    programs = "".join(f"<li>{esc(x)}</li>" for x in meta.get("programs") or [])
    aside = f'<aside class="aside"><h3>Programs</h3><ul>{programs}</ul></aside>' if programs else ""
    return f'''
  <section class="page" id="teaching">
    <h2>{esc(meta.get("title", "Teaching and outreach"))}</h2>
    <div class="split">
      <div>
        {f'<p class="lede">{esc(meta["lede"])}</p>' if meta.get("lede") else ""}
        {md(body)}
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
    const id = (location.hash || '#home').slice(1);
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
    document.querySelectorAll('.nw-post').forEach(p => { p.hidden = f !== 'all' && p.dataset.cat !== f; });
  }));

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
        build_news(news_items()),
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
    <a href="#news">News</a>
    <a href="#teaching">Teaching &amp; Outreach</a>
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
