# Journal covers

Covers appear under a research area on the Research page, linked to the paper. List them
under `covers:` in that area's file in `content/research/`. Three ways to give the image:

1. **The journal's issue page** (easiest). The site finds and downloads the cover itself:
   ```
   covers:
     - issue: https://pubs.acs.org/apcach/issue/3/1
       doi: 10.1021/acsphyschemau.2c00032
       label: "ACS Physical Chemistry Au, January 2023"
   ```
2. **The image address.** On the issue page, right-click the cover, choose
   "Copy image address", and paste it: `image: https://...largecover.jpg`
3. **An uploaded file.** Upload the image to this folder and write
   `image: images/covers/name.jpg`

`doi` is the paper the cover belongs to. `label` is optional; without it the journal name and
year are filled in from the DOI. If a cover cannot be downloaded, the Actions tab shows a
yellow warning and the rest of the site is unaffected; use option 2 or 3 instead.
