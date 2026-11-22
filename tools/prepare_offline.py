"""
Προετοιμασία offline δεδομένων (τρέχει στο GitHub Actions πριν φτιαχτεί το APK).

1. Στατικός χάρτης της Ελλάδας σε δύο επίπεδα (επισκόπηση + λεπτομέρεια σε κομμάτια 1024 px),
   για «Χάρτη» (CARTO Voyager) και «Δορυφόρο» (Esri World Imagery). Ανοίγει αμέσως, χωρίς internet.
2. Όλοι οι αρχαιολογικοί χώροι από το Wikidata (sites.json), ώστε να εμφανίζονται αμέσως.

Αν κάτι αποτύχει, η εφαρμογή δουλεύει κανονικά με τα online πλακίδια / τη λήψη στην πρώτη εκκίνηση.
Χρήση: python tools/prepare_offline.py [φάκελος]   (χρειάζεται Pillow)
"""
import io
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = sys.argv[1] if len(sys.argv) > 1 else "offline"
BBOX = (34.6, 19.2, 41.9, 29.9)            # νότιο, δυτικό, βόρειο, ανατολικό
UA = "Archaia/1.0 build script (offline map of Greek archaeological sites; contact via GitHub)"
LAYERS = {
    "map": {"url": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png", "subs": "abcd",
            "px": 512, "overview": 6, "levels": (7, 8), "q": 84},
    "sat": {"url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            "subs": "", "px": 256, "overview": 7, "levels": (8,), "q": 76},
}
CHUNK = 1024


def tile_xy(lat, lon, z):
    n = 2 ** z
    s = math.sin(math.radians(lat))
    return int((lon + 180) / 360 * n), int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n)


def rng(z):
    x0, y0 = tile_xy(BBOX[2], BBOX[1], z)
    x1, y1 = tile_xy(BBOX[0], BBOX[3], z)
    return x0, y0, x1, y1


def fetch(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return Image.open(io.BytesIO(r.read())).convert("RGB")
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(1 + i * 2)
    raise RuntimeError(f"{url}: {err}")


def tile(L, z, x, y):
    s = L["subs"][(x + y) % len(L["subs"])] if L["subs"] else ""
    time.sleep(0.05)
    return fetch(L["url"].format(s=s, z=z, x=x, y=y))


def build_layer(key, L):
    px = L["px"]
    meta = {}
    # επισκόπηση: μία εικόνα
    z = L["overview"]
    x0, y0, x1, y1 = rng(z)
    img = Image.new("RGB", ((x1 - x0 + 1) * px, (y1 - y0 + 1) * px))
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            img.paste(tile(L, z, x, y).resize((px, px)), ((x - x0) * px, (y - y0) * px))
    name = f"{key}_o.jpg"
    img.save(os.path.join(OUT, name), quality=L["q"], optimize=True, progressive=False)
    meta["overview"] = {"z": z, "x0": x0, "y0": y0, "nx": x1 - x0 + 1, "ny": y1 - y0 + 1, "file": name}
    print(key, "overview", img.size, flush=True)
    # λεπτομέρεια: κομμάτια CHUNK×CHUNK σε δύο επίπεδα
    meta["levels"] = []
    for z in L["levels"]:
        k = CHUNK // px
        x0, y0, x1, y1 = rng(z)
        chunks = {}
        for cx in range(x0 // k, x1 // k + 1):
            for cy in range(y0 // k, y1 // k + 1):
                img = Image.new("RGB", (CHUNK, CHUNK))
                for i in range(k):
                    for j in range(k):
                        img.paste(tile(L, z, cx * k + i, cy * k + j).resize((px, px)), (i * px, j * px))
                name = f"{key}_{z}_{cx}_{cy}.jpg"
                img.save(os.path.join(OUT, name), quality=L["q"], optimize=True)
                chunks[f"{cx}_{cy}"] = name
            print(key, "level", z, "column", cx, flush=True)
        meta["levels"].append({"z": z, "k": k, "eff": z + int(math.log2(px // 256)), "chunks": chunks})
    return meta


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
    meta = {"date": time.strftime("%Y-%m-%d"), "chunk": CHUNK, "layers": {}}
    for key, L in LAYERS.items():
        try:
            meta["layers"][key] = build_layer(key, L)
        except Exception as e:  # noqa: BLE001
            print("ΠΡΟΣΟΧΗ:", key, e, flush=True)
    with open(os.path.join(OUT, "offline.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    try:
        build_sites()
    except Exception as e:  # noqa: BLE001
        print("ΠΡΟΣΟΧΗ: χώροι Wikidata", e, flush=True)


if __name__ == "__main__":
    main()
