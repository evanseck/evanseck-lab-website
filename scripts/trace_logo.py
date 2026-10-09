"""
Trace the circuit lines of the hexagon logo so the home page can send gold signals along them.

Only needed again if the logo changes. Run by hand:
    pip install scikit-image pillow numpy
    python scripts/trace_logo.py
It reads the "mark" image named in content/site.yml and writes data/logo-circuit.json
(the line shapes, in the image's own pixel coordinates). The site build reads that file.
"""
import json
import pathlib

import numpy as np
import yaml
from PIL import Image
from skimage.measure import approximate_polygon, label
from skimage.morphology import skeletonize

ROOT = pathlib.Path(__file__).resolve().parent.parent
site = yaml.safe_load((ROOT / "content" / "site.yml").read_text(encoding="utf-8"))
SRC = ROOT / site.get("mark", "images/logo-mark.png")
OUT = ROOT / "data" / "logo-circuit.json"

im = Image.open(SRC).convert("RGBA")
W, H = im.size
a = np.asarray(im).astype(int)
r, g, b, alpha = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
dark = (alpha > 128) & (r + g + b < 200)               # black lines only (not the gold dots)

# Keep only the connected circuit (leaves out the separate E R G letters).
lab = label(dark, connectivity=2)
sizes = np.bincount(lab.ravel()); sizes[0] = 0
dark = lab == sizes.argmax()

# Thin the lines down to one-pixel-wide centre lines.


skel = skeletonize(dark)

pts = set(zip(*np.nonzero(skel)))
def nbrs(p):
    y, x = p
    return [(y + dy, x + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy or dx) and (y + dy, x + dx) in pts]
deg = {p: len(nbrs(p)) for p in pts}
nodes = {p for p, d in deg.items() if d != 2}

# Walk every chain of pixels between two nodes.
seen, edges = set(), []
for n in nodes:
    for q in nbrs(n):
        if (n, q) in seen:
            continue
        chain, prev, cur = [n], n, q
        while True:
            chain.append(cur)
            seen.add((prev, cur)); seen.add((cur, prev))
            if cur in nodes:
                break
            nxt = [z for z in nbrs(cur) if z != prev and (cur, z) not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
        if len(chain) >= 12:
            edges.append(chain)

def to_d(chain):
    poly = approximate_polygon(np.array([(x, y) for y, x in chain], float), tolerance=1.2)
    return "M" + " L".join(f"{x:.0f} {y:.0f}" for x, y in poly)

def length(chain):
    c = np.array(chain, float)
    return float(np.hypot(*np.diff(c, axis=0).T).sum())

# Join edges into longer routes: from each loose end, keep going as straight as possible.
by_end = {}
for i, e in enumerate(edges):
    by_end.setdefault(e[0], []).append((i, False))
    by_end.setdefault(e[-1], []).append((i, True))
def near(p):
    y, x = p
    return [k for k in by_end if abs(k[0] - y) <= 2 and abs(k[1] - x) <= 2]

used, routes = set(), []
starts = sorted(range(len(edges)), key=lambda i: -length(edges[i]))
for i in starts:
    if i in used:
        continue
    route = list(edges[i]); used.add(i)
    for _ in range(12):
        end = route[-1]
        d0 = np.subtract(route[-1], route[max(0, len(route) - 8)])
        best = None
        for k in near(end):
            for j, rev in by_end[k]:
                if j in used:
                    continue
                e = edges[j][::-1] if rev else edges[j]
                d1 = np.subtract(e[min(7, len(e) - 1)], e[0])
                score = float(np.dot(d0, d1)) / (np.linalg.norm(d0) * np.linalg.norm(d1) + 1e-9)
                if best is None or score > best[0]:
                    best = (score, j, e)
        if not best or best[0] < -0.2:
            break
        used.add(best[1]); route += best[2][1:]
    if length(route) > 40:
        routes.append(route)

routes.sort(key=length, reverse=True)

# Gold dots: their centres and sizes, so the site can make them glow.
gold = (alpha > 128) & (r > 150) & (b < 120) & (r - b > 60)
glab = label(gold, connectivity=2)
pads = []
for k in range(1, glab.max() + 1):
    ys, xs = np.nonzero(glab == k)
    if len(ys) > 60:
        pads.append({"x": round(float(xs.mean()), 1), "y": round(float(ys.mean()), 1),
                     "r": round(float(np.sqrt(len(ys) / np.pi)), 1)})
data = {"source": str(SRC.relative_to(ROOT)), "width": W, "height": H,
        "paths": [{"d": to_d(rt), "length": round(length(rt))} for rt in routes], "pads": pads}
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
print(f"{len(routes)} circuit lines traced from {SRC.name} -> {OUT.relative_to(ROOT)}")
