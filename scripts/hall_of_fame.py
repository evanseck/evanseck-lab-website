"""
Hall of Fame: turns the group's achievement slides into tiles on the Announcements tab.

How to add one: export the slide as a PDF and upload it to content/hall-of-fame/.
The newest slides come first. The site uses the date at the start of the file name if there is
one (and shows it on the tile), otherwise the date the slide was exported from PowerPoint, for example
    2025-11-13-Joe_Defense.pdf      or      2026-04-Unni_Excellence.pdf
Several slides about the same event (e.g. two hooding-ceremony slides) become one tile.

What the site reads from each slide:
  * defense slides ("Dr. …", "Mentor:", "Topic:", "Employment:"): name, mentor, thesis, new job;
  * award and fellowship slides: the award (top line) and the group member's name
    (the name printed above "Evanseck Research Group");
  * presentation slides ("B.S. …"): name, meeting and place, program;
  * hooding-ceremony slides: the photos.
The photos on each slide become the tile's photo and the pop-up's gallery.

To change what a tile says, add a text file with the same name ending in .yml next to the PDF,
for example 2025-11-13-Joe_Defense.yml, with any of these lines:
    title: Dr. Joe Makowski
    line: RNA homodimerization
    tag: Ph.D. defense
    date: 2025-11-13
    photo: 2            (which photo goes on the tile, counting from 1)
    hide: true          (leave this slide off the website)
Settings: scripts/config.json, "hall_of_fame" (which slides count, and spelling fixes).
Needs: pypdf and Pillow (both installed by the workflow).
"""
import datetime
import io
import re

MONTHS = "January February March April May June July August September October November December".split()


# ---------------------------------------------------------------- reading a slide
def _placements(page):
    """Every image drawn on the page with its position (x, y, width, height in points)."""
    from pypdf.generic import ContentStream
    out = []

    def walk(content, resources, ctm, depth=0):
        xo = (resources or {}).get("/XObject") or {}
        stack, m = [], list(ctm)
        for ops, op in ContentStream(content, page.pdf).operations:
            if op == b"q":
                stack.append(list(m))
            elif op == b"Q":
                m = stack.pop() if stack else m
            elif op == b"cm":
                a, b, c, d, e, f = [float(x) for x in ops]
                m = [a * m[0] + b * m[2], a * m[1] + b * m[3], c * m[0] + d * m[2], c * m[1] + d * m[3],
                     e * m[0] + f * m[2] + m[4], e * m[1] + f * m[3] + m[5]]
            elif op == b"Do" and ops[0] in xo:
                x = xo[ops[0]].get_object()
                if x.get("/Subtype") == "/Image":
                    out.append((str(ops[0]).lstrip("/"), m[4], m[5], abs(m[0]), abs(m[3])))
                elif x.get("/Subtype") == "/Form" and depth < 4:
                    walk(x, x.get("/Resources") or resources, m, depth + 1)

    walk(page.get_contents(), page.get("/Resources"), [1, 0, 0, 1, 0, 0])
    return out


def _text_positions(page):
    """(text, x, y) for each run of text on the page."""
    runs = []

    def visit(text, cm, tm, *_):
        if text and text.strip():
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            runs.append((text.strip(), x, y))
    try:
        page.extract_text(visitor_text=visit)
    except Exception:
        pass
    return runs


def _detail(im):
    from PIL import ImageFilter, ImageStat
    g = im.convert("L")
    if g.size[0] > 240:
        g = g.resize((240, max(1, int(240 * g.size[1] / g.size[0]))))
    return ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0], ImageStat.Stat(g).stddev[0]


def _pdf_date(reader):
    """When the slide was exported from PowerPoint (the PDF's own date), for ordering."""
    try:
        raw = str((reader.metadata or {}).get("/CreationDate") or (reader.metadata or {}).get("/ModDate") or "")
        m = re.search(r"(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", raw)
        if m:
            return datetime.datetime(*[int(g or 0) for g in m.groups()])
    except Exception:
        pass
    return None


def read_slide(path):
    """Text lines, text positions and the photos (stitched back together when a photo was
    stored as strips, and with decorations and backgrounds dropped) of a one-page PDF."""
    from pypdf import PdfReader
    from PIL import Image
    reader = PdfReader(str(path))
    page = reader.pages[0]
    pw = float(page.mediabox.width)
    lines = [re.sub(r"\s+", " ", l).strip() for l in (page.extract_text() or "").splitlines()]
    lines = [l for l in lines if l]
    data = {}
    for im in page.images:
        try:
            data[im.name.rsplit(".", 1)[0]] = Image.open(io.BytesIO(im.data)).convert("RGB")
        except Exception:
            continue
    placed = [p for p in _placements(page) if p[0] in data]
    # strips: same left edge and width, touching vertically -> one photo (top strip first)
    placed.sort(key=lambda p: (round(p[1]), round(p[3]), -p[2]))
    groups = []
    for p in placed:
        g = groups[-1] if groups else None
        if g and abs(g[-1][1] - p[1]) < 1.5 and abs(g[-1][3] - p[3]) < 1.5 and abs(g[-1][2] - (p[2] + p[4])) < 2.5:
            g.append(p)
        else:
            groups.append([p])
    photos = []
    for g in groups:
        ims = [data[p[0]] for p in g]
        if len(ims) > 1:
            W = ims[0].size[0]
            ims = [i if i.size[0] == W else i.resize((W, int(i.size[1] * W / i.size[0]))) for i in ims]
            st = Image.new("RGB", (W, sum(i.size[1] for i in ims)))
            y = 0
            for i in ims:
                st.paste(i, (0, y))
                y += i.size[1]
            im = st
        else:
            im = ims[0]
        x, ytop = g[0][1], g[0][2] + g[0][4]
        w, h = g[0][3], ytop - g[-1][2]
        if w > 0.9 * pw or w < 40 or h < 40:                 # full-width backgrounds, small ornaments
            continue
        px, py = im.size
        if px * py < 60000 or max(px, py) / min(px, py) > 2.6:
            continue
        edge, sd = _detail(im)
        if edge < 14 or sd < 30:                           # flat colour, gradients, line frames
            continue
        photos.append({"img": im, "x": x, "y": g[-1][2], "w": w, "h": h})
    photos.sort(key=lambda p: (-(p["y"] + p["h"]), p["x"]))   # top to bottom, left to right
    return {"lines": lines, "text": " ".join(lines), "runs": _text_positions(page), "photos": photos,
            "exported": _pdf_date(reader)}


# ---------------------------------------------------------------- understanding a slide
def _fix(s, fixes):
    for a, b in fixes.items():
        s = s.replace(a, b)
    return s


def _is_label(l):
    return bool(re.match(r"^(Mentor|Topic|Employment|Advisor|Thesis|Dissertation)\s*:", l, re.I))


def _join(parts):
    out = ""
    for p in parts:
        out = out + p if out.endswith("-") else (out + " " + p if out else p)
    return out.strip()


NAME = re.compile(r"^[A-Z][a-zA-Z'’.-]+(?: [A-Z][a-zA-Z'’.-]+){1,3}$")


def _defense(L):
    mentor = topic = job = name = ""
    for i, l in enumerate(L):
        if re.match(r"^Mentor\s*:", l, re.I):
            mentor = re.sub(r"^Mentor\s*:\s*", "", l, flags=re.I) or (L[i + 1] if i + 1 < len(L) else "")
    for l in L:
        if l.startswith("Dr.") and l != mentor and not re.search(r"Evanseck", l):
            name = l
            break
    def after(label):
        for i, l in enumerate(L):
            if re.match(rf"^{label}\s*:", l, re.I):
                parts, rest = [], re.sub(rf"^{label}\s*:\s*", "", l, flags=re.I)
                if rest:
                    parts.append(rest)
                for m in L[i + 1:]:
                    if _is_label(m) or m == name:
                        break
                    parts.append(m)
                return _join(parts)
        return ""
    topic, job = after("Topic"), after("Employment")
    if job.upper() in ("TBD", "TBA", "N/A", ""):
        job = ""
    return name, mentor, topic, job


def _group_member(L, runs, group_word):
    """The name printed just above '<group_word> Research Group', and that line's x position."""
    for i, l in enumerate(L):
        if group_word.lower() in l.lower() and "research group" in l.lower() and i > 0:
            nm = L[i - 1]
            x = next((r[1] for r in runs if group_word.lower() in r[0].lower()), None)
            return nm, x
    return "", None


def _sentence(l):
    """A line of running text (mostly lower-case words), as opposed to a heading or a name."""
    w = l.split()
    return len(w) >= 6 and sum(1 for x in w if x[:1].islower()) / len(w) > 0.4


def _award(L, runs, cfg):
    gw = cfg.get("group_word", "Evanseck")
    name, x = _group_member(L, runs, gw)
    people = {L[i - 1] for i, l in enumerate(L) if "research group" in l.lower() and i > 0}
    head, about, i = [], [], 0
    while i < len(L) and not (L[i] in people or "research group" in L[i].lower() or _sentence(L[i])):
        head.append(L[i]); i += 1
    while i < len(L) and not (L[i] in people or "research group" in L[i].lower()):
        about.append(L[i]); i += 1
    about = _join(about)
    award_line = next((h for h in head if re.search(r"award|fellowship|prize|of the year|medal", h, re.I)), "")
    event = " ".join(h for h in head if h != award_line)
    caption = f"{award_line}, {event}" if award_line and event else (award_line or " ".join(head))
    tail_start = max((i for i, l in enumerate(L) if "research group" in l.lower()), default=len(L))
    note = _join([l for l in L[tail_start + 1:] if l not in people])
    tag = "Fellowship" if re.search(r"fellowship", caption, re.I) else "Award"
    return name, caption, about, note, tag, x


def _presentation(L):
    caps = [l for l in L if l.isupper() and l.isalpha() and len(l) > 1]
    name = " ".join(caps).title() if caps else next((l for l in L if NAME.match(l) and "Research" not in l and "B.S." not in l), "")
    prog = next((l for l in L if l.startswith(("B.S.", "B.A.", "M.S."))), "")
    year = next((l for l in L if l.lower() in ("freshman", "sophomore", "junior", "senior")), "")
    prog = re.sub(r"\s+Major$", "", prog) + (f", {year.lower()}" if year else "")
    event = next((l for l in reversed(L) if "," in l and not l.startswith(("B.S.", "B.A."))), "")
    return name, event, prog


def understand(path, slide, cfg):
    """What one slide says, as a tile: tag, title, caption line, gold line, details, photos."""
    fixes = cfg.get("fix", {})
    L = [_fix(l, fixes) for l in slide["lines"]]
    text = " ".join(L)
    it = {"tag": "Achievement", "title": "", "line": "", "src": "", "dl": [], "captions": []}
    if re.search(r"hooding", text, re.I):
        it.update(tag="Ceremony", title="Ph.D. hooding ceremonies", line="Celebrating the department's newest doctors", group="hooding")
        it["captions"] = ["Ph.D. hooding ceremony"]
    elif re.search(r"Mentor\s*:", text, re.I) and re.search(r"Topic\s*:", text, re.I):
        name, mentor, topic, job = _defense(L)
        mentor = re.sub(r"Dr\.\s+Jeff(ery|rey)?\s+Evanseck", "Dr. Jeff Evanseck", mentor)
        it.update(tag="Ph.D. defense", title=name, line=f"Now at {job}" if job else topic,
                  src=f"Mentor: {re.sub(r'Dr. Jeff Evanseck', 'Dr. Evanseck', mentor)}" if mentor else "")
        it["dl"] = [x for x in [["Mentor", mentor], ["Thesis", topic], ["Now at", job]] if x[1]]
        it["captions"] = [f"{name}'s Ph.D. defense"]
    elif re.search(r"research group", text, re.I) and not re.search(r"\bB\.S\.", text):
        name, caption, about, note, tag, x = _award(L, slide["runs"], cfg)
        it.update(tag=tag, title=name, line=caption)
        it["dl"] = [x2 for x2 in [[tag, caption], ["About", about], ["Note", note]] if x2[1]]
        it["captions"] = [name]
        it["_name_x"] = x
        it["_people"] = sum(1 for l in L if "research group" in l.lower())
    elif re.search(r"\bB\.S\.|\bB\.A\.", text):
        name, event, prog = _presentation(L)
        it.update(tag="Presentation", title=name, line=event, src=prog)
        it["dl"] = [x for x in [["Event", event], ["Program", prog]] if x[1]]
        it["captions"] = [f"{name} at {event.split(',')[0]}" if event else name]
    else:
        it.update(title=L[0] if L else path.stem, line=L[1] if len(L) > 1 else "")
    return it


def _date_from_name(stem):
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?=[-_ ]|$)", stem)
    if not m:
        return None, ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    try:
        return datetime.date(y, mo, d), (f"{MONTHS[mo - 1]} {d}, {y}" if m.group(3) else f"{MONTHS[mo - 1]} {y}")
    except ValueError:
        return None, ""


# ---------------------------------------------------------------- all slides -> tiles
def collect(content_dir, out_dir, cfg, save_photo, warn):
    """Read every PDF in content/hall-of-fame and return the tiles, newest first.
    save_photo(PIL image, name) writes a web copy and returns its path for the page."""
    import yaml
    folder = content_dir / "hall-of-fame"
    if not folder.is_dir():
        return []
    need = cfg.get("require", "Evanseck")
    items, groups = [], {}
    for pdf in sorted(folder.glob("*.pdf")):
        side = pdf.with_suffix(".yml")
        over = {}
        if side.is_file():
            try:
                over = yaml.safe_load(side.read_text(encoding="utf-8")) or {}
            except Exception as e:
                warn(side, f"could not read ({e}); ignored.")
        if over.get("hide"):
            continue
        try:
            slide = read_slide(pdf)
        except Exception as e:
            warn(pdf, f"could not read this PDF ({e}); skipped.")
            continue
        it = understand(pdf, slide, cfg)
        photos = slide["photos"]
        if it.get("_people", 0) > 1 and it.get("_name_x") is not None and photos:
            # several people on an award slide: keep the photo standing above the group member's name
            photos = [min(photos, key=lambda p: abs((p["x"] + p["w"] / 2) - it["_name_x"]))]
        it["date"], it["when"] = _date_from_name(pdf.stem)
        it["exported"] = slide.get("exported")
        if over.get("date"):
            try:
                d = datetime.date.fromisoformat(str(over["date"]))
                it["date"], it["when"] = d, f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
            except ValueError:
                warn(side, "date should look like 2025-11-13.")
        for k in ("title", "line", "tag", "src"):
            if over.get(k):
                it[k] = str(over[k])
        it["text"] = slide["text"]
        it["pdf"] = pdf
        slug = re.sub(r"[^a-z0-9]+", "-", pdf.stem.lower()).strip("-")
        cap = it["captions"][0] if it["captions"] else it["title"]
        it["photos"] = [(p["img"], cap, f"{slug}-{k + 1}") for k, p in enumerate(photos)]
        pick = over.get("photo")
        if isinstance(pick, int) and 1 <= pick <= len(it["photos"]):
            it["photos"].insert(0, it["photos"].pop(pick - 1))
        it["slug"] = "hof-" + slug
        if it.get("group"):
            if it["group"] in groups:
                g = groups[it["group"]]
                g["photos"] += it["photos"]
                g["text"] += " " + it["text"]
                g["dl"] = g["dl"] or it["dl"]
                if it["date"] and (not g["date"] or it["date"] > g["date"]):
                    g["date"], g["when"] = it["date"], it["when"]
                if it["exported"] and (not g["exported"] or it["exported"] > g["exported"]):
                    g["exported"] = it["exported"]
                continue
            groups[it["group"]] = it
        items.append(it)
    keep = []
    for it in items:
        if need and need.lower() not in it["text"].lower():
            continue                                   # e.g. another faculty member's grant
        if not it["title"]:
            warn(it["pdf"], "couldn't find a name or title on this slide; add a .yml file with a title line.")
            continue
        keep.append(it)
    for it in keep:
        it["photos"] = [(save_photo(im, name), cap) for im, cap, name in it["photos"]]
    # newest first: the date in the file name (or .yml) if there is one, else when the slide was exported
    def when(i):
        if i["date"]:
            return datetime.datetime.combine(i["date"], datetime.time())
        return i["exported"] or datetime.datetime.min
    keep.sort(key=lambda i: (when(i), i["pdf"].name), reverse=True)
    return keep
