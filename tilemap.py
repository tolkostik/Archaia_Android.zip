"""
Ελαφρύς χάρτης της Ελλάδας, γραμμένος για την εφαρμογή.

* «Lite»: διανυσματικός χάρτης μέσα στο APK (στεριά, θάλασσα, ακτές, σύνορα, περιφέρειες, πόλεις,
  πελάγη, βουνά). Χωρίς internet, χωρίς διαφημίσεις/καταστήματα, άμεσος.
* «Δορυφόρος» και «Ανάγλυφο»: προαιρετικά, online πλακίδια (αποθηκεύονται στο κινητό).
* Μετακίνηση και ζουμ γίνονται από την κάρτα γραφικών (μετασχηματισμός): τίποτα δεν ξαναζωγραφίζεται
  όσο κινείται το δάχτυλο. Σημεία και ετικέτες ξαναϋπολογίζονται όταν σταματήσει ή αλλάξει το ζουμ.
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
from kivy.graphics import (Color, Ellipse, InstructionGroup, Line, Mesh, PopMatrix, PushMatrix, Rectangle,
                           RoundedRectangle, Scale, Translate)
from kivy.metrics import dp
from kivy.properties import NumericProperty, StringProperty
from kivy.uix.widget import Widget

LAYERS = {
    "lite": {"name": "Χάρτης", "short": "Χάρτ", "bg": (0.70, 0.83, 0.92, 1), "attr": ""},
    "sat": {"name": "Δορυφόρος", "short": "Δορ",
            "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            "subs": "", "max": 18, "retina": False, "attr": "Εικόνες © Esri", "bg": (0.08, 0.12, 0.18, 1)},
    "topo": {"name": "Ανάγλυφο", "short": "Ανάγ", "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
             "subs": "abc", "max": 17, "retina": False, "attr": "© OpenTopoMap · © OpenStreetMap",
             "bg": (0.85, 0.9, 0.93, 1)},
    # μόνο αν λείπει ο ενσωματωμένος χάρτης
    "map": {"name": "Χάρτης (online)", "short": "Χάρτ",
            "url": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png",
            "subs": "abcd", "max": 19, "retina": True, "attr": "© OpenStreetMap · © CARTO",
            "bg": (0.8, 0.88, 0.93, 1)},
}
UA = "Archaia/1.1 (Android app; map of Greek archaeological sites)"
LAND = (0.975, 0.96, 0.915, 1)
NEIGH = (0.90, 0.895, 0.875, 1)
COAST = (0.42, 0.58, 0.72, 1)
REGION = (0.78, 0.66, 0.55, 1)
BORDER = (0.6, 0.6, 0.62, 1)

# πόλεις: όνομα, πλάτος, μήκος, σημασία (1 = μεγάλη, 3 = μικρή)
CITIES = [
    ("Αθήνα", 37.984, 23.728, 1), ("Θεσσαλονίκη", 40.640, 22.944, 1), ("Πάτρα", 38.246, 21.735, 1),
    ("Ηράκλειο", 35.339, 25.144, 1), ("Λάρισα", 39.639, 22.419, 1), ("Βόλος", 39.362, 22.942, 2),
    ("Ιωάννινα", 39.665, 20.853, 1), ("Χανιά", 35.513, 24.018, 2), ("Ρόδος", 36.434, 28.217, 1),
    ("Καλαμάτα", 37.039, 22.114, 2), ("Κομοτηνή", 41.120, 25.405, 2), ("Αλεξανδρούπολη", 40.849, 25.874, 2),
    ("Καβάλα", 40.937, 24.412, 2), ("Σέρρες", 41.086, 23.548, 2), ("Δράμα", 41.150, 24.147, 3),
    ("Ξάνθη", 41.135, 24.888, 3), ("Κατερίνη", 40.272, 22.502, 3), ("Τρίκαλα", 39.555, 21.768, 2),
    ("Καρδίτσα", 39.365, 21.922, 3), ("Λαμία", 38.900, 22.434, 2), ("Χαλκίδα", 38.463, 23.596, 2),
    ("Κόρινθος", 37.939, 22.932, 2), ("Τρίπολη", 37.510, 22.372, 2), ("Σπάρτη", 37.074, 22.430, 3),
    ("Ναύπλιο", 37.568, 22.806, 3), ("Πύργος", 37.675, 21.441, 3), ("Αγρίνιο", 38.621, 21.408, 2),
    ("Μεσολόγγι", 38.369, 21.429, 3), ("Πρέβεζα", 38.959, 20.752, 3), ("Άρτα", 39.160, 20.985, 3),
    ("Ηγουμενίτσα", 39.505, 20.265, 3), ("Κέρκυρα", 39.624, 19.922, 2), ("Κοζάνη", 40.300, 21.789, 2),
    ("Καστοριά", 40.517, 21.268, 3), ("Φλώρινα", 40.781, 21.409, 3), ("Βέροια", 40.524, 22.202, 3),
    ("Έδεσσα", 40.802, 22.047, 3), ("Γιαννιτσά", 40.792, 22.408, 3), ("Κιλκίς", 40.993, 22.875, 3),
    ("Πολύγυρος", 40.378, 23.443, 3), ("Ρέθυμνο", 35.366, 24.482, 2), ("Άγιος Νικόλαος", 35.191, 25.716, 3),
    ("Σητεία", 35.207, 26.105, 3), ("Μυτιλήνη", 39.107, 26.555, 2), ("Χίος", 38.368, 26.136, 2),
    ("Σάμος", 37.756, 26.977, 3), ("Κως", 36.893, 27.288, 2), ("Ερμούπολη", 37.444, 24.942, 3),
    ("Νάξος", 37.104, 25.377, 3), ("Θήρα", 36.417, 25.432, 3), ("Μύκονος", 37.446, 25.329, 3),
    ("Ζάκυνθος", 37.787, 20.899, 3), ("Αργοστόλι", 38.176, 20.489, 3), ("Λευκάδα", 38.831, 20.708, 3),
    ("Θήβα", 38.321, 23.318, 3), ("Λιβαδειά", 38.436, 22.875, 3), ("Άμφισσα", 38.527, 22.377, 3),
    ("Ολυμπία", 37.646, 21.626, 3), ("Πειραιάς", 37.942, 23.646, 3), ("Λήμνος", 39.875, 25.062, 3),
]
SEAS = [("Αιγαίο Πέλαγος", 38.9, 25.0), ("Ιόνιο Πέλαγος", 38.0, 19.6), ("Κρητικό Πέλαγος", 36.0, 24.8),
        ("Λιβυκό Πέλαγος", 34.6, 24.6), ("Μυρτώο", 37.1, 23.6), ("Θρακικό Πέλαγος", 40.4, 24.7)]
PEAKS = [("Όλυμπος", 40.085, 22.358), ("Παρνασσός", 38.535, 22.622), ("Ταΰγετος", 36.954, 22.352),
         ("Ψηλορείτης", 35.228, 24.770), ("Σμόλικας", 40.088, 20.919), ("Γκιώνα", 38.637, 22.243),
         ("Λευκά Όρη", 35.287, 24.000), ("Δίρφυς", 38.623, 23.844)]


# ================================================================== γεωμετρία
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


R = float(2 ** 20)                       # μονάδες περιεχομένου ανά «κόσμο» Mercator
OX, OY = merc(38.5, 24.0)                # αρχή κοντά στην Ελλάδα (ακρίβεια αριθμών στην κάρτα γραφικών)


def load_vector(folder):
    try:
        with open(os.path.join(folder, "greece_vec.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if d.get("gr") else None
    except Exception:  # noqa: BLE001
        return None


# ================================================================== ο χάρτης
class TileMap(Widget):
    zoom = NumericProperty(6.4)
    lat = NumericProperty(38.4)
    lon = NumericProperty(23.9)
    layer = StringProperty("lite")
    min_zoom = 5.0
    max_zoom = 17.5

    __events__ = ("on_select", "on_moved")

    def __init__(self, cache_dir=None, offline_dir=None, **kw):
        super().__init__(**kw)
        self.cache_dir = cache_dir
        self.vec = load_vector(offline_dir) if offline_dir else None
        self.d = max(1.0, dp(1))
        self.sites = []
        self.selected = None
        self.color_of = lambda s: (0.75, 0.36, 0.2, 1)
        self._tex = OrderedDict()
        self._failed = {}
        self._wanted = set()
        self._pending = set()
        self._q = queue.LifoQueue()
        self._hits = []
        self._labels = {}
        self._touches = {}
        self._pinch = None
        self._fly = None
        self._fly_target = self.zoom
        self._base_key = None
        self._vec_g = None
        self._sstate = None
        self._places = [(n, merc(a, o), r) for n, a, o, r in CITIES]
        self._seas = [(n, merc(a, o)) for n, a, o in SEAS]
        self._peaks = [(n, merc(a, o)) for n, a, o in PEAKS]
        self._regions = [(n, merc(a, o)) for n, a, o in (self.vec or {}).get("regions", [])]
        with self.canvas:
            self._bg_col = Color(*LAYERS["lite"]["bg"])
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
        self._sites_later = Clock.create_trigger(self._draw_sites_now, 0.12)
        self.bind(pos=self._trig, size=self._trig, zoom=self._trig, lat=self._trig, lon=self._trig,
                  layer=self._on_layer)
        self.bind(size=self._attr, pos=self._attr, layer=self._attr)
        for _ in range(4):
            threading.Thread(target=self._worker, daemon=True).start()

    def on_select(self, site):
        pass

    def on_moved(self):
        pass

    def layers(self):
        """Τα διαθέσιμα είδη χάρτη, με σειρά."""
        return (["lite"] if self.vec else ["map"]) + ["sat", "topo"]

    # ------------------------------------------------------------------ δεδομένα
    def set_sites(self, sites):
        for s in sites:
            s["_mx"], s["_my"] = merc(s["lat"], s["lon"])
        self.sites = sites
        self._sstate = None
        self._trig()

    def _on_layer(self, *a):
        if self.layer == "lite" and not self.vec:
            self.layer = "map"
            return
        self._tex.clear()
        self._wanted.clear()
        self._base_key = None
        self._sstate = None
        self._trig()

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
        new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))
        W = self.world()
        cx, cy = self.center_world()
        px = (cx + (sx - self.center_x)) / W
        py = (cy - (sy - self.center_y)) / W
        self.zoom = new_zoom
        W2 = self.world()
        self.set_center_world(px * W2 - (sx - self.center_x), py * W2 + (sy - self.center_y))

    def zoom_by(self, dz):
        """Κουμπιά +/−: γρήγορο ομαλό ζουμ στο κέντρο."""
        z = max(self.min_zoom, min(self.max_zoom, (self._fly_target if self._fly else self.zoom) + dz))
        self.fly_to(self.lat, self.lon, z, dur=0.22)

    def fly_to(self, lat, lon, zoom=None, dur=0.6):
        z0, a0, o0 = self.zoom, self.lat, self.lon
        z1 = self.zoom if zoom is None else zoom
        self._fly_target = z1
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
                self._sstate = None
                self._trig()
                self.dispatch("on_moved")
        self._fly = Clock.schedule_interval(step, 1 / 60.0)

    # ------------------------------------------------------------------ online πλακίδια
    def _tile_level(self):
        L = int(math.floor(self.zoom + math.log2(self.d) + 0.25))
        return max(0, min(LAYERS[self.layer].get("max", 18), L))

    def _url(self, z, x, y):
        L = LAYERS[self.layer]
        s = L["subs"][(x + y) % len(L["subs"])] if L["subs"] else ""
        r = "@2x" if L.get("retina") and self.d >= 1.5 else ""
        return L["url"].format(s=s, z=z, x=x, y=y, r=r)

    def _path(self, key):
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
        if self._find(key):
            Clock.schedule_once(lambda dt, k=key: self._loaded(k), 0)
        else:
            self._q.put(key)
        return None

    def _worker(self):
        while True:
            key = self._q.get()
            try:
                if key not in self._wanted or key[0] != self.layer:
                    self._pending.discard(key)
                    continue
                p = self._path(key)
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
        try:
            t = CoreImage(self._find(key), nocache=True).texture
        except Exception:  # noqa: BLE001
            self._failed[key] = time.time()
            return
        self._tex[key] = t
        while len(self._tex) > 120:
            self._tex.popitem(last=False)
        self._base_key = None
        self._trig()

    def _fallback(self, z, x, y):
        for k in range(1, 4):
            if z - k < 0:
                break
            pk = (self.layer, z - k, x >> k, y >> k)
            t = self._tex.get(pk)
            if t is not None:
                size = t.width / (2 ** k)
                sx, sy = x - (pk[2] << k), y - (pk[3] << k)
                return t.get_region(sx * size, t.height - (sy + 1) * size, size, size)
        return None

    # ------------------------------------------------------------------ σχεδίαση
    def _view(self):
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
        st = self._sstate
        if st is None:
            self._draw_sites_now()
        elif abs(st[0] - W) < 1e-6:
            self._ST.xy = (st[1] - cx, cy - st[2])      # μόνο μετακίνηση: μετατόπιση, χωρίς σχεδίαση
            self._sites_later()
        elif time.time() - st[3] > 0.06:
            self._draw_sites_now()
        else:
            self._sites_later()

    def _vector_group(self):
        """Ο διανυσματικός χάρτης: φτιάχνεται μία φορά (πλέγματα τριγώνων + γραμμές)."""
        if self._vec_g is not None:
            return self._vec_g
        g = InstructionGroup()
        v = self.vec
        for key, col in (("nb", NEIGH), ("gr", LAND)):
            g.add(Color(*col))
            for m in v.get(key, []):
                xy = m["v"]
                verts = []
                for i in range(0, len(xy), 2):
                    verts += (xy[i], xy[i + 1], 0.0, 0.0)
                g.add(Mesh(vertices=verts, indices=m["i"], mode="triangles"))
        lines = v.get("lines", {})
        for key, col in (("region", REGION), ("border", BORDER), ("coast", COAST)):
            g.add(Color(*col))
            for pts in lines.get(key, []):
                if len(pts) >= 4:
                    g.add(Line(points=pts, width=1))
        self._vec_g = g
        return g

    def _update_base(self):
        lay = self.layer
        if lay == "lite" and self.vec:
            if self._base_key != "lite":
                self._base_key = "lite"
                self._bg_col.rgba = LAYERS["lite"]["bg"]
                self._base.clear()
                self._base.add(self._vector_group())
            return
        vx0, vy0, vx1, vy1 = self._view()
        L = self._tile_level()
        n = 2 ** L
        parts = []
        self._wanted = set()
        for ty in range(max(0, int(vy0 * n)), min(n - 1, int(vy1 * n)) + 1):
            for tx in range(int(math.floor(vx0 * n)), int(math.floor(vx1 * n)) + 1):
                wx = tx % n
                key = (lay, L, wx, ty)
                self._wanted.add(key)
                t = self._get(key) or self._fallback(L, wx, ty)
                if t is not None:
                    parts.append((key, tx / n, ty / n, (tx + 1) / n, (ty + 1) / n, t))
        bk = (lay, tuple(p[0] for p in parts))
        if bk == self._base_key:
            return
        self._base_key = bk
        self._bg_col.rgba = LAYERS[lay]["bg"]
        self._base.clear()
        if self.vec:                         # ο ενσωματωμένος χάρτης από κάτω, όσο φορτώνουν τα πλακίδια
            self._base.add(self._vector_group())
        self._base.add(Color(1, 1, 1, 1))
        for _, mx0, my0, mx1, my1, t in parts:
            self._base.add(Rectangle(texture=t, pos=((mx0 - OX) * R, -(my1 - OY) * R),
                                     size=((mx1 - mx0) * R, (my1 - my0) * R)))

    def _attr(self, *a):
        g = self._attr_g
        g.clear()
        txt = LAYERS[self.layer].get("attr")
        if not txt:
            return
        t = self._label(txt, 9, False, (0.15, 0.15, 0.15, 1))
        g.add(Color(1, 1, 1, 0.7))
        g.add(Rectangle(pos=(self.right - t.width - dp(8), self.y), size=(t.width + dp(8), t.height + dp(2))))
        g.add(Color(1, 1, 1, 1))
        g.add(Rectangle(texture=t, pos=(self.right - t.width - dp(4), self.y + dp(1)), size=t.size))

    def _label(self, text, size=12, bold=True, color=(1, 1, 1, 1), italic=False):
        key = (text, size, bold, color, italic)
        t = self._labels.get(key)
        if t is None:
            lb = CoreLabel(text=text, font_size=dp(size), bold=bold, italic=italic, color=color)
            lb.refresh()
            t = lb.texture
            if len(self._labels) > 500:
                self._labels.clear()
            self._labels[key] = t
        return t

    def _draw_sites_now(self, *a):
        W = self.world()
        c = self.center_world()
        self._ST.xy = (0, 0)
        self._sstate = (W, c[0], c[1], time.time())
        g = self._sites_g
        g.clear()
        cv = _Rec(g)
        if self.layer == "lite" or self.layer == "map":
            self._draw_places(cv, W, c)
        self._draw_sites(cv, W, c)

    def _draw_places(self, cv, W, c):
        """Ετικέτες χάρτη (πελάγη, περιφέρειες, πόλεις, βουνά) χωρίς επικαλύψεις."""
        z = self.zoom
        pad = max(self.width, self.height) * 0.6
        taken = set()
        cell = dp(22)

        def free(x, y, w, h):
            cells = {(int(i // cell), int(j // cell)) for i in (x, x + w / 2, x + w) for j in (y, y + h)}
            if cells & taken:
                return False
            taken.update(cells)
            return True

        def put(text, mx, my, size, color, bold=False, italic=False, dot=None):
            x, y = self.to_screen(mx, my, W, c)
            if not (self.x - pad <= x <= self.right + pad and self.y - pad <= y <= self.top + pad):
                return
            t = self._label(text, size, bold, color, italic)
            if not free(x - t.width / 2, y, t.width, t.height):
                return
            if dot:
                cv.Color(*dot)
                cv.Ellipse(pos=(x - dp(3), y - dp(3)), size=(dp(6), dp(6)))
                y += dp(4)
            cv.Color(1, 1, 1, 1)
            cv.Rectangle(texture=t, pos=(x - t.width / 2, y), size=t.size)
        if z < 9.5:
            for n, (mx, my) in self._seas:
                put(n, mx, my, 12 if z < 7.5 else 14, (0.25, 0.45, 0.62, 0.8), italic=True)
        if 6.8 <= z < 9.2:
            for n, (mx, my) in self._regions:
                put(n.upper(), mx, my, 10, (0.62, 0.5, 0.4, 0.75), bold=True)
        maxr = 1 if z < 6.8 else (2 if z < 7.8 else 3)
        for n, (mx, my), r in sorted(self._places, key=lambda p: p[2]):
            if r <= maxr:
                put(n, mx, my, 13 if r == 1 else 11, (0.2, 0.2, 0.22, 1), bold=r == 1, dot=(0.35, 0.35, 0.4, 1))
        if z >= 7.3:
            for n, (mx, my) in self._peaks:
                put(n, mx, my, 10, (0.45, 0.35, 0.25, 0.9), italic=True,
                    dot=(0.55, 0.42, 0.3, 1))

    def _draw_sites(self, cv, W, c):
        pts = []
        pad = max(self.width, self.height) * 0.6     # και λίγο έξω από την οθόνη, για ομαλή μετακίνηση
        x0, x1, y0, y1 = self.x - pad, self.right + pad, self.y - pad, self.top + pad
        cx, cy = c
        ox, oy = self.center_x - cx, self.center_y + cy
        for s in self.sites:
            if s.get("_hidden"):
                continue
            x, y = ox + s["_mx"] * W, oy - s["_my"] * W
            if x0 <= x <= x1 and y0 <= y <= y1:
                pts.append((x, y, s))
        hits = []
        if self.zoom < 11.5 and len(pts) > 40:
            groups = cluster(pts, dp(46) if self.zoom < 9 else dp(34))
        else:
            groups = [(x, y, [s]) for x, y, s in pts]
        singles = []
        for x, y, objs in groups:
            if len(objs) == 1:
                singles.append((x, y, objs[0]))
                continue
            imp = any(o.get("important") for o in objs)
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
            self.fly_to(*self._screen_latlon(touch.x, touch.y), min(self.max_zoom, self.zoom + 1.2), dur=0.25)
        return True

    def _screen_latlon(self, sx, sy):
        W = self.world()
        cx, cy = self.center_world()
        return unmerc((cx + sx - self.center_x) / W, (cy - (sy - self.center_y)) / W)

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
            self.fly_to(lat, lon, min(self.zoom + 2.2, 13.5), dur=0.35)
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
    _cls = {"Color": Color, "Ellipse": Ellipse, "Rectangle": Rectangle, "RoundedRectangle": RoundedRectangle,
            "Line": Line}

    def __init__(self, g):
        self.g = g

    def __getattr__(self, name):
        cls = self._cls[name]

        def make(*a, **k):
            ins = cls(*a, **k)
            self.g.add(ins)
            return ins
        return make
