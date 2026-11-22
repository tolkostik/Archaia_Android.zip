"""
Δίκτυο με αποθήκευση (cache) στο κινητό: ό,τι φορτώθηκε μία φορά ανοίγει και χωρίς internet.
"""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

UA = "Archaia/1.0 (Android travel guide; open data: Wikidata, OpenStreetMap)"
CACHE = None
_last_call = {}


def set_dir(path):
    global CACHE
    CACHE = os.path.join(path, "cache")
    try:
        os.makedirs(CACHE, exist_ok=True)
    except OSError:
        CACHE = None


def _ctx():
    try:
        import ssl
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return None


def _path(key):
    return os.path.join(CACHE, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json") if CACHE else None


def cached(key, max_age=None):
    p = _path(key)
    if not p or not os.path.exists(p):
        return None
    if max_age is not None and time.time() - os.path.getmtime(p) > max_age:
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _store(key, data):
    p = _path(key)
    if p:
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
        except OSError:
            pass


def _polite(host, gap):
    """Οι δωρεάν υπηρεσίες ζητούν να μη γίνονται πολλές κλήσεις ανά δευτερόλεπτο."""
    w = gap - (time.time() - _last_call.get(host, 0))
    if w > 0:
        time.sleep(w)
    _last_call[host] = time.time()


def get_json(url, params=None, data=None, max_age=7 * 86400, timeout=25, headers=None, gap=0.0):
    """JSON από το internet με cache. Αν δεν υπάρχει σύνδεση, επιστρέφει το αποθηκευμένο (ακόμα και παλιό)."""
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    key = full + ("|" + data if data else "")
    fresh = cached(key, max_age)
    if fresh is not None:
        return fresh
    try:
        if gap:
            _polite(urllib.parse.urlparse(url).netloc, gap)
        h = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "el,en"}
        h.update(headers or {})
        body = data.encode("utf-8") if data else None
        if body:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(full, data=body, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
            out = json.loads(r.read().decode("utf-8"))
        _store(key, out)
        return out
    except Exception:
        old = cached(key)
        if old is not None:
            return old
        raise
