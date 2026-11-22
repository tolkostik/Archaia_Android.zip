"""
Προετοιμασία offline δεδομένων (τρέχει στο GitHub Actions πριν φτιαχτεί το APK).

1. Ελαφρύς διανυσματικός χάρτης της Ελλάδας (greece_vec.json, ~0,5 MB): στεριά, γειτονικές χώρες,
   ακτογραμμή, σύνορα, όρια περιφερειών. Από το Natural Earth (κοινό κτήμα, χωρίς διαφημίσεις/όρους).
   Τα πολύγωνα «σπάνε» σε τρίγωνα εδώ, ώστε το κινητό απλώς να τα ζωγραφίζει.
2. Όλοι οι αρχαιολογικοί χώροι από το Wikidata (sites.json), ώστε να εμφανίζονται αμέσως.

Αν κάτι αποτύχει, η εφαρμογή δουλεύει κανονικά (online χάρτης / λήψη χώρων στην πρώτη εκκίνηση).
Χρήση: python tools/prepare_offline.py [φάκελος]   (χρειάζεται shapely, numpy, mapbox_earcut)
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = sys.argv[1] if len(sys.argv) > 1 else "offline"
UA = "Archaia/1.1 build script (offline map of Greek archaeological sites)"
NE = ["https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/{f}",
      "https://github.com/nvkelso/natural-earth-vector/raw/master/geojson/{f}"]
BOX = (16.5, 32.0, 33.5, 44.5)              # δυτ., νότ., ανατ., βόρ. (λίγο πέρα από την Ελλάδα)
R = float(2 ** 20)


def merc(lat, lon):
    s = math.sin(math.radians(max(-85.0, min(85.0, lat))))
    return (lon + 180.0) / 360.0, 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


OX, OY = merc(38.5, 24.0)


def P(lon, lat):
    """Συντεταγμένες περιεχομένου (ίδιες με το tilemap.py)."""
    x, y = merc(lat, lon)
    return round((x - OX) * R, 1), round(-(y - OY) * R, 1)


def download(f):
    err = None
    for base in NE:
        for i in range(3):
            try:
                req = urllib.request.Request(base.format(f=f), headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(2 + 3 * i)
    raise RuntimeError(f"{f}: {err}")


def polys(g):
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if hasattr(g, "geoms"):
        out = []
        for x in g.geoms:
            out += polys(x)
        return out
    return []


def tri(poly):
    """Τρίγωνα πολυγώνου (με τρύπες): (κορυφές [(x,y)], δείκτες)."""
    try:
        import numpy as np
        import mapbox_earcut as earcut
    except ImportError:                      # εναλλακτικά: τριγωνοποίηση του shapely (2.1+)
        import shapely
        pts, idx = [], []
        for t in polys(shapely.constrained_delaunay_triangles(poly)):
            for x, y in list(t.exterior.coords)[:3]:
                idx.append(len(pts))
                pts.append(P(x, y))
        return pts, idx
    rings = [list(poly.exterior.coords)[:-1]] + [list(h.coords)[:-1] for h in poly.interiors]
    pts, ends = [], []
    for r in rings:
        pts += [P(x, y) for x, y in r]
        ends.append(len(pts))
    if len(pts) < 3:
        return [], []
    idx = earcut.triangulate_float64(np.array(pts, dtype=np.float64), np.array(ends, dtype=np.uint32))
    return pts, [int(i) for i in idx]


def line(coords):
    out = []
    for x, y in coords:
        out += P(x, y)
    return out


def build_vector():
    from shapely.geometry import box, shape
    clip = box(*BOX)
    meshes = {"gr": [], "nb": []}
    lines = {"coast": [], "border": [], "region": []}
    regions = []
    countries = download("ne_10m_admin_0_countries.geojson")
    for ft in countries["features"]:
        pr = ft["properties"]
        g = shape(ft["geometry"])
        if not g.intersects(clip):
            continue
        greek = pr.get("ADM0_A3") == "GRC" or pr.get("ISO_A3") == "GRC"
        try:
            g = g.intersection(clip)
        except Exception:  # noqa: BLE001
            g = g.buffer(0).intersection(clip)
        g = g.simplify(0.002 if greek else 0.006, preserve_topology=True)
        for p in polys(g):
            if p.area < (0.00002 if greek else 0.0005):
                continue
            v, i = tri(p)
            if i:
                meshes["gr" if greek else "nb"].append((v, i))
            lines["coast" if greek else "border"].append(line(p.exterior.coords))
    print("countries ok", {k: len(v) for k, v in meshes.items()}, flush=True)
    try:
        adm = download("ne_10m_admin_1_states_provinces.geojson")
        for ft in adm["features"]:
            pr = ft["properties"]
            if pr.get("adm0_a3") != "GRC":
                continue
            g = shape(ft["geometry"]).simplify(0.003, preserve_topology=True)
            for p in polys(g):
                if p.area > 0.002:
                    lines["region"].append(line(p.exterior.coords))
            name = pr.get("name_el") or pr.get("name") or ""
            c = shape(ft["geometry"]).representative_point()
            if name:
                regions.append([name, round(c.y, 3), round(c.x, 3)])
        print("regions", len(regions), flush=True)
    except Exception as e:  # noqa: BLE001
        print("ΠΡΟΣΟΧΗ: περιφέρειες", e, flush=True)

    # ένωση τριγώνων σε λίγα πλέγματα (το πολύ 60.000 κορυφές το καθένα)
    def batch(items):
        out, v, i = [], [], []
        for pv, pi in items:
            if len(v) // 2 + len(pv) > 60000 and v:
                out.append({"v": v, "i": i})
                v, i = [], []
            base = len(v) // 2
            for x, y in pv:
                v += [x, y]
            i += [base + k for k in pi]
        if v:
            out.append({"v": v, "i": i})
        return out
    data = {"date": time.strftime("%Y-%m-%d"), "origin": [38.5, 24.0], "R": R,
            "gr": batch(meshes["gr"]), "nb": batch(meshes["nb"]), "lines": lines, "regions": regions}
    with open(os.path.join(OUT, "greece_vec.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print("vector", round(os.path.getsize(os.path.join(OUT, "greece_vec.json")) / 1e6, 2), "MB", flush=True)


def build_sites():
    import data as D
    url = D.WD + "?" + urllib.parse.urlencode({"query": D.QUERY, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        rows = json.loads(r.read().decode("utf-8")).get("results", {}).get("bindings", [])
    sites = D.parse_rows(rows)
    keep = ("id", "name", "lat", "lon", "desc", "img", "wiki_el", "n", "unesco", "region")
    out = [{k: s[k] for k in keep if s.get(k) not in ("", None, False, 0)} for s in sites]
    for s in out:
        s["lat"], s["lon"] = round(s["lat"], 5), round(s["lon"], 5)
    with open(os.path.join(OUT, "sites.json"), "w", encoding="utf-8") as fh:
        json.dump({"date": time.strftime("%Y-%m-%d"), "sites": out}, fh, ensure_ascii=False, separators=(",", ":"))
    print("sites", len(out), flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, fn in (("χάρτης", build_vector), ("χώροι Wikidata", build_sites)):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print("ΠΡΟΣΟΧΗ:", name, e, flush=True)


if __name__ == "__main__":
    main()
