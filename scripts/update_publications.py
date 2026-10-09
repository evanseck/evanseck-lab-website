"""
Fetch Jeffrey Evanseck's publications from OpenAlex and write data/publications.json.

Runs weekly via .github/workflows/update-publications.yml, or by hand:
    python scripts/update_publications.py

Uses only the Python standard library. Settings live in scripts/config.json.
"""
import datetime
import html
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8"))
OUT = ROOT / "data" / "publications.json"
API = "https://api.openalex.org"


def api_get(path, params):
    params = dict(params)
    if CONFIG.get("contact_email"):
        params["mailto"] = CONFIG["contact_email"]
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        params["api_key"] = key
    url = f"{API}/{path}?{urllib.parse.urlencode(params, safe=':|*,')}"
    req = urllib.request.Request(url, headers={"User-Agent": "evanseck-group-website (GitHub Action)"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 3:
                raise
            wait = 3 * (2 ** attempt)
            print(f"  request failed ({e}); retrying in {wait}s")
            time.sleep(wait)


def find_author_ids():
    """Use pinned IDs from config, or search OpenAlex for matching author profiles.
    OpenAlex sometimes splits one person across several profiles, so all matches are used."""
    pinned = CONFIG.get("openalex_author_ids") or []
    orcid = (CONFIG.get("orcid") or "").strip().replace("https://orcid.org/", "")
    surname = CONFIG["author_surname"].lower()

    if orcid:
        data = api_get("authors", {"filter": f"orcid:{orcid}"})
        found = data.get("results", [])
        if found:
            ids = []
            for a in found:
                name = a.get("display_name", "")
                print(f"ORCID {orcid} -> OpenAlex {a['id'].rsplit('/', 1)[-1]}  {name}  ({a.get('works_count', 0)} works)")
                if surname not in name.lower():
                    sys.exit(f"Stopping: ORCID {orcid} belongs to '{name}', not {CONFIG['author_surname']}. "
                             "Check the 'orcid' value in scripts/config.json.")
                ids.append(a["id"].rsplit("/", 1)[-1])
            extra = [i for i in pinned if i not in ids]
            if extra:
                print(f"Also including pinned author IDs: {', '.join(extra)}")
            # OpenAlex often files conference abstracts under a second, unlinked profile.
            split = [i for i in name_matched_ids() if i not in ids + extra]
            if split:
                print(f"Also including profiles with the same name: {', '.join(split)}")
            return ids + extra + split
        print(f"::warning::No OpenAlex profile is linked to ORCID {orcid} yet; falling back to a name search.")

    if pinned:
        print(f"Using pinned OpenAlex author IDs: {', '.join(pinned)}")
        return pinned

    ids = name_matched_ids(verbose=True)
    if not ids:
        sys.exit("No matching author profile found. Add IDs to openalex_author_ids in scripts/config.json.")
    return ids


def name_matched_ids(verbose=False):
    """OpenAlex author profiles whose name matches the surname and first initial."""
    surname = CONFIG["author_surname"].lower()
    initial = CONFIG.get("author_first_initial", "").lower()
    try:
        data = api_get("authors", {"search": CONFIG["author_surname"], "per-page": 50})
    except Exception as e:
        print(f"  name search skipped ({e})")
        return []
    ids = []
    if verbose:
        print("OpenAlex author profiles found:")
    for a in data.get("results", []):
        name = a.get("display_name", "")
        parts = name.lower().split()
        ok = surname in name.lower() and (not initial or (parts and parts[0].startswith(initial)))
        inst = ", ".join(i.get("display_name", "") for i in (a.get("last_known_institutions") or []))
        print(f"  {'USE ' if ok else 'skip'} {a['id'].rsplit('/', 1)[-1]}  {name}  "
              f"({a.get('works_count', 0)} works; {inst or 'no institution listed'})")
        if ok:
            ids.append(a["id"].rsplit("/", 1)[-1])
    return ids


def fetch_works(ids):
    works, cursor = [], "*"
    while cursor:
        data = api_get("works", {
            "filter": f"author.id:{'|'.join(ids)}",
            "per-page": 200,
            "cursor": cursor,
            "select": "id,doi,display_name,publication_year,publication_date,type,authorships,primary_location,biblio",
        })
        results = data.get("results", [])
        works.extend(results)
        cursor = data.get("meta", {}).get("next_cursor") if results else None
    seen = {w["id"] for w in works}

    # Recent records that carry the name but are not attached to any profile yet
    # (common for conference abstracts, e.g. Biophysical Society meeting abstracts).
    since = f"{datetime.date.today().year - 3}-01-01"
    try:
        data = api_get("works", {
            "filter": f"raw_author_name.search:{CONFIG['author_surname']},from_publication_date:{since}",
            "per-page": 200,
            "select": "id,doi,display_name,publication_year,publication_date,type,authorships,primary_location,biblio",
        })
        added = 0
        for w in data.get("results", []):
            if w["id"] not in seen and has_author_name(w):
                works.append(w); seen.add(w["id"]); added += 1
        print(f"{added} more record(s) found by author name since {since}")
    except Exception as e:
        print(f"  author-name search skipped ({e})")

    # DOIs added by hand in scripts/config.json ("extra_dois")
    for doi in CONFIG.get("extra_dois", []):
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi).strip(), flags=re.I)
        if not doi:
            continue
        w = None
        try:
            w = api_get(f"works/doi:{doi}", {})
        except Exception:
            w = crossref_work(doi)
        if w and w.get("id") not in seen:
            works.append(w); seen.add(w.get("id"))
            print(f"  added by hand: {doi}")
        elif not w:
            print(f"::warning::Could not find DOI {doi} in OpenAlex or Crossref.")
    return works


def has_author_name(w):
    s = CONFIG["author_surname"].lower()
    for a in w.get("authorships") or []:
        names = [(a.get("author") or {}).get("display_name") or "", a.get("raw_author_name") or ""]
        if any(s in n.lower() for n in names):
            return True
    return False


def crossref_work(doi):
    """Turn a Crossref record into the same shape as an OpenAlex work (for very new DOIs)."""
    try:
        req = urllib.request.Request(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}",
                                     headers={"User-Agent": "evanseck-group-website (GitHub Action)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            m = json.load(r)["message"]
    except Exception:
        return None
    parts = (m.get("published") or m.get("issued") or {}).get("date-parts", [[None]])[0]
    year = parts[0]
    date = "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(parts)) if year else ""
    if len(parts) < 3:
        date = f"{year}-{(parts + [1, 1])[1]:02d}-01" if year else ""
    page = m.get("page") or ""
    first, _, last = page.partition("-")
    kind = {"journal-article": "article", "proceedings-article": "proceedings-article"}.get(m.get("type"), "other")
    return {
        "id": "crossref:" + doi,
        "doi": "https://doi.org/" + doi,
        "display_name": (m.get("title") or [""])[0],
        "publication_year": year,
        "publication_date": date,
        "type": kind,
        "authorships": [{"author": {"display_name": f'{a.get("given", "")} {a.get("family", "")}'.strip(),
                                    "orcid": a.get("ORCID")}} for a in m.get("author", [])],
        "primary_location": {"source": {"display_name": (m.get("container-title") or [""])[0]}},
        "biblio": {"volume": m.get("volume"), "first_page": first or None, "last_page": last or None},
    }


def clean(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def title_key(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())


def to_entry(w):
    title = clean(w.get("display_name"))
    year = w.get("publication_year")
    if not title or not year:
        return None
    source = ((w.get("primary_location") or {}).get("source") or {})
    biblio = w.get("biblio") or {}
    first, last = biblio.get("first_page"), biblio.get("last_page")
    pages = f"{first}–{last}" if first and last and first != last else (first or "")
    return {
        "title": title,
        "year": year,
        "date": w.get("publication_date") or f"{year}-01-01",
        "authors": [clean((a.get("author") or {}).get("display_name")) for a in (w.get("authorships") or [])],
        "orcids": [((a.get("author") or {}).get("orcid") or "").replace("https://orcid.org/", "") for a in (w.get("authorships") or [])],
        "venue": clean(source.get("display_name")),
        "volume": biblio.get("volume") or "",
        "pages": pages,
        "doi": w.get("doi") or "",
        "_type": w.get("type"),
    }


COVER_TITLE = re.compile(r"^\s*(front|back|inside(?: front| back)?|outside(?: front| back)?|supplementary|journal)?\s*"
                         r"(cover|cover picture|cover image|cover feature|cover art|frontispiece)\b", re.I)


def cover_items(works):
    """Publisher records for journal covers ('Front Cover: ...', 'Cover Picture: ...')."""
    out = {}
    for w in works:
        e = to_entry(w)
        if e and COVER_TITLE.search(e["title"]):
            e.pop("_type", None)
            out[e["doi"] or e["title"]] = e
    return sorted(out.values(), key=lambda e: e["date"], reverse=True)


def process(works):
    include = set(CONFIG.get("include_types") or [])
    hide_dois = {d.lower().replace("https://doi.org/", "") for d in CONFIG.get("hide_dois", [])}
    hide_titles = [t.lower() for t in CONFIG.get("hide_titles_containing", [])]

    best = {}
    for w in works:
        e = to_entry(w)
        if not e or (include and e["_type"] not in include) or COVER_TITLE.search(e["title"]):
            continue
        doi = e["doi"].lower().replace("https://doi.org/", "")
        if doi and doi in hide_dois:
            continue
        if any(t in e["title"].lower() for t in hide_titles):
            continue
        # Merge duplicates (same title), preferring the record with a DOI and a journal name.
        k = title_key(e["title"])
        score = (e["_type"] in ("article", "review", "letter", "book-chapter"),
                 bool(e["doi"]), bool(e["venue"]), bool(e["pages"]))
        if k not in best or score > best[k][0]:
            best[k] = (score, e)

    pubs = [e for _, e in best.values()]
    for e in pubs:
        e["type"] = e.pop("_type", "") or ""
    pubs.sort(key=lambda e: (e["date"], e["title"]), reverse=True)
    return pubs


def main():
    ids = find_author_ids()
    works = fetch_works(ids)
    pubs = process(works)
    print(f"{len(works)} records from OpenAlex -> {len(pubs)} publications after filtering")

    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    old_pubs = old.get("publications", [])

    # Safety checks so an API hiccup never wipes the page.
    if not pubs:
        sys.exit("OpenAlex returned no publications; leaving the existing file unchanged.")
    if old.get("source") == "OpenAlex" and len(pubs) < 0.7 * len(old_pubs):
        sys.exit(f"Publication count dropped from {len(old_pubs)} to {len(pubs)}; "
                 "leaving the existing file unchanged. Check the log above.")

    if pubs == old_pubs and old.get("highlight") == CONFIG.get("highlight_names") and cover_items(works) == old.get("cover_items", []):
        print("No changes.")
        return

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "OpenAlex",
        "updated": datetime.date.today().isoformat(),
        "author_ids": ids,
        "highlight": CONFIG.get("highlight_names", []),
        "publications": pubs,
        "cover_items": cover_items(works),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote data/{OUT.name}")


if __name__ == "__main__":
    main()
