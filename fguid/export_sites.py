#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exporte les décollages/atterrissages parapente France (ParaglidingEarth)
en CSV (séparateur ; UTF-8 BOM, ouvrable dans Excel FR) et KMZ (Google Earth).
Usage : python3 export_sites.py [sites.geojson]   (sinon : téléchargement PGE)"""
import csv, io, json, os, sys, zipfile
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
ORI = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

if len(sys.argv) > 1:
    gj = json.load(open(sys.argv[1], encoding="utf-8"))
else:
    import requests
    r = requests.get("https://www.paraglidingearth.com/api/geojson/getCountrySites.php",
                     params={"iso": "fr", "style": "detailled"}, timeout=60)
    gj = r.json()

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

rows = []
for f in gj.get("features", []):
    if not f.get("geometry") or f["geometry"]["type"] != "Point": continue
    lon, lat = f["geometry"]["coordinates"]
    p = f.get("properties", {})
    name = (p.get("name") or "").strip()
    if not name: continue
    oris = [o for o in ORI if num(p.get(o)) and num(p.get(o)) > 0]
    land = p.get("landing") or {}
    llat = num(land.get("landing_lat") or p.get("landing_lat"))
    llon = num(land.get("landing_lng") or p.get("landing_lng"))
    rows.append({
        "pge_id": p.get("pge_site_id", ""), "nom": name,
        "lat_deco": round(lat, 5), "lon_deco": round(lon, 5), "alt_deco_m": int(num(p.get("takeoff_altitude")) or 0),
        "orientations": ",".join(oris),
        "parapente": p.get("paragliding", ""), "delta": p.get("hanggliding", ""),
        "thermique": p.get("thermals", ""), "soaring": p.get("soaring", ""), "treuil": p.get("winch", ""),
        "cross": p.get("xc", ""), "plaine": p.get("flatland", ""),
        "atterro_nom": (land.get("landing_name") or "").strip(),
        "lat_atterro": round(llat, 5) if llat else "", "lon_atterro": round(llon, 5) if llon else "",
        "alt_atterro_m": int(num(land.get("landing_altitude")) or 0) if land.get("landing_altitude") else "",
        "lien_pge": (p.get("pge_link") or "").replace("\\/", "/"), "ffvl_id": p.get("ffvl_site_id", ""),
        "_desc": (p.get("takeoff_description") or "").strip(),
        "_landdesc": (land.get("landing_description") or "").strip(),
    })
rows.sort(key=lambda r: r["nom"].lower())

# ---- CSV ----
cols = [c for c in rows[0].keys() if not c.startswith("_")]
with open(os.path.join(HERE, "sites_parapente_fr.csv"), "w", encoding="utf-8-sig", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, delimiter=";", extrasaction="ignore")
    w.writeheader(); w.writerows(rows)

# ---- KMZ ----
def pm(name, lat, lon, alt, desc, style, extra):
    ed = "".join(f'<Data name="{escape(k)}"><value>{escape(str(v))}</value></Data>' for k, v in extra.items())
    return (f"<Placemark><name>{escape(name)}</name><styleUrl>#{style}</styleUrl>"
            f"<description><![CDATA[{desc}]]></description><ExtendedData>{ed}</ExtendedData>"
            f"<Point><coordinates>{lon},{lat},{alt}</coordinates></Point></Placemark>")
deco, atterro = [], []
for r in rows:
    d = (f"Altitude : {r['alt_deco_m']} m<br>Orientations : {r['orientations'] or '–'}<br>"
         + (f"{escape(r['_desc'])}<br>" if r['_desc'] else "")
         + (f"<a href=\"{r['lien_pge']}\">Fiche ParaglidingEarth</a>" if r['lien_pge'] else ""))
    deco.append(pm(r["nom"], r["lat_deco"], r["lon_deco"], r["alt_deco_m"], d, "deco",
                   {"altitude_m": r["alt_deco_m"], "orientations": r["orientations"], "pge_id": r["pge_id"]}))
    if r["lat_atterro"] and r["lon_atterro"]:
        nm = f"Atterro – {r['nom']}" + (f" ({r['atterro_nom']})" if r['atterro_nom'] else "")
        d2 = (f"Atterrissage du site {escape(r['nom'])}<br>"
              + (f"Altitude : {r['alt_atterro_m']} m<br>" if r['alt_atterro_m'] != "" else "")
              + (escape(r['_landdesc']) if r['_landdesc'] else ""))
        atterro.append(pm(nm, r["lat_atterro"], r["lon_atterro"], r["alt_atterro_m"] or 0, d2, "atterro",
                          {"site": r["nom"], "pge_id": r["pge_id"]}))
kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<name>Sites parapente France — ParaglidingEarth</name>
<Style id="deco"><IconStyle><scale>1.0</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/triangle.png</href></Icon><color>ff2a70e8</color></IconStyle><LabelStyle><scale>0.8</scale></LabelStyle></Style>
<Style id="atterro"><IconStyle><scale>0.9</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/target.png</href></Icon><color>ff32b432</color></IconStyle><LabelStyle><scale>0.7</scale></LabelStyle></Style>
<Folder><name>Décollages ({len(deco)})</name>{''.join(deco)}</Folder>
<Folder><name>Atterrissages ({len(atterro)})</name>{''.join(atterro)}</Folder>
</Document></kml>"""
with zipfile.ZipFile(os.path.join(HERE, "sites_parapente_fr.kmz"), "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("doc.kml", kml)
print(f"CSV : {len(rows)} sites · KMZ : {len(deco)} décollages, {len(atterro)} atterrissages")
