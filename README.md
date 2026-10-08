# Evanseck Research Group website

All of the writing on the site lives in the **`content/`** folder as plain-text files.
Change a file, and GitHub rebuilds and republishes the site within about two minutes.
Nobody needs to touch any code.

```
content/
  site.yml          group name, tagline, logo, contact info
  home.md           Home page text
  research.md       intro paragraph on the Research page
  research/         one file per research area
  people/           one file per person (PI, students, alumni)
  news/             one file per news item (shown on the Home page)
  teaching.md       Teaching & Outreach page
  join.md           Join Us page
images/             logo and photos (people photos go in images/people/)
```

Files whose names start with `_` (like `_TEMPLATE.md`) are templates. They never appear on the site.

---

## For group members: add yourself

1. On the repository page, open `content/people/` and click **`_TEMPLATE.md`**. Click the
   copy icon to copy everything.
2. Go back to `content/people/` and click **Add file → Create new file**.
3. Name it after yourself, like `sarah-kim.md`, and paste in the template.
4. Fill it in. Keep the part before each colon (`name:`, `role:` ...) and replace what's after it.
5. Click **Commit changes**. Your profile is live in about two minutes.

**Photo (optional):** in `images/people/` click **Add file → Upload files**, upload your
photo (square-ish, under 1 MB), then put its path in your file:
`photo: images/people/sarah-kim.jpg`

**Updating later:** open your file, click the pencil icon, edit, and commit.
**When you graduate:** change `group:` to `alumni` and fill in `years:` and `now:`.

Your name is **bolded automatically** in the publication list once your file exists.

No GitHub account? Fill in the template in any text editor and email it (with your photo)
to whoever maintains the site.

### Writing tips

The text below the second `---` is your bio. It's written in Markdown, which is just plain text
with a few extras:

```
Plain paragraphs are separated by a blank line.
*italic*   **bold**   [link text](https://example.com)
```

**If something you type contains a colon, wrap it in quotes** in the top section, e.g.
`project: "RNA dynamics: the s2m element"`. This is the most common mistake.

---

## Posting news

Copy `content/news/_TEMPLATE.md` to a new file named with the date first, like
`2026-10-15-new-paper.md`, set the `date:`, and write a sentence or two. The newest
eight news items appear on the Home page.

## Adding a research area

Copy `content/research/_TEMPLATE.md`. The number at the start of the file name sets the
order (`01-...` comes first).

## Adding the logo

Upload it to `images/` (e.g. `images/logo.png`, ideally with a transparent background),
then in `content/site.yml` set `logo: images/logo.png`. It appears in the banner and the
browser tab.

---

## If something doesn't show up

Go to the **Actions** tab and click the latest run. A yellow warning names the file and the
problem (for example, a missing colon or a photo that wasn't uploaded). A broken file is
skipped, so the rest of the site keeps working. Fix the file and commit again.

## Publications (automatic)

Every Monday, the site fetches Dr. Evanseck's papers from OpenAlex and rebuilds the page.
To refresh immediately: **Actions → Build and publish site → Run workflow**.
Settings are in `scripts/config.json`:

- `orcid`: Dr. Evanseck's ORCID (already filled in). The updater uses it to find exactly his
  OpenAlex profile, so no one else's papers get mixed in. If the ORCID ever points to someone
  else, the update stops and leaves the current list alone.
- `openalex_author_ids`: normally leave empty. Only needed if a real paper is missing because
  OpenAlex filed it under a second profile; add that profile's ID here.
- `hide_dois`: DOIs of papers that aren't his, e.g. `["10.1021/xxxx"]`.
- `highlight_names`: extra last names to bold (current members are bolded automatically).

---

## One-time setup (site maintainer)

1. Upload everything in this folder to the repository's `main` branch, keeping the folders.
   If you uploaded an earlier version of the site, **delete** the old `index.html` at the top
   level and `.github/workflows/update-publications.yml`.
   (The `.github` folder is hidden on Mac. If it doesn't upload, create the workflow on
   github.com: Add file → Create new file → name it `.github/workflows/site.yml` → paste.)
2. **Settings → Pages → Source: "GitHub Actions"**.
3. **Settings → Actions → General → Workflow permissions: "Read and write permissions"**
   (in an organization, an Owner may need to allow this first under the organization's
   Settings → Actions).
4. **Actions → Build and publish site → Run workflow** to do the first build and publication fetch.
5. Give group members access: **Settings → Collaborators** (or add them to the organization)
   with the **Write** role.

### Preview on your own computer (optional)

```
pip install markdown pyyaml
python scripts/build.py
```
Then open `_site/index.html` in a browser.

### Notes

- GitHub pauses scheduled runs in repositories with no activity for 60 days. It emails
  first; click "Enable workflow" on the Actions tab to resume.
- If OpenAlex starts requiring an API key, add a free one under
  Settings → Secrets and variables → Actions as `OPENALEX_API_KEY`.
- Page design is in `templates/style.css`; page structure is in `scripts/build.py`.
