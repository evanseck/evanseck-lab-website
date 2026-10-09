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
CHECK = {"date": datetime.date.today().isoformat(), "crossref": "not run", "crossref_found": [], "name_search": "not run"}


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
        CHECK["name_search"] = f"ok: {added} new"
    except Exception as e:
        print(f"  author-name search skipped ({e})")
        CHECK["name_search"] = f"failed: {e}"

    # Crossref, where publishers register new records (meeting abstracts) first
    try:
        known = {(w.get("doi") or "").lower(): i for i, w in enumerate(works) if w.get("doi")}
        added = 0
        for w in crossref_search():
            d = w["doi"].lower()
            if d not in known:
                known[d] = len(works); works.append(w); added += 1
            elif to_entry(works[known[d]]) is None and to_entry(w) is not None:
                # OpenAlex has the DOI but no usable title or year (common for meeting abstracts)
                works[known[d]] = w; added += 1
        print(f"{added} more record(s) found in Crossref")
        CHECK["crossref"] = f"ok: {len(CHECK['crossref_found'])} match(es), {added} new"
    except Exception as e:
        print(f"  Crossref search skipped ({e})")
        CHECK["crossref"] = f"failed: {e}"

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


def crossref_get(path, params=None):
    params = dict(params or {})
    if CONFIG.get("contact_email"):
        params["mailto"] = CONFIG["contact_email"]
    url = f"https://api.crossref.org/{path}" + (f"?{urllib.parse.urlencode(params, safe=':,')}" if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": "evanseck-group-website (GitHub Action; weekly publication check)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)["message"]
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def crossref_work(doi):
    """A single DOI from Crossref, in the same shape as an OpenAlex work."""
    try:
        return crossref_to_work(crossref_get(f"works/{urllib.parse.quote(doi)}"))
    except Exception:
        return None


def crossref_search():
    """Crossref records with the surname in the author list (last few years).
    Publishers register meeting abstracts here first (e.g. Biophysical Society abstracts in
    Biophysical Journal); OpenAlex can take months to list them, or never links them."""
    since = f"{datetime.date.today().year - 4}-01-01"
    surname = CONFIG["author_surname"].lower()
    initial = CONFIG.get("author_first_initial", "").lower()
    def mine(m):
        for a in m.get("author", []):
            full = f'{a.get("given", "")} {a.get("family", "")} {a.get("name", "")}'.lower()
            if surname in full and (not a.get("given") or not initial or
                                    (a.get("given") or "").lower().startswith(initial)):
                return True
        return False

    out, seen = [], set()
    # A focused search in the journals where the group's meeting abstracts appear (Biophysical
    # Journal for BPS), then a general search across all journals.
    passes = []
    for i in CONFIG.get("abstract_journal_issns", []):
        passes.append({"query.author": CONFIG["author_surname"], "filter": f"issn:{i},from-pub-date:{since}"})
        passes.append({"query.bibliographic": CONFIG["author_surname"], "filter": f"issn:{i},from-pub-date:{since}"})
    passes.append({"query.author": CONFIG["author_surname"], "filter": f"from-pub-date:{since}"})
    for extra in passes:
        offset = 0
        while True:
            msg = crossref_get("works", {"rows": 100, "offset": offset, **extra})
            items = msg.get("items", [])
            for m in items:
                d = (m.get("DOI") or "").lower()
                if d not in seen and mine(m):
                    seen.add(d)
                    out.append(crossref_to_work(m))
                    print(f"  Crossref: {(m.get('title') or [''])[0][:80]}  ({d})")
                    CHECK["crossref_found"].append(f"{(m.get('title') or [''])[0][:90]} ({d})")
            offset += len(items)
            if not items or offset >= min(msg.get("total-results", 0), 500):
                break
            time.sleep(1)
    return out


def crossref_to_work(m):
    doi = m.get("DOI", "")
    parts = (m.get("published") or m.get("issued") or {}).get("date-parts", [[None]])[0]
    year = parts[0]
    date = "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(parts)) if year else ""
    if len(parts) < 3:
        date = f"{year}-{(parts + [1, 1])[1]:02d}-01" if year else ""
    page = m.get("page") or ""
    first, _, last = page.partition("-")
    kind = {"journal-article": "article", "proceedings-article": "proceedings-article"}.get(m.get("type"), "other")
    title = (m.get("title") or [""])[0]
    title = re.sub(r"^\s*BPS\s?\d{4}\s*[–—-]\s*", "", title)   # "BPS2026 – Title" -> "Title"
    return {
        "id": "crossref:" + doi.lower(),
        "doi": "https://doi.org/" + doi,
        "display_name": title,
        "publication_year": year,
        "publication_date": date,
        "type": kind,
        "authorships": [{"author": {"display_name": f'{a.get("given", "")} {a.get("family", "")}'.strip(),
                                    "orcid": re.sub(r"^https?://orcid\.org/", "https://orcid.org/", a.get("ORCID") or "") or None}}
                        for a in m.get("author", [])],
        "primary_location": {"source": {"display_name": (m.get("container-title") or [""])[0]}},
        "biblio": {"volume": m.get("volume"), "first_page": first or None, "last_page": last or None},
    }


def clean(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def title_key(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())


def to_entry(w):
    title = re.sub(r"^\s*BPS\s?\d{4}\s*[–—-]\s*", "", clean(w.get("display_name")))   # "BPS2026 – Title"
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
    # Note why any Crossref match did not make it into the list (saved in last_check)
    have = {p["doi"].lower() for p in pubs}
    CHECK["not_listed"] = []
    for line in CHECK["crossref_found"]:
        d = line.rsplit("(", 1)[-1].rstrip(")").lower()
        if "https://doi.org/" + d in have:
            continue
        recs = [w for w in works if (w.get("doi") or "").lower() == "https://doi.org/" + d]
        why = "; ".join(f'{"no title/year" if to_entry(w) is None else "type " + str(w.get("type"))} '
                        f'[{w.get("id", "")[:30]}]' for w in recs) or "record not kept"
        CHECK["not_listed"].append(f"{d}: {why}")

    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    old_pubs = old.get("publications", [])

    # Safety checks so an API hiccup never wipes the page.
    if not pubs:
        sys.exit("OpenAlex returned no publications; leaving the existing file unchanged.")
    if old.get("source") == "OpenAlex" and len(pubs) < 0.7 * len(old_pubs):
        sys.exit(f"Publication count dropped from {len(old_pubs)} to {len(pubs)}; "
                 "leaving the existing file unchanged. Check the log above.")

    same_check = {k: v for k, v in CHECK.items() if k != "date"} == \
        {k: v for k, v in (old.get("last_check") or {}).items() if k != "date"}
    if pubs == old_pubs and old.get("highlight") == CONFIG.get("highlight_names") and \
            cover_items(works) == old.get("cover_items", []) and same_check:
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
        "last_check": CHECK,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote data/{OUT.name}")


if __name__ == "__main__":
    main()
