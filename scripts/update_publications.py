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
            return ids + extra
        print(f"::warning::No OpenAlex profile is linked to ORCID {orcid} yet; falling back to a name search.")

    if pinned:
        print(f"Using pinned OpenAlex author IDs: {', '.join(pinned)}")
        return pinned

    initial = CONFIG.get("author_first_initial", "").lower()
    data = api_get("authors", {"search": CONFIG["author_surname"], "per-page": 50})
    ids = []
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
    if not ids:
        sys.exit("No matching author profile found. Add IDs to openalex_author_ids in scripts/config.json.")
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
    return works


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


def process(works):
    include = set(CONFIG.get("include_types") or [])
    hide_dois = {d.lower().replace("https://doi.org/", "") for d in CONFIG.get("hide_dois", [])}
    hide_titles = [t.lower() for t in CONFIG.get("hide_titles_containing", [])]

    best = {}
    for w in works:
        e = to_entry(w)
        if not e or (include and e["_type"] not in include):
            continue
        doi = e["doi"].lower().replace("https://doi.org/", "")
        if doi and doi in hide_dois:
            continue
        if any(t in e["title"].lower() for t in hide_titles):
            continue
        # Merge duplicates (same title), preferring the record with a DOI and a journal name.
        k = title_key(e["title"])
        score = (bool(e["doi"]), bool(e["venue"]), bool(e["pages"]))
        if k not in best or score > best[k][0]:
            best[k] = (score, e)

    pubs = [e for _, e in best.values()]
    for e in pubs:
        e.pop("_type", None)
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

    if pubs == old_pubs and old.get("highlight") == CONFIG.get("highlight_names"):
        print("No changes.")
        return

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "OpenAlex",
        "updated": datetime.date.today().isoformat(),
        "author_ids": ids,
        "highlight": CONFIG.get("highlight_names", []),
        "publications": pubs,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote data/{OUT.name}")


if __name__ == "__main__":
    main()
