"""
Δεδομένα αρχαιολογικών χώρων: ενσωματωμένοι (offline) + όλοι από το Wikidata (με internet, αποθηκεύονται).
Φωτογραφίες από το Wikimedia Commons, κείμενα από την ελληνική Wikipedia.
"""
import json
import math
import os
import re
import urllib.parse

import net
import sites_gr as SG

WD = "https://query.wikidata.org/sparql"

# αρχαιολογικός χώρος και υποκατηγορίες, μέσα στην Ελλάδα (Q41)
QUERY = """SELECT ?s ?sLabel ?sDescription ?coord ?img ?el ?n ?unesco ?admLabel WHERE {
  ?s wdt:P17 wd:Q41 ; wdt:P31/wdt:P279* wd:Q839954 ; wdt:P625 ?coord ; wikibase:sitelinks ?n .
  OPTIONAL { ?s wdt:P18 ?img . }
  OPTIONAL { ?el schema:about ?s ; schema:isPartOf <https://el.wikipedia.org/> . }
  OPTIONAL { ?s wdt:P1435 wd:Q9259 . BIND("1" AS ?unesco) }
  OPTIONAL { ?s wdt:P131 ?adm . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "el,en". }
} LIMIT 12000"""


def _v(r, k):
    return (r.get(k) or {}).get("value", "")


def _coord(s):
    m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", s or "")
    return (float(m.group(2)), float(m.group(1))) if m else (None, None)


def distance(a1, o1, a2, o2):
    r = math.radians
    h = math.sin(r(a2 - a1) / 2) ** 2 + math.cos(r(a1)) * math.cos(r(a2)) * math.sin(r(o2 - o1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def builtin():
    return [dict(s) for s in SG.SITES]


OFFLINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offline")


def bundled():
    """Οι χώροι που μπήκαν στο APK (offline/sites.json): (λίστα, ημερομηνία) ή ([], None)."""
    try:
        with open(os.path.join(OFFLINE, "sites.json"), encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:  # noqa: BLE001
        return [], None
    out = []
    for s in d.get("sites", []):
        it = {"id": s["id"], "qid": s["id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"],
              "desc": s.get("desc", ""), "img": s.get("img", ""), "wiki_el": s.get("wiki_el", ""),
              "n": s.get("n", 0), "unesco": bool(s.get("unesco")), "region": s.get("region", ""), "period": "",
              "top": False, "wiki_title": ""}
        out.append(it)
    return out, d.get("date")


def fetch_all(max_age=30 * 86400):
    """Όλοι οι αρχαιολογικοί χώροι από το Wikidata (cache 30 ημέρες)."""
    data = net.get_json(WD, {"query": QUERY, "format": "json"}, max_age=max_age, timeout=90,
                        headers={"Accept": "application/sparql-results+json"})
    return parse_rows(data.get("results", {}).get("bindings", []))


def parse_rows(rows):
    seen = {}
    for r in rows:
        q = _v(r, "s").rsplit("/", 1)[-1]
        name = _v(r, "sLabel")
        if not name or re.fullmatch(r"Q\d+", name):
            continue
        it = seen.get(q)
        if it is None:
            lat, lon = _coord(_v(r, "coord"))
            if lat is None or not (34.0 <= lat <= 42.0 and 19.0 <= lon <= 30.0):
                continue
            it = {"id": q, "qid": q, "name": name, "lat": lat, "lon": lon, "desc": _v(r, "sDescription"),
                  "img": "", "wiki_el": _v(r, "el"), "n": int(float(_v(r, "n") or 0)), "unesco": False,
                  "region": _v(r, "admLabel"), "period": "", "top": False, "wiki_title": ""}
            seen[q] = it
        if not it["img"] and _v(r, "img"):
            it["img"] = _v(r, "img").replace("http://", "https://")
        if _v(r, "unesco"):
            it["unesco"] = True
    return list(seen.values())


def merge(base, extra):
    """Ενώνει τους ενσωματωμένους με του Wikidata: ίδιος χώρος (κοντά < 700 μ.) → παίρνει φωτογραφία/άρθρο."""
    out = [dict(b) for b in base]
    grid = {}
    for b in out:
        grid.setdefault((round(b["lat"], 1), round(b["lon"], 1)), []).append(b)
    for e in extra:
        near = None
        for dk in ((0, 0), (0.1, 0), (-0.1, 0), (0, 0.1), (0, -0.1)):
            for b in grid.get((round(e["lat"] + dk[0], 1), round(e["lon"] + dk[1], 1)), []):
                if distance(b["lat"], b["lon"], e["lat"], e["lon"]) < 0.7:
                    near = b
                    break
            if near:
                break
        if near and near.get("top"):
            if not near.get("qid") or e["n"] > near.get("_n", -1):
                near.update(qid=e["qid"], _n=e["n"])
                near["img"] = near.get("img") or e["img"]
                near["wiki_el"] = near.get("wiki_el") or e["wiki_el"]
            continue
        out.append(e)
    # σημαντικοί: ενσωματωμένοι, UNESCO, ή γνωστοί διεθνώς (άρθρα σε πολλές γλώσσες)
    for s in out:
        s["important"] = bool(s.get("top") or s.get("unesco") or s.get("n", 0) >= 6)
    return out


# ================================================================== λεπτομέρειες
def wiki_summary(site):
    """(κείμενο, εικόνα, σύνδεσμος) από την ελληνική Wikipedia."""
    title = ""
    if site.get("wiki_el"):
        title = urllib.parse.unquote(site["wiki_el"].rsplit("/wiki/", 1)[-1])
    elif site.get("wiki_title"):
        title = site["wiki_title"]
    if not title:
        return None
    try:
        d = net.get_json("https://el.wikipedia.org/api/rest_v1/page/summary/" +
                         urllib.parse.quote(title.replace(" ", "_")), max_age=60 * 86400)
    except Exception:  # noqa: BLE001
        # ο τίτλος ίσως διαφέρει λίγο: αναζήτηση
        r = net.get_json("https://el.wikipedia.org/w/rest.php/v1/search/title", {"q": site["name"], "limit": 1},
                         max_age=60 * 86400)
        pages = r.get("pages") or []
        if not pages:
            return None
        d = net.get_json("https://el.wikipedia.org/api/rest_v1/page/summary/" +
                         urllib.parse.quote(pages[0]["key"]), max_age=60 * 86400)
    return {"text": d.get("extract", ""), "img": (d.get("originalimage") or d.get("thumbnail") or {}).get("source", ""),
            "url": ((d.get("content_urls") or {}).get("mobile") or {}).get("page", "")}


def details(qid):
    """Επιπλέον στοιχεία από το Wikidata: χρονολογία, κατηγορία Commons, ιστοσελίδα, επισκέπτες."""
    if not qid:
        return {}
    q = f"""SELECT ?inc ?cat ?web ?vis ?typeLabel ?admLabel WHERE {{
  OPTIONAL {{ wd:{qid} wdt:P571 ?inc . }}
  OPTIONAL {{ wd:{qid} wdt:P373 ?cat . }}
  OPTIONAL {{ wd:{qid} wdt:P856 ?web . }}
  OPTIONAL {{ wd:{qid} wdt:P1174 ?vis . }}
  OPTIONAL {{ wd:{qid} wdt:P31 ?type . }}
  OPTIONAL {{ wd:{qid} wdt:P131 ?adm . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "el,en". }}
}} LIMIT 20"""
    rows = net.get_json(WD, {"query": q, "format": "json"}, max_age=60 * 86400, timeout=40,
                        headers={"Accept": "application/sparql-results+json"}).get("results", {}).get("bindings", [])
    out = {"types": []}
    for r in rows:
        for k in ("inc", "cat", "web", "vis", "admLabel"):
            if _v(r, k) and not out.get(k):
                out[k] = _v(r, k)
        t = _v(r, "typeLabel")
        if t and t not in out["types"] and not re.fullmatch(r"Q\d+", t):
            out["types"].append(t)
    if out.get("inc"):
        out["year"] = year_text(out["inc"])
    return out


def year_text(iso):
    m = re.match(r"([+-]?)(\d+)", iso or "")
    if not m:
        return ""
    y = int(m.group(2))
    return f"{y} π.Χ." if m.group(1) == "-" else f"{y} μ.Χ."


def gallery(category, limit=12):
    """Φωτογραφίες από την κατηγορία του Commons."""
    if not category:
        return []
    d = net.get_json("https://commons.wikimedia.org/w/api.php",
                     {"action": "query", "generator": "categorymembers", "gcmtitle": "Category:" + category,
                      "gcmtype": "file", "gcmlimit": limit * 2, "prop": "imageinfo", "iiprop": "url|mime",
                      "iiurlwidth": 640, "format": "json"}, max_age=60 * 86400)
    out = []
    for p in (d.get("query") or {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("mime") in ("image/jpeg", "image/png") and ii.get("thumburl"):
            out.append(ii["thumburl"])
    return out[:limit]


def thumb(img, width=640):
    if not img:
        return ""
    if "Special:FilePath" in img:
        return img.replace("http://", "https://") + ("&" if "?" in img else "?") + f"width={width}"
    return img


def search(sites, q):
    import unicodedata

    def f(t):
        t = unicodedata.normalize("NFD", (t or "").lower())
        return "".join(c for c in t if unicodedata.category(c) != "Mn").replace("ς", "σ")
    q = f(q.strip())
    if not q:
        return []
    hits = [s for s in sites if q in f(s["name"]) or q in f(s.get("region", ""))]
    return sorted(hits, key=lambda s: (not f(s["name"]).startswith(q), not s.get("important"), -s.get("n", 0)))
