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

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
TEMPLATES = ROOT / "templates"
OUT = ROOT / "_site"

PEOPLE_GROUPS = [
    ("postdoc", "Postdocs"),
    ("staff", "Staff"),
    ("grad", "Graduate students"),
    ("undergrad", "Undergraduate researchers"),
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
    k = 0
    for row in range(-1, 8):
        for col in range(-1, 16):
            cx = col * w + (w / 2 if row % 2 else 0)
            cy = row * h
            pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(-90, 270, 60)]
            hexes.append("M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + " Z")
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
                    traces.append(f'<path class="tr" style="--d:{(k % 23) * 0.05:.2f}s" d="M{sx:.1f} {sy:.1f} L{m1x:.1f} {m1y:.1f} L{ex:.1f} {ey:.1f}"/>')
                    nodes.append(f'<circle class="nd" style="--d:{(k % 23) * 0.05 + 0.5:.2f}s" cx="{ex:.1f}" cy="{ey:.1f}" r="3"/>')
    for (row, col, i) in [(1, 12, 1), (3, 13, 4), (4, 11, 0), (2, 14, 2)]:
        cx = col * w + (w / 2 if row % 2 else 0)
        cy = row * h
        a = math.radians(-90 + 60 * i)
        gold.append(f'<circle class="gd" cx="{cx + r * math.cos(a):.1f}" cy="{cy + r * math.sin(a):.1f}" r="8"/>')
    return (f'<svg class="lattice" viewBox="0 0 1100 460" preserveAspectRatio="xMaxYMid slice" aria-hidden="true" focusable="false">'
            f'<path class="hx" d="{" ".join(hexes)}"/>{"".join(traces)}{"".join(nodes)}{"".join(gold)}</svg>')


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
        for p in pubs[:4]
    )
    tiles = "".join(
        f'<a class="tile" href="#research" data-target="area-{esc(slug)}">'
        f'<div class="tile-fig">{figure_html(pick_figure(m)[0], path, pick_figure(m)[1]) if pick_figure(m)[0] else ""}</div>'
        f'<span class="tile-title">{esc(m["title"])}</span></a>'
        for path, m, b, slug in areas
    )
    news = sorted(items("news"), key=lambda x: str(x[1].get("date", "")), reverse=True)
    news_html = ""
    if news:
        rows = []
        for path, m, b in news[:8]:
            d = m.get("date")
            try:
                d = d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d))
                label = d.strftime("%B %Y")
            except ValueError:
                warn(path, "date should look like 2026-10-15; showing it as written.")
                label = str(d)
            rows.append(f'<li><time>{esc(label)}</time><div>{md(b)}</div></li>')
        news_html = '<h2 class="section-title">News</h2><ul class="news">' + "".join(rows) + "</ul>"
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
        extra = collaborators_html(m.get("collaborators")) + key_papers_html(m.get("papers"), pubs_by_doi, path)
        out.append(f'<article class="area{" has-fig" if fig else ""}" id="area-{esc(slug)}">'
                   f'<div class="area-text"><h3>{esc(m["title"])}</h3>{md(b)}{extra}</div>{fig_html}</article>')
    return f'''
  <section class="page" id="research">
    <h2>{esc(meta.get("title", "Research"))}</h2>
    <div class="page-intro">{md(body)}</div>
    {"".join(out)}
  </section>'''


def person_links(m):
    links = []
    if m.get("email"):
        links.append(f'<a href="mailto:{esc(m["email"])}">{esc(m["email"])}</a>')
    for l in m.get("links") or []:
        if isinstance(l, dict) and l.get("url"):
            links.append(f'<a href="{esc(l["url"])}">{esc(l.get("label", l["url"]))}</a>')
    return " · ".join(links)


def build_people(people):
    pi_html, groups, alumni = "", {g: [] for g, _ in PEOPLE_GROUPS}, []
    for path, m, b in people:
        g = str(m.get("group", "")).strip().lower()
        photo = image(m.get("photo"), path)
        if g == "pi":
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
            extra = ", ".join(x for x in [str(m.get("years") or ""), str(m.get("now") or "")] if x)
            alumni.append((m["name"], f'<li>{esc(m["name"])}{f"<span>, {esc(extra)}</span>" if extra else ""}</li>'))
        elif g in groups:
            img = (f'<img class="photo" src="{esc(photo)}" alt="{esc(m["name"])}">' if photo
                   else f'<div class="photo" aria-hidden="true">{initials(m["name"])}</div>')
            role = esc(m.get("role", ""))
            proj = f'<p class="role">{esc(m["project"])}</p>' if m.get("project") else ""
            bio = f'<div class="bio">{md(b)}</div>' if b else ""
            links = person_links(m)
            groups[g].append((m.get("order", 99), m["name"], f'''
        <div class="person">{img}<h4>{esc(m["name"])}</h4><p class="role">{role}</p>{proj}{bio}{f'<p class="role">{links}</p>' if links else ""}</div>'''))
        else:
            warn(path, f"skipped: 'group:' must be one of pi, postdoc, staff, grad, undergrad, alumni (found '{g}').")

    out = [pi_html]
    for g, label in PEOPLE_GROUPS:
        if groups[g]:
            cards = "".join(c for _, _, c in sorted(groups[g], key=lambda x: (x[0], x[1])))
            out.append(f'<p class="group-label">{label}</p><div class="people">{cards}</div>')
    if alumni:
        out.append('<p class="group-label">Alumni</p><ul class="alumni">' + "".join(h for _, h in sorted(alumni)) + "</ul>")
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


def build_publications(pubs, matchers):
    by_year = {}
    for p in pubs:
        by_year.setdefault(p["year"], []).append(p)
    years = sorted(by_year, reverse=True)
    if not years:
        return '<section class="page" id="publications"><h2>Publications</h2><p>Coming soon.</p></section>'
    cutoff = years[0] - RECENT_YEARS + 1
    blocks, earlier = [], 0
    for y in years:
        lis = []
        for p in by_year[y]:
            authors = ", ".join(f"<b>{esc(a)}</b>" if is_member(a, matchers) else esc(a) for a in p.get("authors", []))
            c = f'<span class="t">{esc(p["title"])}</span>{authors}. '
            if p.get("venue"):
                c += f"<i>{esc(p['venue'])}</i> "
            c += esc(y)
            if p.get("volume"):
                c += f", {esc(p['volume'])}"
            if p.get("pages"):
                c += f", {esc(p['pages'])}"
            c += "."
            if p.get("doi"):
                c += f' <a class="doi" href="{esc(p["doi"])}">doi:{esc(doi_url(p["doi"])[1])}</a>'
            lis.append(f"<li>{c}</li>")
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
    <p class="note">Group members are shown in bold. This list updates automatically each week from <a href="https://openalex.org">OpenAlex</a>.</p>
    <div id="pub-list">{"".join(blocks)}</div>
    {button}
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
    if (el) el.scrollIntoView({ block: 'start' });
    else if (location.hash && location.hash !== '#home') window.scrollTo(0, document.querySelector('.tabs').offsetTop);
    else window.scrollTo(0, 0);
  });
  show();

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
    hero_mark = (f'<div class="hero-mark" aria-hidden="true"><img class="logo-light" src="{esc(mark)}" alt="">'
                 f'<img class="logo-dark" src="{esc(mark_dark)}" alt=""></div>') if mark else ""
    nav_icon = f'<img src="{esc(icon)}" alt="">' if icon else ""
    footer_logo = f'<img src="{esc(logo_dark)}" alt="">' if logo_dark else ""
    social = ""
    if url:
        social = (f'<link rel="canonical" href="{esc(url)}">\n'
                  f'<meta property="og:type" content="website">\n'
                  f'<meta property="og:url" content="{esc(url)}">\n'
                  f'<meta property="og:title" content="{esc(name)}">\n'
                  f'<meta property="og:description" content="{esc(description)}">\n'
                  + (f'<meta property="og:image" content="{esc(url + logo)}">\n' if logo else ""))

    areas = research_areas()
    body = "".join([
        build_home(site, pubs, areas),
        build_research(areas, {doi_url(p['doi'])[1].lower(): p for p in pubs if p.get('doi')}),
        build_people(people),
        build_publications(pubs, name_matchers(people, config)),
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
</head>
<body data-site-name="{esc(name)}">
<header class="hero">
  {lattice_svg()}
  <div class="wrap hero-grid">
    {hero_mark}
    <div class="hero-text">
      <h1 class="hero-title">{esc(name)}</h1>
      {f'<p class="hero-lede">{esc(lede)}</p>' if lede else ""}
      <p class="hero-meta">{esc(site.get("tagline", ""))}<br>{esc(site.get("department", ""))}</p>
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
  <div class="wrap"><div class="footer-base">
    <span>{esc(site.get("department", ""))}</span>
    <span>Updated {datetime.date.today().strftime("%B %-d, %Y")}</span>
  </div></div>
</footer>
<script>{SCRIPT}</script>
</body>
</html>
'''
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / "index.html").write_text(page, encoding="utf-8")
    for folder in ("images", "fonts"):
        if (ROOT / folder).exists():
            shutil.copytree(ROOT / folder, OUT / folder)
    (OUT / ".nojekyll").write_text("")

    print(f"Built _site/index.html: {len(people)} people, {len(pubs)} publications.")
    if warnings:
        print(f"{len(warnings)} warning(s); see above.")


if __name__ == "__main__":
    main()
