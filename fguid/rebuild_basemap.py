#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Régénère le fond de carte OSM (z9) pour couvrir toutes les traces de tous les
appareils, et l'injecte dans data_base.json ET index.html (const D)."""
import base64, io, json, math, os, re, sys, time

import requests
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "index.html")
BASE_PATH = os.path.join(HERE, "data_base.json")

s = requests.Session()
s.headers.update({"User-Agent": "helico-parapente-page/1.0 (github.io)"})

base = json.load(open(BASE_PATH))
la = [90.0, -90.0]; lo = [180.0, -180.0]
for a in base["aircraft"]:
    for dd in a["days"]:
        for sg in dd["segs"]:
            for p in sg["pts"]:
                lo[0] = min(lo[0], p[0]); lo[1] = max(lo[1], p[0])
                la[0] = min(la[0], p[1]); la[1] = max(la[1], p[1])
la = [la[0] - 0.08, la[1] + 0.08]; lo = [lo[0] - 0.08, lo[1] + 0.08]
print(f"emprise traces+marge : lat {la[0]:.2f}..{la[1]:.2f} lon {lo[0]:.2f}..{lo[1]:.2f}")

Z = 9; N = 2 ** Z
def xt(lon): return int((lon + 180) / 360 * N)
def yt(lat): return int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * N)
x0, x1 = xt(lo[0]), xt(lo[1])
y0, y1 = yt(la[1]), yt(la[0])          # y0 = nord
W, H = (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256
print(f"tuiles x {x0}-{x1}, y {y0}-{y1} -> {W}x{H}px ({(x1-x0+1)*(y1-y0+1)} tuiles)")
img = Image.new("RGB", (W, H), (240, 240, 235))
ok = 0
for x in range(x0, x1 + 1):
    for y in range(y0, y1 + 1):
        for a in range(3):
            try:
                r = s.get(f"https://tile.openstreetmap.org/{Z}/{x}/{y}.png", timeout=20)
                if r.status_code == 200:
                    img.paste(Image.open(io.BytesIO(r.content)).convert("RGB"),
                              ((x - x0) * 256, (y - y0) * 256))
                    ok += 1; break
                time.sleep(1 + a)
            except Exception:
                time.sleep(1 + a)
        time.sleep(0.12)
print(f"tuiles OK : {ok}")
buf = io.BytesIO(); img.save(buf, "JPEG", quality=80)
uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
bm = {"uri": uri,
      "lon0": x0 / N * 360 - 180, "lon1": (x1 + 1) / N * 360 - 180,
      "lat0": math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y0 / N)))),
      "lat1": math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y1 + 1) / N))))}
print("bounds:", {k: round(v, 4) for k, v in bm.items() if k != "uri"}, f"jpeg {len(uri)//1024} Ko")

base["basemap"] = bm
json.dump(base, open(BASE_PATH, "w"), ensure_ascii=False, separators=(",", ":"))

html = open(PAGE, encoding="utf-8").read()
m = re.search(r"^const D = (.*);$", html, re.M)
D = json.loads(m.group(1))
D["basemap"] = bm
html = html[:m.start()] + "const D = " + json.dumps(D, ensure_ascii=False, separators=(",", ":")) + ";" + html[m.end():]
open(PAGE, "w", encoding="utf-8").write(html)
print("fond de carte injecté dans base + page")
