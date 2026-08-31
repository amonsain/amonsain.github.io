#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mise à jour automatique de fguid/index.html : ajoute à la page les journées de vol
de F-GUID (hex 395103) publiées dans les archives ADS-B Exchange depuis la dernière
journée intégrée. Fusion additive : jours, passages < 1 km, temps < 10 km, stats.
Ne modifie rien s'il n'y a pas de nouveau jour archivé.
"""
import datetime, json, math, os, re, statistics, sys, time
from zoneinfo import ZoneInfo

import requests

HEX = "395103"
REG = "F-GUID"
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
PARIS = ZoneInfo("Europe/Paris")
MOIS = [None, "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Referer": "https://globe.adsbexchange.com/"}
s = requests.Session(); s.headers.update(UA)

def log(*a): print(*a, flush=True)

def dist_km(la1, lo1, la2, lo2):
    dx = (lo2 - lo1) * 111.32 * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot(dx, (la2 - la1) * 110.57)

def dmy(t):  return datetime.datetime.fromtimestamp(t, PARIS).strftime("%d/%m/%y")
def hm(t):   return datetime.datetime.fromtimestamp(t, PARIS).strftime("%H h %M")

# ---------- page ----------
html = open(PAGE, encoding="utf-8").read()
m = re.search(r"^const D = (.*);$", html, re.M)
if not m: sys.exit("const D introuvable")
D = json.loads(m.group(1))
ac = next(a for a in D["aircraft"] if a["reg"] == REG)
last_day = max(d["d"] for d in ac["days"])
today = datetime.datetime.now(datetime.timezone.utc).date()
start = datetime.date.fromisoformat(last_day) + datetime.timedelta(days=1)
log(f"dernier jour intégré : {last_day} ; fenêtre : {start} -> {today - datetime.timedelta(days=1)}")
if start >= today:
    log("rien à faire"); sys.exit(0)

# ---------- traces des nouveaux jours (archives uniquement) ----------
def fetch_day(d):
    url = (f"https://globe.adsbexchange.com/globe_history/{d.year}/{d:%m}/{d:%d}"
           f"/traces/{HEX[-2:]}/trace_full_{HEX}.json")
    for k in range(3):
        try:
            r = s.get(url, timeout=30)
            if r.status_code == 200: return r.json()
            if r.status_code == 404: return None
            log(f"  {d}: HTTP {r.status_code}"); time.sleep(20 * (k + 1))
        except Exception as e:
            log(f"  {d}: {e}"); time.sleep(20 * (k + 1))
    return None

new_days = []           # [ [ [t,lat,lon,alt_m], ... ] par jour ]
d = start
while d < today:
    j = fetch_day(d)
    if j:
        pts = []
        base = j.get("timestamp", 0)
        for e in j.get("trace", []):
            if e[1] is None or e[2] is None: continue
            t = base + e[0]
            if datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date() != d: continue
            alt = None
            if len(e) > 10 and isinstance(e[10], (int, float)): alt = e[10] * 0.3048
            elif isinstance(e[3], (int, float)):               alt = e[3] * 0.3048
            if len(e) > 8 and isinstance(e[8], dict) and isinstance(e[8].get("alt_geom"), (int, float)):
                alt = e[8]["alt_geom"] * 0.3048
            pts.append([t, e[1], e[2], alt])
        if pts:
            new_days.append((d.isoformat(), pts))
            log(f"  {d}: {len(pts)} points")
    time.sleep(0.5)
    d += datetime.timedelta(days=1)

if not new_days:
    log("aucun nouveau jour archivé"); sys.exit(0)

# ---------- élévations terrain ----------
flat = [p for _, pts in new_days for p in pts]
log(f"élévations : {len(flat)} points")
gnd = [None] * len(flat)
for k in range(0, len(flat), 100):
    batch = flat[k:k + 100]
    locs = "|".join(f"{p[1]},{p[2]}" for p in batch)
    ok = False
    for attempt in range(4):
        try:
            r = s.post("https://api.opentopodata.org/v1/eudem25m",
                       json={"locations": locs}, timeout=40)
            if r.status_code == 200:
                for i, res in enumerate(r.json()["results"]):
                    gnd[k + i] = res.get("elevation")
                ok = True; break
        except Exception as e:
            log(f"  lot {k}: {e}")
        time.sleep(4 * (attempt + 1))
    if not ok: sys.exit(f"élévations indisponibles (lot {k}) — pas de mise à jour")
    time.sleep(1.05)

i = 0
for _, pts in new_days:
    for p in pts:
        p.append(gnd[i]); i += 1

# ---------- sites parapente ----------
sites = []
r = s.get("https://www.paraglidingearth.com/api/geojson/getCountrySites.php",
          params={"iso": "fr", "style": "detailled"}, timeout=60)
if not (r.ok and r.text.strip().startswith("{")):
    sys.exit("sites ParaglidingEarth indisponibles — pas de mise à jour")
known_dept = {x["site"]: x["dept"] for a in D["aircraft"] for x in a["sites"] if x["dept"] != "?"}
def guess_dept(lat, lon):
    if lat < 43.45:
        if lon < 0.55: return "65"
        if lon < 1.0:  return "31"
        if lon < 2.2:  return "09"
    return "?"
for f in r.json().get("features", []):
    if not f.get("geometry") or f["geometry"]["type"] != "Point": continue
    lon, lat = f["geometry"]["coordinates"]
    name = (f.get("properties", {}).get("name") or "").strip()
    if not name: continue
    alt = float(f["properties"].get("takeoff_altitude") or 0)
    sites.append({"name": name, "lat": lat, "lon": lon, "alt": alt,
                  "dept": known_dept.get(name) or guess_dept(lat, lon)})
log(f"sites : {len(sites)}")
grid = {}
for idx, x in enumerate(sites):
    grid.setdefault((int(x["lat"] / 0.2), int(x["lon"] / 0.2)), []).append(idx)
def near_sites(lat, lon):
    out = []
    a0, b0 = int(lat / 0.2), int(lon / 0.2)
    for a in (a0 - 1, a0, a0 + 1):
        for b in (b0 - 1, b0, b0 + 1):
            out += grid.get((a, b), [])
    return out

# ---------- traitement des nouveaux jours ----------
CL = [50, 150, 300, 1000, float("inf")]
def cls(h): return 4 if h is None else next(i for i, c in enumerate(CL) if h < c)

add_days, add_pass, hours_s, near10_s = [], [], 0.0, 0.0
add_cc = [0, 0, 0, 0, 0]
site_agg, dwell_agg = {}, {}

for day_iso, pts in new_days:
    segs, cur = [], []
    openp = {}
    def close(si, op):
        st = sites[si]
        add_pass.append({"t": f"{dmy(op['t0'])} · {hm(op['t0'])}–{hm(op['te'])}",
                         "k": int(op["t0"]), "site": st["name"], "dept": st["dept"],
                         "d": op["d"], "dz": op["dz"]})
        ag = site_agg.setdefault(st["name"], {
            "site": st["name"], "dept": st["dept"], "dmin": op["d"], "dz": op["dz"],
            "when": f"{dmy(op['tb'])} · {hm(op['tb'])}", "dzmin": op["dzmin"], "n": 0,
            "lat": st["lat"], "lon": st["lon"], "salt": st["alt"]})
        ag["n"] += 1
        if op["d"] < ag["dmin"]:
            ag.update(dmin=op["d"], dz=op["dz"], when=f"{dmy(op['tb'])} · {hm(op['tb'])}")
        if op["dzmin"] is not None and (ag["dzmin"] is None or op["dzmin"] < ag["dzmin"]):
            ag["dzmin"] = op["dzmin"]
    prev = None
    for t, lat, lon, alt, g in pts:
        agl = None if (alt is None or g is None) else max(0, round(alt - g))
        if prev and (t - prev[0] > 240 or dist_km(prev[1], prev[2], lat, lon) > 6):
            if len(cur) > 1: segs.append({"pts": cur})
            cur = []
        cur.append([round(lon, 4), round(lat, 4), agl])
        dt = min(t - prev[0], 60) if prev else 0
        hours_s += dt
        n10 = False
        for si in near_sites(lat, lon):
            st = sites[si]
            dk = dist_km(lat, lon, st["lat"], st["lon"])
            if dk < 10:
                n10 = True
                dw = dwell_agg.setdefault(st["name"], {"sec": 0.0, "days": set()})
                dw["sec"] += dt; dw["days"].add(day_iso)
            if dk < 1:
                dz = None if alt is None else round(alt - st["alt"])
                op = openp.get(si)
                if op and t - op["te"] <= 120:
                    op["te"] = t
                    if dk * 1000 < op["d"]:
                        op.update(d=round(dk * 1000), dz=dz, tb=t)
                    if dz is not None and (op["dzmin"] is None or dz < op["dzmin"]):
                        op["dzmin"] = dz
                else:
                    if op: close(si, op)
                    openp[si] = {"t0": t, "te": t, "tb": t, "d": round(dk * 1000),
                                 "dz": dz, "dzmin": dz}
        if n10: near10_s += dt
        for si in [x for x in openp if t - openp[x]["te"] > 120]:
            close(si, openp.pop(si))
        prev = (t, lat, lon)
    for si, op in openp.items(): close(si, op)
    if len(cur) > 1: segs.append({"pts": cur})
    for sg in segs:
        for i in range(1, len(sg["pts"])):
            a, b = sg["pts"][i - 1][2], sg["pts"][i][2]
            add_cc[cls((a + b) / 2 if (a is not None and b is not None) else None)] += 1
    if segs: add_days.append({"d": day_iso, "segs": segs})

if not add_days:
    log("nouveaux jours sans segments exploitables"); sys.exit(0)

# ---------- fusion additive ----------
ac["days"] = sorted(ac["days"] + add_days, key=lambda x: x["d"])
ac["class_counts"] = [a + b for a, b in zip(ac["class_counts"], add_cc)]
by = {x["site"]: x for x in ac["sites"]}
for nsit in site_agg.values():
    ex = by.get(nsit["site"])
    if not ex: ac["sites"].append(nsit); continue
    ex["n"] += nsit["n"]
    if nsit["dmin"] < ex["dmin"]:
        ex.update(dmin=nsit["dmin"], dz=nsit["dz"], when=nsit["when"])
    if nsit["dzmin"] is not None and (ex["dzmin"] is None or nsit["dzmin"] < ex["dzmin"]):
        ex["dzmin"] = nsit["dzmin"]
ac["sites"].sort(key=lambda x: x["dmin"])
ac["passages"] = sorted(ac["passages"] + add_pass, key=lambda p: p.get("k", 0))
dby = {x["site"]: x for x in ac["dwell"]}
for name, dw in dwell_agg.items():
    h = dw["sec"] / 3600
    if name in dby:
        dby[name]["h"] = round(dby[name]["h"] + h, 2)
        dby[name]["j"] += len(dw["days"])
    elif h >= 0.05:
        ac["dwell"].append({"site": name, "h": round(h, 2), "j": len(dw["days"])})
ac["dwell"].sort(key=lambda x: -x["h"])
st = ac["stats"]
st["hours"] = round(st["hours"] + hours_s / 3600, 1)
st["near10_h"] = round(st["near10_h"] + near10_s / 3600, 1)
st["ndays"] += len(add_days)
st["npass"] += len(add_pass)
agls = sorted(p[2] for dd in ac["days"] for sg in dd["segs"] for p in sg["pts"] if p[2] is not None)
st["agl_med"] = agls[len(agls) // 2] if agls else None

# hors de l'emprise du fond de carte ?
b = D["basemap"]
out_of = sum(1 for dd in add_days for sg in dd["segs"] for p in sg["pts"]
             if not (b["lon0"] < p[0] < b["lon1"] and b["lat1"] < p[1] < b["lat0"]))
if out_of: log(f"ATTENTION : {out_of} points hors du fond de carte actuel")

# ---------- en-têtes (dates) ----------
last = datetime.date.fromisoformat(ac["days"][-1]["d"])
html = html[:m.start()] + "const D = " + json.dumps(D, ensure_ascii=False, separators=(",", ":")) + ";" + html[m.end():]
html = re.sub(r"→ \d{1,2}(?:er)? [a-zéû]+ \d{4}", f"→ {last.day} {MOIS[last.month]} {last.year}", html, count=1)
html = re.sub(r"(<title>[^<]*avril–)[a-zéû]+( \d{4})", rf"\g<1>{MOIS[last.month]}\g<2>", html, count=1)
open(PAGE, "w", encoding="utf-8").write(html)
log(f"page mise à jour : +{len(add_days)} jour(s), +{len(add_pass)} passage(s), "
    f"dernier jour {last}, {st['hours']} h au total")
