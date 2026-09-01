#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mise à jour de fguid/index.html.

- data_base.json : données consolidées, uniquement des journées ARCHIVÉES
  (définitives) par ADS-B Exchange. Ne fait que grandir.
- La page affichée = base + journée(s) LIVE provisoires (le jour en cours,
  recalculé entièrement à chaque passage, remplacé par l'archive quand elle sort).
- elev_cache.json : cache des élévations terrain (opentopodata, 1 req/s).

Aucun texte n'est ajouté à la page : uniquement des données et les dates d'en-tête.
"""
import copy, datetime, json, math, os, re, sys, time
from zoneinfo import ZoneInfo

import requests

HEX = "395103"
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "index.html")
BASE_PATH = os.path.join(HERE, "data_base.json")
CACHE_PATH = os.path.join(HERE, "elev_cache.json")
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

def dmy(t): return datetime.datetime.fromtimestamp(t, PARIS).strftime("%d/%m/%y")
def hm(t):  return datetime.datetime.fromtimestamp(t, PARIS).strftime("%H h %M")

# ---------------- récupération des traces ----------------
def http_json(url, tries=3):
    for k in range(tries):
        try:
            r = s.get(url, timeout=30)
            if r.status_code == 200: return r.json()
            if r.status_code == 404: return None
            log(f"  HTTP {r.status_code} {url[-40:]}"); time.sleep(15 * (k + 1))
        except Exception as e:
            log(f"  {e}"); time.sleep(15 * (k + 1))
    return None

def parse_trace(j, day):
    pts = []
    base = j.get("timestamp", 0)
    for e in j.get("trace", []):
        if e[1] is None or e[2] is None: continue
        t = base + e[0]
        if datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date() != day: continue
        alt = None
        if len(e) > 10 and isinstance(e[10], (int, float)): alt = e[10] * 0.3048
        elif isinstance(e[3], (int, float)):               alt = e[3] * 0.3048
        if len(e) > 8 and isinstance(e[8], dict) and isinstance(e[8].get("alt_geom"), (int, float)):
            alt = e[8]["alt_geom"] * 0.3048
        pts.append([t, e[1], e[2], alt])
    return pts

def fetch_archive(day):
    return http_json(f"https://globe.adsbexchange.com/globe_history/{day.year}/{day:%m}/{day:%d}"
                     f"/traces/{HEX[-2:]}/trace_full_{HEX}.json")

def fetch_live():
    return http_json(f"https://globe.adsbexchange.com/data/traces/{HEX[-2:]}/trace_full_{HEX}.json")

# ---------------- élévations (avec cache) ----------------
cache = {}
if os.path.exists(CACHE_PATH):
    cache = json.load(open(CACHE_PATH))
cache_dirty = False

def add_elevations(days):
    """days: [(iso, [[t,lat,lon,alt],...])] -> ajoute gnd en 5e position."""
    global cache_dirty
    need = []
    for _, pts in days:
        for p in pts:
            key = f"{p[1]:.5f},{p[2]:.5f}"
            if key not in cache: need.append((key, p[1], p[2]))
    need = list({k: (k, la, lo) for k, la, lo in need}.values())
    if need: log(f"élévations à récupérer : {len(need)} (cache : {len(cache)})")
    for k in range(0, len(need), 100):
        batch = need[k:k + 100]
        locs = "|".join(f"{la},{lo}" for _, la, lo in batch)
        ok = False
        for attempt in range(4):
            try:
                r = s.post("https://api.opentopodata.org/v1/eudem25m",
                           json={"locations": locs}, timeout=40)
                if r.status_code == 200:
                    for (key, _, _), res in zip(batch, r.json()["results"]):
                        cache[key] = res.get("elevation")
                    ok = True; cache_dirty = True; break
            except Exception as e:
                log(f"  élévations lot {k}: {e}")
            time.sleep(4 * (attempt + 1))
        if not ok: return False
        time.sleep(1.05)
    for _, pts in days:
        for p in pts:
            p.append(cache.get(f"{p[1]:.5f},{p[2]:.5f}"))
    return True

# ---------------- sites parapente ----------------
def fetch_sites(D):
    r = s.get("https://www.paraglidingearth.com/api/geojson/getCountrySites.php",
              params={"iso": "fr", "style": "detailled"}, timeout=60)
    if not (r.ok and r.text.strip().startswith("{")): return None
    known = {x["site"]: x["dept"] for a in D["aircraft"] for x in a["sites"] if x["dept"] != "?"}
    def guess(lat, lon):
        if lat < 43.45:
            if lon < 0.55: return "65"
            if lon < 1.0:  return "31"
            if lon < 2.2:  return "09"
        return "?"
    out = []
    for f in r.json().get("features", []):
        if not f.get("geometry") or f["geometry"]["type"] != "Point": continue
        lon, lat = f["geometry"]["coordinates"]
        name = (f.get("properties", {}).get("name") or "").strip()
        if not name: continue
        out.append({"name": name, "lat": lat, "lon": lon,
                    "alt": float(f["properties"].get("takeoff_altitude") or 0),
                    "dept": known.get(name) or guess(lat, lon)})
    return out

# ---------------- traitement d'un lot de journées ----------------
CL = [50, 150, 300, 1000, float("inf")]
def cls(h): return 4 if h is None else next(i for i, c in enumerate(CL) if h < c)

def process(days, sites):
    grid = {}
    for idx, x in enumerate(sites):
        grid.setdefault((int(x["lat"] / 0.2), int(x["lon"] / 0.2)), []).append(idx)
    def near(lat, lon):
        a0, b0 = int(lat / 0.2), int(lon / 0.2)
        return [i for a in (a0-1, a0, a0+1) for b in (b0-1, b0, b0+1) for i in grid.get((a, b), [])]

    C = {"days": [], "cc": [0]*5, "pass": [], "sites": {}, "dwell": {}, "h": 0.0, "n10": 0.0}
    def close(si, op):
        st = sites[si]
        C["pass"].append({"t": f"{dmy(op['t0'])} · {hm(op['t0'])}–{hm(op['te'])}",
                          "k": int(op["t0"]), "site": st["name"], "dept": st["dept"],
                          "d": op["d"], "dz": op["dz"]})
        ag = C["sites"].setdefault(st["name"], {
            "site": st["name"], "dept": st["dept"], "dmin": op["d"], "dz": op["dz"],
            "when": f"{dmy(op['tb'])} · {hm(op['tb'])}", "dzmin": op["dzmin"], "n": 0,
            "lat": st["lat"], "lon": st["lon"], "salt": st["alt"]})
        ag["n"] += 1
        if op["d"] < ag["dmin"]:
            ag.update(dmin=op["d"], dz=op["dz"], when=f"{dmy(op['tb'])} · {hm(op['tb'])}")
        if op["dzmin"] is not None and (ag["dzmin"] is None or op["dzmin"] < ag["dzmin"]):
            ag["dzmin"] = op["dzmin"]

    for day_iso, pts in days:
        segs, cur, openp, prev = [], [], {}, None
        for t, lat, lon, alt, g in pts:
            agl = None if (alt is None or g is None) else max(0, round(alt - g))
            if prev and (t - prev[0] > 240 or dist_km(prev[1], prev[2], lat, lon) > 6):
                if len(cur) > 1: segs.append({"pts": cur})
                cur = []
            cur.append([round(lon, 4), round(lat, 4), agl])
            dt = min(t - prev[0], 60) if prev else 0
            C["h"] += dt
            n10 = False
            for si in near(lat, lon):
                st = sites[si]
                dk = dist_km(lat, lon, st["lat"], st["lon"])
                if dk < 10:
                    n10 = True
                    dw = C["dwell"].setdefault(st["name"], {"sec": 0.0, "days": set()})
                    dw["sec"] += dt; dw["days"].add(day_iso)
                if dk < 1:
                    dz = None if alt is None else round(alt - st["alt"])
                    op = openp.get(si)
                    if op and t - op["te"] <= 120:
                        op["te"] = t
                        if dk * 1000 < op["d"]: op.update(d=round(dk * 1000), dz=dz, tb=t)
                        if dz is not None and (op["dzmin"] is None or dz < op["dzmin"]):
                            op["dzmin"] = dz
                    else:
                        if op: close(si, op)
                        openp[si] = {"t0": t, "te": t, "tb": t, "d": round(dk * 1000),
                                     "dz": dz, "dzmin": dz}
            if n10: C["n10"] += dt
            for si in [x for x in openp if t - openp[x]["te"] > 120]:
                close(si, openp.pop(si))
            prev = (t, lat, lon)
        for si, op in openp.items(): close(si, op)
        if len(cur) > 1: segs.append({"pts": cur})
        for sg in segs:
            for i in range(1, len(sg["pts"])):
                a, b = sg["pts"][i-1][2], sg["pts"][i][2]
                C["cc"][cls((a + b) / 2 if (a is not None and b is not None) else None)] += 1
        if segs: C["days"].append({"d": day_iso, "segs": segs})
    return C

def merge(D, C):
    ac = D["aircraft"][0]
    ac["days"] = sorted(ac["days"] + C["days"], key=lambda x: x["d"])
    ac["class_counts"] = [a + b for a, b in zip(ac["class_counts"], C["cc"])]
    by = {x["site"]: x for x in ac["sites"]}
    for ns in C["sites"].values():
        ex = by.get(ns["site"])
        if not ex: ac["sites"].append(dict(ns)); continue
        ex["n"] += ns["n"]
        if ns["dmin"] < ex["dmin"]: ex.update(dmin=ns["dmin"], dz=ns["dz"], when=ns["when"])
        if ns["dzmin"] is not None and (ex["dzmin"] is None or ns["dzmin"] < ex["dzmin"]):
            ex["dzmin"] = ns["dzmin"]
    ac["sites"].sort(key=lambda x: x["dmin"])
    ac["passages"] = sorted(ac["passages"] + C["pass"], key=lambda p: p.get("k", 0))
    dby = {x["site"]: x for x in ac["dwell"]}
    for name, dw in C["dwell"].items():
        h = dw["sec"] / 3600
        if name in dby:
            dby[name]["h"] = round(dby[name]["h"] + h, 2); dby[name]["j"] += len(dw["days"])
        elif h >= 0.05:
            ac["dwell"].append({"site": name, "h": round(h, 2), "j": len(dw["days"])})
    ac["dwell"].sort(key=lambda x: -x["h"])
    st = ac["stats"]
    st["hours"] = round(st["hours"] + C["h"] / 3600, 1)
    st["near10_h"] = round(st["near10_h"] + C["n10"] / 3600, 1)
    st["ndays"] += len(C["days"])
    st["npass"] += len(C["pass"])
    agls = sorted(p[2] for dd in ac["days"] for sg in dd["segs"] for p in sg["pts"] if p[2] is not None)
    st["agl_med"] = agls[len(agls) // 2] if agls else None

# ---------------- déroulé ----------------
html = open(PAGE, encoding="utf-8").read()
m = re.search(r"^const D = (.*);$", html, re.M)
if not m: sys.exit("const D introuvable")
D_page = json.loads(m.group(1))

if os.path.exists(BASE_PATH):
    base = json.load(open(BASE_PATH))
else:
    base = copy.deepcopy(D_page)   # première exécution : la page est 100 % archives
    json.dump(base, open(BASE_PATH, "w"), ensure_ascii=False, separators=(",", ":"))
    log("data_base.json initialisé depuis la page")

last = datetime.date.fromisoformat(max(d["d"] for d in base["aircraft"][0]["days"]))
today = datetime.datetime.now(datetime.timezone.utc).date()
log(f"base : dernier jour archivé {last} ; aujourd'hui {today}")

# 1. journées archivées manquantes (définitives)
arch = []
d = last + datetime.timedelta(days=1)
while d < today:
    j = fetch_archive(d)
    if j:
        pts = parse_trace(j, d)
        if pts: arch.append((d.isoformat(), pts)); log(f"  archive {d}: {len(pts)} pts")
    time.sleep(0.4)
    d += datetime.timedelta(days=1)

# 2. live : jour en cours + éventuel hier pas encore archivé (couvert ~24 h)
in_arch = {x[0] for x in arch}
live = []
lj = fetch_live()
if lj:
    for d in (today - datetime.timedelta(days=1), today):
        if d > last and d.isoformat() not in in_arch:
            pts = parse_trace(lj, d)
            if pts: live.append((d.isoformat(), pts)); log(f"  live {d}: {len(pts)} pts (provisoire)")

if not arch and not live:
    log("rien de neuf"); sys.exit(0)

sites = fetch_sites(base)
if sites is None: sys.exit("sites ParaglidingEarth indisponibles")
if not add_elevations(arch + live): sys.exit("élévations indisponibles")

if arch:
    merge(base, process(arch, sites))
    json.dump(base, open(BASE_PATH, "w"), ensure_ascii=False, separators=(",", ":"))
    log(f"base consolidée : +{len(arch)} jour(s) archivé(s)")

display = copy.deepcopy(base)
if live:
    merge(display, process(live, sites))

if cache_dirty:
    json.dump(cache, open(CACHE_PATH, "w"), sort_keys=True)

lastd = datetime.date.fromisoformat(display["aircraft"][0]["days"][-1]["d"])
html = html[:m.start()] + "const D = " + json.dumps(display, ensure_ascii=False, separators=(",", ":")) + ";" + html[m.end():]
html = re.sub(r"→ \d{1,2}(?:er)? [a-zéû]+ \d{4}", f"→ {lastd.day} {MOIS[lastd.month]} {lastd.year}", html, count=1)
html = re.sub(r"(<title>[^<]*avril–)[a-zéû]+( \d{4})", rf"\g<1>{MOIS[lastd.month]}\g<2>", html, count=1)
open(PAGE, "w", encoding="utf-8").write(html)
st = display["aircraft"][0]["stats"]
log(f"page mise à jour : archives +{len(arch)}, live {len(live)} jour(s) provisoire(s), "
    f"dernier jour {lastd}, {st['hours']} h, {st['npass']} passages")
