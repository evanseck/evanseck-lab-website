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


# ---------------------------------------------------------------- sections

def build_home(site, pubs):
    r = read_md(CONTENT / "home.md") or ({}, "")
    meta, body = r
    latest = "".join(
        f'<li><a href="{esc(p["doi"] or "#publications")}">{esc(p["title"])}</a>'
        f'<span class="src">{esc(p.get("venue", ""))}{", " if p.get("venue") else ""}{esc(p["year"])}</span></li>'
        for p in pubs[:4]
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
        news_html = '<h2 style="margin:3rem 0 0">News</h2><ul class="news">' + "".join(rows) + "</ul>"
    return f'''
  <section class="page" id="home">
    <div class="split">
      <div>
        {f'<p class="lede">{esc(meta.get("lede"))}</p>' if meta.get("lede") else ""}
        {md(body)}
      </div>
      <aside class="aside">
        <h3>Latest papers</h3>
        <ul class="latest">{latest}</ul>
      </aside>
    </div>
    {news_html}
  </section>'''


def build_research():
    meta, body = read_md(CONTENT / "research.md") or ({}, "")
    areas = []
    for path, m, b in items("research"):
        if not m.get("title"):
            warn(path, "skipped: needs a 'title:' line.")
            continue
        collab = f'<p class="collab">Collaborators: {esc(m["collaborators"])}</p>' if m.get("collaborators") else ""
        areas.append(f'<article class="area"><h3>{esc(m["title"])}</h3><div>{md(b)}{collab}</div></article>')
    return f'''
  <section class="page" id="research">
    <h2>{esc(meta.get("title", "Research"))}</h2>
    <div style="margin-bottom:2rem">{md(body)}</div>
    {"".join(areas)}
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
                c += f' <a href="{esc(p["doi"])}">DOI</a>'
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
  const siteName = document.querySelector('.banner h1').textContent;
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
  window.addEventListener('hashchange', () => { show(); window.scrollTo(0, 0); });
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

    people = []
    for path, m, b in items("people"):
        if not m.get("name"):
            warn(path, "skipped: needs a 'name:' line.")
            continue
        people.append((path, m, b))

    logo = image(site.get("logo"), CONTENT / "site.yml")
    logo_html = f'<img src="{esc(logo)}" alt="">' if logo else ""
    favicon = f'<link rel="icon" href="{esc(logo)}">' if logo else ""

    body = "".join([
        build_home(site, pubs),
        build_research(),
        build_people(people),
        build_publications(pubs, name_matchers(people, config)),
        build_teaching(),
        build_join(site),
    ])
    name = site.get("name", "Research Group")
    page = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(name)}</title>
<meta name="description" content="{esc(name)}: {esc(site.get("tagline", ""))}. {esc(site.get("department", ""))}.">
{favicon}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=Public+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{(TEMPLATES / "style.css").read_text(encoding="utf-8")}
</style>
</head>
<body>
<header class="banner">
  <div class="wrap">
    <div>
      <div class="brand">{logo_html}<h1>{esc(name)}</h1></div>
      <p class="sub">{esc(site.get("tagline", ""))}<br>{esc(site.get("department", ""))}</p>
    </div>
    {(TEMPLATES / "banner.svg").read_text(encoding="utf-8")}
  </div>
</header>
<nav class="tabs" aria-label="Site pages">
  <div class="wrap">
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
<footer>
  <div class="wrap">
    <span>{esc(name)}, {esc(site.get("department", "").split(",")[-1].strip())}</span>
    <span>Updated {datetime.date.today().strftime("%B %-d, %Y")}</span>
  </div>
</footer>
<script>{SCRIPT}</script>
</body>
</html>
'''
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / "index.html").write_text(page, encoding="utf-8")
    if (ROOT / "images").exists():
        shutil.copytree(ROOT / "images", OUT / "images")
    (OUT / ".nojekyll").write_text("")

    print(f"Built _site/index.html: {len(people)} people, {len(pubs)} publications.")
    if warnings:
        print(f"{len(warnings)} warning(s); see above.")


if __name__ == "__main__":
    main()
