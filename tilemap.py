"""
Ελαφρύς χάρτης (Web Mercator) γραμμένος για την εφαρμογή.

* Στατικός χάρτης της Ελλάδας μέσα στο APK (επισκόπηση + λεπτομέρεια σε κομμάτια): ανοίγει αμέσως,
  χωρίς internet, για «Χάρτη» και «Δορυφόρο».
* Online πλακίδια μόνο όταν κάνεις πολύ ζουμ (και μόνο αν είναι ενεργή η «λεπτομέρεια HD»).
* Η μετακίνηση και το ζουμ γίνονται από την κάρτα γραφικών (μετασχηματισμός), χωρίς να ξαναζωγραφίζεται
  τίποτα· τα σημεία ξαναϋπολογίζονται μόνο όταν σταματήσει η κίνηση ή αλλάξει το ζουμ.
"""
import json
import math
import os
import queue
import threading
import time
import urllib.request
from collections import OrderedDict

from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.text import Label as CoreLabel
from kivy.graphics import (Color, Ellipse, InstructionGroup, Line, PopMatrix, PushMatrix, Rectangle,
                           RoundedRectangle, Scale, Translate)
from kivy.metrics import dp
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.widget import Widget

LAYERS = {
    "map": {"name": "Χάρτης", "short": "Χάρτ", "url": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
            "subs": "abcd", "max": 19, "retina": True, "attr": "© OpenStreetMap · © CARTO",
            "bg": (0.8, 0.88, 0.93, 1)},
    "sat": {"name": "Δορυφόρος", "short": "Δορ",
            "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            "subs": "", "max": 18, "retina": False, "attr": "Εικόνες © Esri, Maxar, Earthstar Geographics",
            "bg": (0.08, 0.12, 0.18, 1)},
    "topo": {"name": "Ανάγλυφο", "short": "Ανάγ", "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", "subs": "abc",
             "max": 17, "retina": False, "attr": "© OpenTopoMap (CC-BY-SA) · © OpenStreetMap",
             "bg": (0.85, 0.9, 0.93, 1)},
}
UA = "Archaia/1.0 (Android app; map of Greek archaeological sites)"


# ================================================================== γεωμετρία (καθαρές συναρτήσεις)
def merc(lat, lon):
    """(x, y) στο [0,1] – το y μεγαλώνει προς τα νότια."""
    lat = max(-85.0, min(85.0, lat))
    s = math.sin(math.radians(lat))
    return (lon + 180.0) / 360.0, 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


def unmerc(x, y):
    lon = x * 360.0 - 180.0
    n = math.pi - 2 * math.pi * y
    return math.degrees(math.atan(math.sinh(n))), lon


def cluster(points, cell):
    """points: [(x, y, obj)] σε οθόνη → [(x, y, [objs])] ανά κελί."""
    grid = {}
    for x, y, o in points:
        k = (int(x // cell), int(y // cell))
        g = grid.get(k)
        if g is None:
            grid[k] = [x, y, [o]]
        else:
            n = len(g[2])
            g[0] = (g[0] * n + x) / (n + 1)
            g[1] = (g[1] * n + y) / (n + 1)
            g[2].append(o)
    return [tuple(g) for g in grid.values()]


# ================================================================== ο χάρτης
R = float(2 ** 20)                       # μονάδες περιεχομένου ανά «κόσμο» Mercator
OX, OY = merc(38.5, 24.0)                # αρχή κοντά στην Ελλάδα (ακρίβεια αριθμών στην κάρτα γραφικών)


def load_offline(folder):
    try:
        with open(os.path.join(folder, "offline.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for key, m in (meta.get("layers") or {}).items():
        if m.get("overview") and os.path.exists(os.path.join(folder, m["overview"]["file"])):
            out[key] = m
    return out


class TileMap(Widget):
    zoom = NumericProperty(6.4)
    lat = NumericProperty(38.4)
    lon = NumericProperty(23.9)
    layer = StringProperty("map")
    online = BooleanProperty(True)        # online πλακίδια όταν κάνεις πολύ ζουμ
    min_zoom = 5.0

    __events__ = ("on_select", "on_moved")

    def __init__(self, cache_dir=None, offline_dir=None, **kw):
        super().__init__(**kw)
        self.cache_dir = cache_dir
        self.offline_dir = offline_dir
        self.offline = load_offline(offline_dir) if offline_dir else {}
        self.d = max(1.0, dp(1))
        self.sites = []
        self.selected = None
        self.color_of = lambda s: (0.75, 0.36, 0.2, 1)
        self._tex = OrderedDict()           # online πλακίδια
        self._otex = OrderedDict()          # offline εικόνες (επισκόπηση / κομμάτια)
        self._oload = []                    # κομμάτια που περιμένουν φόρτωση (ένα ανά καρέ)
        self._failed = {}
        self._wanted = set()
        self._pending = set()
        self._q = queue.LifoQueue()
        self._hits = []
        self._labels = {}
        self._touches = {}
        self._pinch = None
        self._fly = None
        self._base_key = None
        self._sstate = None                 # (W, cx, cy, χρόνος) της τελευταίας σχεδίασης σημείων
        with self.canvas:
            self._bg_col = Color(*LAYERS[self.layer]["bg"])
            self._bg = Rectangle(pos=self.pos, size=self.size)
            PushMatrix()
            self._T = Translate(0, 0)
            self._S = Scale(1, 1, 1)
        self._base = InstructionGroup()
        self.canvas.add(self._base)
        with self.canvas:
            PopMatrix()
            PushMatrix()
            self._ST = Translate(0, 0)
        self._sites_g = InstructionGroup()
        self.canvas.add(self._sites_g)
        with self.canvas:
            PopMatrix()
        self._attr_g = InstructionGroup()
        self.canvas.add(self._attr_g)
        self._trig = Clock.create_trigger(self._redraw, 0)
        self._sites_later = Clock.create_trigger(self._draw_sites_now, 0.14)
        self.bind(pos=self._trig, size=self._trig, zoom=self._trig, lat=self._trig, lon=self._trig,
                  layer=self._on_layer, online=self._on_layer)
        self.bind(size=self._attr, layer=self._attr)
        for _ in range(4):
            threading.Thread(target=self._worker, daemon=True).start()

    def on_select(self, site):
        pass

    def on_moved(self):
        pass

    # ------------------------------------------------------------------ δεδομένα
    def set_sites(self, sites):
        for s in sites:
            s["_mx"], s["_my"] = merc(s["lat"], s["lon"])
        self.sites = sites
        self._sstate = None
        self._trig()

    def _on_layer(self, *a):
        self._tex.clear()
        self._wanted.clear()
        self._base_key = None
        self._trig()

    def has_offline(self, layer=None):
        return (layer or self.layer) in self.offline

    # ------------------------------------------------------------------ συντεταγμένες
    def world(self):
        return 256.0 * (2 ** self.zoom) * self.d

    def center_world(self):
        W = self.world()
        x, y = merc(self.lat, self.lon)
        return x * W, y * W

    def to_screen(self, mx, my, W=None, c=None):
        W = W or self.world()
        cx, cy = c or self.center_world()
        return self.center_x + (mx * W - cx), self.center_y - (my * W - cy)

    def set_center_world(self, wx, wy):
        W = self.world()
        self.lat, self.lon = unmerc(min(max(wx / W, 0), 1), min(max(wy / W, 0.0), 1))

    def zoom_at(self, new_zoom, sx, sy):
        """Ζουμ κρατώντας ακίνητο το σημείο της οθόνης (sx, sy)."""
        new_zoom = max(self.min_zoom, min(17.5, new_zoom))
        W = self.world()
        cx, cy = self.center_world()
        px = (cx + (sx - self.center_x)) / W
        py = (cy - (sy - self.center_y)) / W
        self.zoom = new_zoom
        W2 = self.world()
        self.set_center_world(px * W2 - (sx - self.center_x), py * W2 + (sy - self.center_y))

    def fly_to(self, lat, lon, zoom=None, dur=0.7):
        z0, a0, o0 = self.zoom, self.lat, self.lon
        z1 = self.zoom if zoom is None else zoom
        t0 = time.time()
        if self._fly:
            self._fly.cancel()

        def step(dt):
            t = min(1.0, (time.time() - t0) / dur)
            e = 1 - (1 - t) ** 3
            self.zoom = z0 + (z1 - z0) * e
            self.lat = a0 + (lat - a0) * e
            self.lon = o0 + (lon - o0) * e
            if t >= 1:
                self._fly.cancel()
                self._fly = None
                self._sites_later()
                self.dispatch("on_moved")
        self._fly = Clock.schedule_interval(step, 1 / 60.0)

    # ------------------------------------------------------------------ online πλακίδια
    def _tile_level(self):
        L = int(math.floor(self.zoom + math.log2(self.d) + 0.25))
        return max(0, min(LAYERS[self.layer]["max"], L))

    def _url(self, z, x, y):
        L = LAYERS[self.layer]
        s = L["subs"][(x + y) % len(L["subs"])] if L["subs"] else ""
        r = "@2x" if L["retina"] and self.d >= 1.5 else ""
        return L["url"].format(s=s, z=z, x=x, y=y, r=r)

    def _path(self, key):
        """Η διαδρομή χωρίς κατάληξη (το αρχείο είναι .png ή .jpg)."""
        lay, z, x, y = key
        return os.path.join(self.cache_dir, lay, str(z), str(x), str(y)) if self.cache_dir else None

    def _find(self, key):
        p = self._path(key)
        if not p:
            return None
        for ext in (".png", ".jpg"):
            if os.path.exists(p + ext):
                return p + ext
        return None

    def _get(self, key):
        t = self._tex.get(key)
        if t is not None:
            self._tex.move_to_end(key)
            return t
        if key in self._pending or time.time() - self._failed.get(key, 0) < 30:
            return None
        self._pending.add(key)
        p = self._find(key)
        if p:                                # από τη μνήμη του κινητού: φόρτωση στο επόμενο καρέ
            Clock.schedule_once(lambda dt, k=key: self._loaded(k), 0)
        else:
            self._q.put(key)
        return None

    def _store(self, key, t):
        self._tex[key] = t
        while len(self._tex) > 120:
            self._tex.popitem(last=False)

    def _worker(self):
        while True:
            key = self._q.get()
            try:
                if key not in self._wanted or key[0] != self.layer:
                    self._pending.discard(key)
                    continue
                p = self._path(key)
                if not self._find(key):
                    req = urllib.request.Request(self._url(*key[1:]), headers={"User-Agent": UA})
                    try:
                        import net
                        ctx = net._ctx()
                    except Exception:  # noqa: BLE001
                        ctx = None
                    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                        data = r.read()
                    if p:
                        os.makedirs(os.path.dirname(p), exist_ok=True)
                        ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
                        with open(p + ext, "wb") as fh:
                            fh.write(data)
                Clock.schedule_once(lambda dt, k=key: self._loaded(k), 0)
            except Exception:  # noqa: BLE001
                self._failed[key] = time.time()
                self._pending.discard(key)

    def _loaded(self, key):
        self._pending.discard(key)
        if key[0] != self.layer:
            return
        p = self._find(key)
        try:
            t = CoreImage(p, nocache=True).texture
        except Exception:  # noqa: BLE001
            self._failed[key] = time.time()
            return
        self._store(key, t)
        self._trig()

    def _fallback(self, z, x, y):
        """Αν λείπει το πλακίδιο (και δεν υπάρχει offline χάρτης), κομμάτι από γονικό πλακίδιο."""
        for k in range(1, 4):
            if z - k < 0:
                break
            pk = (self.layer, z - k, x >> k, y >> k)
            t = self._tex.get(pk)
            if t is not None:
                n = 2 ** k
                size = t.width / n
                sx, sy = x - (pk[2] << k), y - (pk[3] << k)
                return t.get_region(sx * size, t.height - (sy + 1) * size, size, size)
        return None

    # ------------------------------------------------------------------ offline εικόνες
    def _otexture(self, name, now=False):
        t = self._otex.get(name)
        if t is not None:
            self._otex.move_to_end(name)
            return t
        if now:
            try:
                t = CoreImage(os.path.join(self.offline_dir, name), nocache=True).texture
            except Exception:  # noqa: BLE001
                return None
            self._otex[name] = t
            while len(self._otex) > 18:
                self._otex.popitem(last=False)
            return t
        if name not in self._oload:
            self._owanted = getattr(self, "_owanted", set()) | {name}
            self._oload.append(name)
            if len(self._oload) == 1:
                Clock.schedule_once(self._load_one, 0)
        return None

    def _load_one(self, dt):
        """Μία εικόνα ανά καρέ, για να μην «κολλάει» η κίνηση."""
        if not self._oload:
            return
        name = self._oload.pop()
        if name in getattr(self, "_owanted", ()) and self._otexture(name, now=True) is not None:
            self._base_key = None
            self._trig()
        if self._oload:
            Clock.schedule_once(self._load_one, 0)

    # ------------------------------------------------------------------ σχεδίαση
    @staticmethod
    def _rect(mx0, my0, mx1, my1, tex):
        return Rectangle(texture=tex, pos=((mx0 - OX) * R, -(my1 - OY) * R), size=((mx1 - mx0) * R, (my1 - my0) * R))

    def _view(self):
        """Τα όρια της οθόνης σε συντεταγμένες Mercator [0,1]."""
        W = self.world()
        cx, cy = self.center_world()
        return ((cx - self.width / 2) / W, (cy - self.height / 2) / W,
                (cx + self.width / 2) / W, (cy + self.height / 2) / W)

    def _redraw(self, *a):
        W = self.world()
        cx, cy = self.center_world()
        self._bg.pos, self._bg.size = self.pos, self.size
        s = W / R
        self._S.xyz = (s, s, 1)
        self._T.xy = (self.center_x - cx + OX * W, self.center_y + cy - OY * W)
        self._update_base()
        # σημεία: κατά τη μετακίνηση απλώς μετατοπίζονται· ξαναϋπολογίζονται όταν σταματήσει
        st = self._sstate
        if st is None:
            self._draw_sites_now()
        elif abs(st[0] - W) < 1e-6:
            self._ST.xy = (st[1] - cx, cy - st[2])
            self._sites_later()
        elif time.time() - st[3] > 0.07:
            self._draw_sites_now()
        else:
            self._sites_later()

    def _update_base(self):
        vx0, vy0, vx1, vy1 = self._view()
        lay = self.layer
        off = self.offline.get(lay)
        tl = self.zoom + math.log2(self.d)
        parts = []                          # (κλειδί, mx0, my0, mx1, my1, υφή)
        needed = []
        need_online = True
        if off:
            ov = off["overview"]
            n = 2 ** ov["z"]
            t = self._otexture(ov["file"], now=True)
            if t is not None:
                parts.append((ov["file"], ov["x0"] / n, ov["y0"] / n, (ov["x0"] + ov["nx"]) / n,
                              (ov["y0"] + ov["ny"]) / n, t))
            eff_o = ov["z"] + math.log2(t.width / ov["nx"] / 256.0) if t is not None else ov["z"]
            finest = eff_o
            lv = None
            for cand in sorted(off.get("levels") or [], key=lambda x: -x["eff"]):
                finest = max(finest, cand["eff"])
                if cand["eff"] > tl + 0.35 or tl < eff_o + 0.6:
                    continue                   # πιο λεπτομερές απ' όσο χρειάζεται
                n, k = 2 ** cand["z"], cand["k"]
                vis = (int(vx1 * n // k) - int(vx0 * n // k) + 1) * (int(vy1 * n // k) - int(vy0 * n // k) + 1)
                if vis <= 12:
                    lv = cand
                    break
            if lv:
                for cand in sorted(off.get("levels") or [], key=lambda x: x["eff"]):
                    if cand["eff"] >= lv["eff"]:
                        break
                    n, k = 2 ** cand["z"], cand["k"]      # ό,τι πιο αδρό είναι ήδη φορτωμένο, από κάτω
                    for cxk in range(int(vx0 * n // k), int(vx1 * n // k) + 1):
                        for cyk in range(int(vy0 * n // k), int(vy1 * n // k) + 1):
                            name = cand["chunks"].get(f"{cxk}_{cyk}")
                            t = self._otex.get(name) if name else None
                            if t is not None:
                                parts.append((name, cxk * k / n, cyk * k / n, (cxk + 1) * k / n, (cyk + 1) * k / n, t))
                n, k = 2 ** lv["z"], lv["k"]
                for cxk in range(int(vx0 * n // k), int(vx1 * n // k) + 1):
                    for cyk in range(int(vy0 * n // k), int(vy1 * n // k) + 1):
                        name = lv["chunks"].get(f"{cxk}_{cyk}")
                        if not name:
                            continue
                        needed.append(name)
                        t = self._otexture(name)
                        if t is not None:
                            parts.append((name, cxk * k / n, cyk * k / n, (cxk + 1) * k / n, (cyk + 1) * k / n, t))
            need_online = self.online and tl > finest + 1.0
        else:
            need_online = True
        self._wanted = set()
        if need_online:
            L = self._tile_level()
            n = 2 ** L
            for ty in range(max(0, int(vy0 * n)), min(n - 1, int(vy1 * n)) + 1):
                for tx in range(int(math.floor(vx0 * n)), int(math.floor(vx1 * n)) + 1):
                    wx = tx % n
                    key = (lay, L, wx, ty)
                    self._wanted.add(key)
                    t = self._get(key) or (None if off else self._fallback(L, wx, ty))
                    if t is not None:
                        parts.append((key, tx / n, ty / n, (tx + 1) / n, (ty + 1) / n, t))
        self._owanted = set(needed)
        bk = (lay, tuple(p[0] for p in parts))
        if bk == self._base_key:
            return
        self._base_key = bk
        self._bg_col.rgba = LAYERS[lay]["bg"]
        self._base.clear()
        self._base.add(Color(1, 1, 1, 1))
        for _, mx0, my0, mx1, my1, t in parts:
            self._base.add(self._rect(mx0, my0, mx1, my1, t))

    def _attr(self, *a):
        g = self._attr_g
        g.clear()
        txt = LAYERS[self.layer]["attr"]
        t = self._label(txt, 9, False, (0.15, 0.15, 0.15, 1))
        g.add(Color(1, 1, 1, 0.75))
        g.add(Rectangle(pos=(self.right - t.width - dp(8), self.y), size=(t.width + dp(8), t.height + dp(2))))
        g.add(Color(1, 1, 1, 1))
        g.add(Rectangle(texture=t, pos=(self.right - t.width - dp(4), self.y + dp(1)), size=t.size))

    def _draw_sites_now(self, *a):
        W = self.world()
        c = self.center_world()
        self._ST.xy = (0, 0)
        self._sstate = (W, c[0], c[1], time.time())
        self._draw_sites(W, c)

    def _label(self, text, size=12, bold=True, color=(1, 1, 1, 1)):
        key = (text, size, bold, color)
        t = self._labels.get(key)
        if t is None:
            lb = CoreLabel(text=text, font_size=dp(size), bold=bold, color=color)
            lb.refresh()
            t = lb.texture
            if len(self._labels) > 400:
                self._labels.clear()
            self._labels[key] = t
        return t

    def _draw_sites(self, W, c):
        pts = []
        pad = max(self.width, self.height) * 0.6     # και λίγο έξω από την οθόνη, για ομαλή μετακίνηση
        for s in self.sites:
            if s.get("_hidden"):
                continue
            x, y = self.to_screen(s["_mx"], s["_my"], W, c)
            if self.x - pad <= x <= self.right + pad and self.y - pad <= y <= self.top + pad:
                pts.append((x, y, s))
        hits = []
        g = self._sites_g
        g.clear()
        cv = _Rec(g)
        if True:
            if self.zoom < 11.5 and len(pts) > 40:
                groups = cluster(pts, dp(46) if self.zoom < 9 else dp(34))
            else:
                groups = [(x, y, [s]) for x, y, s in pts]
            singles = []
            for x, y, objs in groups:
                if len(objs) == 1:
                    singles.append((x, y, objs[0]))
                    continue
                # ομάδα: κύκλος με τον αριθμό· αν έχει σημαντικό χώρο, χρώμα τερακότας
                imp = sum(1 for o in objs if o.get("important"))
                r = dp(13) + min(dp(13), math.log(len(objs), 2) * dp(3))
                cv.Color(0, 0, 0, 0.18)
                cv.Ellipse(pos=(x - r, y - r - dp(2)), size=(2 * r, 2 * r))
                cv.Color(1, 1, 1, 0.95)
                cv.Ellipse(pos=(x - r - dp(2), y - r - dp(2)), size=(2 * r + dp(4), 2 * r + dp(4)))
                cv.Color(*((0.75, 0.36, 0.2, 1) if imp else (0.1, 0.3, 0.55, 1)))
                cv.Ellipse(pos=(x - r, y - r), size=(2 * r, 2 * r))
                t = self._label(str(len(objs)), 12)
                cv.Color(1, 1, 1, 1)
                cv.Rectangle(texture=t, pos=(x - t.width / 2, y - t.height / 2), size=t.size)
                hits.append((x, y, r + dp(4), objs))
            # μεμονωμένοι: πρώτα οι λιγότερο σημαντικοί, από πάνω οι σημαντικοί
            singles.sort(key=lambda p: (p[2].get("important", False), p[2] is self.selected))
            show_labels = self.zoom >= 9.5
            nlab = 0
            for x, y, s in singles:
                sel = s is self.selected
                r = dp(11) if sel else (dp(8) if s.get("important") else dp(5.5))
                cv.Color(0, 0, 0, 0.25)
                cv.Ellipse(pos=(x - r, y - r - dp(1.5)), size=(2 * r, 2 * r))
                cv.Color(*((0.95, 0.75, 0.2, 1) if s.get("unesco") else (1, 1, 1, 1)))
                cv.Ellipse(pos=(x - r - dp(2), y - r - dp(2)), size=(2 * r + dp(4), 2 * r + dp(4)))
                cv.Color(*self.color_of(s))
                cv.Ellipse(pos=(x - r, y - r), size=(2 * r, 2 * r))
                if sel:
                    cv.Color(1, 1, 1, 1)
                    cv.Line(circle=(x, y, r + dp(6)), width=dp(1.6))
                hits.append((x, y, max(r, dp(9)) + dp(6), [s]))
                if show_labels and (s.get("important") or self.zoom >= 12) and nlab < 60:
                    nlab += 1
                    t = self._label(s["name"][:34], 11, True, (0.12, 0.1, 0.08, 1))
                    cv.Color(1, 1, 1, 0.86)
                    cv.RoundedRectangle(pos=(x - t.width / 2 - dp(5), y + r + dp(3)),
                                     size=(t.width + dp(10), t.height + dp(2)), radius=[dp(6)])
                    cv.Color(1, 1, 1, 1)
                    cv.Rectangle(texture=t, pos=(x - t.width / 2, y + r + dp(4)), size=t.size)
        self._hits = hits

    # ------------------------------------------------------------------ αφή
    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False
        if touch.is_mouse_scrolling:
            f = 0.5 if touch.button == "scrolldown" else -0.5
            self.zoom_at(self.zoom + f, *touch.pos)
            return True
        if self._fly:
            self._fly.cancel()
            self._fly = None
        touch.grab(self)
        self._touches[touch.uid] = [touch.x, touch.y, touch.x, touch.y, time.time()]
        if len(self._touches) == 2:
            (a, b) = list(self._touches.values())
            self._pinch = (math.hypot(a[2] - b[2], a[3] - b[3]) or 1, self.zoom)
        if touch.is_double_tap:
            self.zoom_at(self.zoom + 1.0, *touch.pos)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self or touch.uid not in self._touches:
            return False
        st = self._touches[touch.uid]
        if len(self._touches) >= 2 and self._pinch:
            st[2], st[3] = touch.x, touch.y
            (a, b) = list(self._touches.values())[:2]
            dist = math.hypot(a[2] - b[2], a[3] - b[3]) or 1
            mx, my = (a[2] + b[2]) / 2, (a[3] + b[3]) / 2
            self.zoom_at(self._pinch[1] + math.log2(dist / self._pinch[0]), mx, my)
            return True
        dx, dy = touch.x - st[2], touch.y - st[3]
        st[2], st[3] = touch.x, touch.y
        cx, cy = self.center_world()
        self.set_center_world(cx - dx, cy + dy)
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return False
        touch.ungrab(self)
        st = self._touches.pop(touch.uid, None)
        if len(self._touches) < 2:
            self._pinch = None
        if not st:
            return True
        moved = math.hypot(touch.x - st[0], touch.y - st[1])
        if moved < dp(10) and time.time() - st[4] < 0.6 and not touch.is_double_tap:
            self._tap(touch.x, touch.y)
        else:
            self._sites_later()
            self.dispatch("on_moved")
        return True

    def _tap(self, x, y):
        best = None
        ox, oy = self._ST.xy
        for hx_, hy, r, objs in self._hits:
            hx_, hy = hx_ + ox, hy + oy
            d = math.hypot(x - hx_, y - hy)
            if d <= r and (best is None or d < best[0]):
                best = (d, hx_, hy, objs)
        if best is None:
            return
        _, hx_, hy, objs = best
        if len(objs) > 1:
            lat = sum(o["lat"] for o in objs) / len(objs)
            lon = sum(o["lon"] for o in objs) / len(objs)
            self.fly_to(lat, lon, min(self.zoom + 2.2, 13.5))
            return
        self.select(objs[0])

    def select(self, site, fly=False):
        self.selected = site
        self._sstate = None
        if fly and site:
            self.fly_to(site["lat"], site["lon"], max(self.zoom, 12.5))
        self._trig()
        self.dispatch("on_select", site)


class _Rec:
    """Βοηθός: δημιουργεί εντολές γραφικών και τις προσθέτει σε ομάδα (InstructionGroup)."""

    def __init__(self, g):
        self.g = g

    def __getattr__(self, name):
        cls = {"Color": Color, "Ellipse": Ellipse, "Rectangle": Rectangle, "RoundedRectangle": RoundedRectangle,
               "Line": Line}[name]

        def make(*a, **k):
            ins = cls(*a, **k)
            self.g.add(ins)
            return ins
        return make
