"""
Biophysical Society (BPS) Annual Meetings: details for the "Seminar reviews" blocks.

The group's BPS abstracts are found with the publications (they appear in Biophysical Journal),
so this script only adds what the abstracts don't say: the meeting's dates and place (from the
Biophysical Society's page for that year's meeting) and the Society's logo.

Runs every day with the other updates (.github/workflows/site.yml), or by hand:
    python scripts/update_bps.py
Saves data/bps.json and images/bps/society-logo.png.
To use a different picture for one year, upload images/news/bps-<year>-logo.png.
Photos named images/news/bps-<year>-1.jpg, -2.jpg ... are shown with that year's meeting.
"""
import datetime
import html
import io
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "bps.json"
UA = {"User-Agent": "evanseck-group-website (GitHub Action; BPS meeting details)"}
LOGO = "https://www.biophysics.org/Portals/_default/skins/biophysics/images/sticky_03.png"
MONTHS = "January|February|March|April"


def get(url, binary=False):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def bps_years():
    pubs = json.loads((ROOT / "data" / "publications.json").read_text(encoding="utf-8")).get("publications", [])
    return sorted({str(p.get("year")) for p in pubs
                   if p.get("type") == "conference-abstract" and "biophysical journal" in (p.get("venue") or "").lower()})


def meeting_details(year):
    url = f"https://www.biophysics.org/{year}meeting/general-info"
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", get(url))))
    out = {"page": f"https://www.biophysics.org/{year}meeting"}
    m = re.search(rf"({MONTHS})\s+(\d{{1,2}})\s*[-–]\s*(\d{{1,2}}),\s*{year}", text)
    if m:
        out["dates"] = f"{m.group(1)} {m.group(2)}–{m.group(3)}, {year}"
        out["start"] = datetime.datetime.strptime(f"{m.group(1)} {m.group(2)} {year}", "%B %d %Y").date().isoformat()
    m = re.search(r"([A-Z][\w&' .]+?(?:Center|Centre|Convention[\w ]*|Hall)),\s*\d+[^,]*,\s*([A-Z][a-zA-Z .]+),\s*([A-Z][a-zA-Z ]+?)\s+\d{5}", text)
    if m:
        out["venue"] = m.group(1).strip()
        out["location"] = f"{m.group(2).strip()}, {m.group(3).strip()}"
    return out


def main():
    state = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"years": {}}
    for year in bps_years():
        e = state["years"].get(year, {})
        if e.get("dates") and e.get("location"):
            continue
        try:
            e.update({k: v for k, v in meeting_details(year).items() if v})
            print(f"BPS {year}: {e.get('dates', '?')}, {e.get('location', '?')}")
        except Exception as ex:
            print(f"  BPS {year}: meeting page not read ({ex})")
        state["years"][year] = e
    logo = ROOT / "images" / "bps" / "society-logo.png"
    if not logo.exists():
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(get(LOGO, binary=True)))
            im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
            logo.parent.mkdir(parents=True, exist_ok=True)
            im.save(logo, "PNG", optimize=True)
            print(f"  saved {logo.relative_to(ROOT)}")
        except Exception as ex:
            print(f"  Society logo not saved ({ex})")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the site build over this
        print(f"::warning::BPS check skipped: {e}")
        sys.exit(0)
