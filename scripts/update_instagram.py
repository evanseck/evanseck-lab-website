"""
Bring the lab's Instagram posts onto the website (Announcements tab, as photo tiles that open
the post on Instagram).

Runs with the other updates (.github/workflows/site.yml), or by hand:
    INSTAGRAM_TOKEN=... python scripts/update_instagram.py

Setup (once): the lab account must be a professional (Creator or Business) Instagram account,
and its access token is stored in the repository as a secret named INSTAGRAM_TOKEN
(Settings -> Secrets and variables -> Actions). See README / the setup notes.

What it does each run:
  * keeps the token alive (Instagram tokens last 60 days; each run renews it);
  * reads the account's posts, saves a copy of each photo in images/instagram/ (Instagram's own
    image links expire), and lists them in data/instagram.json;
  * posts deleted on Instagram are removed from the site; to hide one, add its link to
    "hide" in scripts/config.json ("instagram").
Uses only the Python standard library, plus Pillow (already installed for the site).
"""
import datetime
import io
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8")).get("instagram", {})
OUT = ROOT / "data" / "instagram.json"
IMG = ROOT / "images" / "instagram"
API = "https://graph.instagram.com"
FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"


def call(path, **params):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "evanseck-group-website"}),
                                timeout=40) as r:
        return json.load(r)


def save_photo(url, media_id):
    from PIL import Image, ImageOps
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    IMG.mkdir(parents=True, exist_ok=True)
    path = IMG / f"{media_id}.jpg"
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((1080, 1350))
        im.save(path, "JPEG", quality=85, optimize=True, progressive=True)
    return str(path.relative_to(ROOT))


def main():
    token = os.environ.get("INSTAGRAM_TOKEN", "").strip()
    if not token:
        print("No INSTAGRAM_TOKEN secret yet; Instagram posts are not being collected.")
        return
    state = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    posts = {p["id"]: p for p in state.get("posts", [])}

    # Keep the token alive (allowed once it is a day old; extends it to 60 days from now).
    try:
        r = call("refresh_access_token", grant_type="ig_refresh_token", access_token=token)
        days = int(r.get("expires_in", 0)) // 86400
        state["token_valid_until"] = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
        if r.get("access_token") and r["access_token"] != token:
            # Never print tokens: logs of a public repository are public.
            print("::warning::Instagram issued a different token. Generate a fresh long-lived token "
                  "and save it in the INSTAGRAM_TOKEN secret before the old one expires.")
    except Exception as e:
        print(f"  token not renewed this run ({e})")

    me = call("me", fields="username", access_token=token)
    state["username"] = me.get("username", "")
    found, after, limit = [], None, int(CONFIG.get("max_posts", 60))
    while len(found) < limit:
        params = {"fields": FIELDS, "limit": 25, "access_token": token}
        if after:
            params["after"] = after
        page = call("me/media", **params)
        found += page.get("data", [])
        after = (page.get("paging") or {}).get("cursors", {}).get("after")
        if not after or not (page.get("paging") or {}).get("next"):
            break

    hide = {h.strip().rstrip("/") for h in CONFIG.get("hide", [])}
    keep = {}
    for m in found[:limit]:
        if m["id"] in hide or (m.get("permalink") or "").rstrip("/") in hide:
            continue
        old = posts.get(m["id"], {})
        img = old.get("image", "")
        if not img or not (ROOT / img).is_file():
            src = m.get("thumbnail_url") if m.get("media_type") == "VIDEO" else m.get("media_url")
            try:
                img = save_photo(src, m["id"]) if src else ""
            except Exception as e:
                print(f"  photo not saved for {m.get('permalink')}: {e}")
                img = ""
        keep[m["id"]] = {"id": m["id"], "permalink": m.get("permalink", ""), "caption": m.get("caption", ""),
                         "type": m.get("media_type", ""), "date": (m.get("timestamp") or "")[:10], "image": img}
        if m["id"] not in posts:
            print(f"  new post: {m.get('permalink')}")
    for gone in set(posts) - set(keep):           # deleted on Instagram, or hidden
        f = ROOT / posts[gone].get("image", "")
        if posts[gone].get("image") and f.is_file():
            f.unlink()
    OUT.parent.mkdir(exist_ok=True)
    state["updated"] = datetime.date.today().isoformat()
    state["posts"] = sorted(keep.values(), key=lambda p: p["date"], reverse=True)
    OUT.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(keep)} Instagram post(s) from @{state['username']}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the site build over this
        print(f"::warning::Instagram update skipped: {e}")
        sys.exit(0)
