"""
Αρχαία Ελλάδα – διαδραστικός χάρτης με όλους τους αρχαιολογικούς χώρους της Ελλάδας.
Πατάς ένα σημείο → φωτογραφίες, ιστορία, πληροφορίες, οδηγίες. Ελεύθερα δεδομένα: Wikidata, Wikipedia,
Wikimedia Commons, OpenStreetMap/CARTO, Esri, OpenTopoMap.
"""
import hashlib
import json
import os
import threading
import time
import urllib.request
import webbrowser

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.properties import ListProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.stacklayout import StackLayout
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

import data as D
import net
import sites_gr as SG
import tilemap as TM
import ui_widgets as UW
import updater as UP

IS_ANDROID = platform == "android"
DATA_DIR = None
TERRA = (0.75, 0.36, 0.2, 1)
GOLD = (0.95, 0.75, 0.2, 1)
BLUE = (0.1, 0.3, 0.55, 1)
GREECE = (38.4, 23.9, 6.4)
FILTERS = [("all", "Όλοι"), ("important", "Σημαντικοί"), ("unesco", "UNESCO"), ("fav", "Αγαπημένα")]


def esc(t):
    return str(t).replace("&", "&amp;").replace("[", "&bl;").replace("]", "&br;")


def hx(c):
    return UW.hexc(c)


class Tap(ButtonBehavior, BoxLayout):
    pass


class RoundBtn(ButtonBehavior, FloatLayout):
    """Στρογγυλό αιωρούμενο κουμπί με εικονίδιο ή κείμενο."""

    def __init__(self, icon=None, text="", color=(1, 1, 1, 1), fg=BLUE, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(48), dp(48)))
        super().__init__(**kw)
        self._col = color

        def bg(*a):
            self.canvas.before.clear()
            with self.canvas.before:
                Color(0, 0, 0, 0.18)
                RoundedRectangle(pos=(self.x, self.y - dp(2)), size=self.size, radius=[self.height / 2])
                Color(*self._col)
                RoundedRectangle(pos=self.pos, size=self.size, radius=[self.height / 2])
        self.bind(pos=bg, size=bg)
        if icon:
            self.add_widget(UW.Icon(kind=icon, rgba=list(fg), size_hint=(None, None), size=(dp(24), dp(24)),
                                    pos_hint={"center_x": 0.5, "center_y": 0.5}))
        if text:
            self.lab = Label(text=f"[b]{text}[/b]", markup=True, color=fg, font_size=sp(12),
                             pos_hint={"center_x": 0.5, "center_y": 0.5})
            self.add_widget(self.lab)


class ArchaiaApp(App):
    title = "Archaia"
    c_bg = ListProperty([0.965, 0.955, 0.935, 1])

    # ================================================================== εκκίνηση
    def build(self):
        global DATA_DIR
        DATA_DIR = self.user_data_dir
        net.set_dir(DATA_DIR)
        UP.set_dir(DATA_DIR)
        Window.clearcolor = UW.C["bg"]
        self.grad = UW.gradient(UW.PRIMARY, UW.PRIMARY2)
        self.cfg = self.load_cfg()
        self.favs = set(self.cfg.get("favs", []))
        self.filter = self.cfg.get("filter", "all")
        bsites, self.bundled_date = D.bundled()
        self.sites = D.merge(D.builtin(), bsites)
        self.loaded_all = bool(bsites)
        self.img_sem = threading.Semaphore(4)
        self.detail_site = None

        self.sm = ScreenManager(transition=NoTransition())
        self.sm.add_widget(self.build_map_screen())
        for name in ("detail", "list"):
            scr = Screen(name=name)
            root = BoxLayout(orientation="vertical")
            bar = self.top_bar(name)
            root.add_widget(bar)
            sv = ScrollView(do_scroll_x=False, bar_width=dp(4))
            box = BoxLayout(orientation="vertical", size_hint_y=None, padding=(dp(12), dp(12)), spacing=dp(12))
            box.bind(minimum_height=box.setter("height"))
            sv.add_widget(box)
            root.add_widget(sv)
            scr.add_widget(root)
            self.sm.add_widget(scr)
            setattr(self, f"{name}_box", box)
            setattr(self, f"{name}_sv", sv)
        Window.bind(on_keyboard=self._key)
        self.apply_filter()
        if self._need_download():
            Clock.schedule_once(lambda dt: self.load_all(), 1.5)
        if UP.due():
            Clock.schedule_once(lambda dt: self.check_update(), 6)
        return self.sm

    def on_pause(self):
        return True

    def _need_download(self):
        """Οι χώροι υπάρχουν ήδη μέσα στην εφαρμογή· λήψη μόνο αν λείπουν ή είναι πολύ παλιοί (> 6 μήνες)."""
        if not self.bundled_date:
            return True
        try:
            import datetime
            age = (datetime.date.today() - datetime.date.fromisoformat(self.bundled_date)).days
        except ValueError:
            return True
        return age > 180

    def load_cfg(self):
        try:
            with open(os.path.join(DATA_DIR, "settings.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:  # noqa: BLE001
            return {}

    def save_cfg(self):
        self.cfg["favs"] = sorted(self.favs)
        self.cfg["filter"] = self.filter
        self.cfg["view"] = [self.map.lat, self.map.lon, self.map.zoom]
        self.cfg["layer"] = self.map.layer
        try:
            with open(os.path.join(DATA_DIR, "settings.json"), "w", encoding="utf-8") as fh:
                json.dump(self.cfg, fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass

    def on_stop(self):
        self.save_cfg()

    def _key(self, win, key, *a):
        if key == 27:
            ov = getattr(self, "viewer_ov", None)
            if ov is not None and ov.parent is not None:
                ov.parent.remove_widget(ov)
                return True
            if self.sm.current != "map":
                self.sm.current = "map"
                return True
            if self.sheet.parent is not None:
                self.close_sheet()
                return True
        return False

    # ================================================================== χάρτης
    def build_map_screen(self):
        scr = Screen(name="map")
        fl = FloatLayout()
        self.map = TM.TileMap(cache_dir=os.path.join(DATA_DIR, "tiles"), offline_dir=D.OFFLINE, size_hint=(1, 1),
                              pos_hint={"x": 0, "y": 0})
        v = self.cfg.get("view")
        if v:
            self.map.lat, self.map.lon, self.map.zoom = v
        else:
            self.map.lat, self.map.lon, self.map.zoom = GREECE
        lay = self.cfg.get("layer", "lite")
        self.map.layer = lay if lay in self.map.layers() else self.map.layers()[0]
        self.map.color_of = lambda s: SG.PERIOD_COLORS.get(s.get("period", ""), TERRA)
        self.map.bind(on_select=lambda w, s: self.open_sheet(s))
        fl.add_widget(self.map)

        # ---- πάνω: τίτλος + αναζήτηση
        top = BoxLayout(orientation="vertical", size_hint=(0.94, None), pos_hint={"center_x": 0.5, "top": 0.985},
                        spacing=dp(6))
        top.bind(minimum_height=top.setter("height"))
        card = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52), padding=(dp(10), dp(6)),
                         spacing=dp(8))

        def cbg(*a):
            card.canvas.before.clear()
            with card.canvas.before:
                Color(0, 0, 0, 0.15)
                RoundedRectangle(pos=(card.x, card.y - dp(2)), size=card.size, radius=[dp(26)])
                Color(1, 1, 1, 0.97)
                RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(26)])
        card.bind(pos=cbg, size=cbg)
        card.add_widget(UW.Icon(kind="search", rgba=list(BLUE), size_hint=(None, None), size=(dp(24), dp(24)),
                                pos_hint={"center_y": 0.5}))
        self.search_in = TextInput(hint_text="Αναζήτηση: Δελφοί, Κνωσός, Βεργίνα…", multiline=False,
                                   background_color=(0, 0, 0, 0), foreground_color=UW.C["ink"], font_size=sp(16),
                                   padding=(dp(4), dp(10)), cursor_color=BLUE)
        self.search_in.bind(text=lambda *a: (Clock.unschedule(self.do_search), Clock.schedule_once(self.do_search, 0.3)))
        card.add_widget(self.search_in)
        top.add_widget(card)
        self.results = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        self.results.bind(minimum_height=self.results.setter("height"))
        top.add_widget(self.results)
        self.status = Label(text="", markup=True, size_hint_y=None, height=dp(26), color=(1, 1, 1, 1),
                            font_size=sp(12), halign="center")

        def sbg(*a):
            self.status.canvas.before.clear()
            if self.status.text:
                with self.status.canvas.before:
                    Color(0.1, 0.12, 0.16, 0.72)
                    w = self.status.texture_size[0] + dp(20)
                    RoundedRectangle(pos=(self.status.center_x - w / 2, self.status.y), size=(w, self.status.height),
                                     radius=[dp(13)])
        self.status.bind(pos=sbg, size=sbg, texture_size=sbg)
        top.add_widget(self.status)
        fl.add_widget(top)

        # ---- δεξιά: είδος χάρτη, Ελλάδα, θέση μου, λίστα (ψηλά, για να μην τα σκεπάζει η κάρτα χώρου)
        col = BoxLayout(orientation="vertical", size_hint=(None, None), width=dp(50), spacing=dp(9),
                        pos_hint={"right": 0.975, "top": 0.86})
        col.bind(minimum_height=col.setter("height"))
        self.layer_btn = RoundBtn(text=TM.LAYERS[self.map.layer]["short"], size=(dp(50), dp(50)))
        self.layer_btn.bind(on_release=lambda *a: self.next_layer())
        b_home = RoundBtn(icon="home", size=(dp(50), dp(50)))
        b_home.bind(on_release=lambda *a: self.map.fly_to(*GREECE, dur=0.4))
        b_me = RoundBtn(icon="pin", size=(dp(50), dp(50)))
        b_me.bind(on_release=lambda *a: self.my_location())
        b_list = RoundBtn(icon="list", size=(dp(50), dp(50)))
        b_list.bind(on_release=lambda *a: self.open_list())
        for b in (self.layer_btn, b_home, b_me, b_list):
            col.add_widget(b)
        fl.add_widget(col)
        # ---- αριστερά: ζουμ + / −
        zc = BoxLayout(orientation="vertical", size_hint=(None, None), width=dp(54), height=dp(118), spacing=dp(10),
                       pos_hint={"x": 0.025, "top": 0.74})
        b_in = RoundBtn(text="+", fg=BLUE, size=(dp(54), dp(54)))
        b_in.lab.font_size = sp(26)
        b_in.bind(on_release=lambda *a: self.map.zoom_by(1))
        b_out = RoundBtn(text="−", fg=BLUE, size=(dp(54), dp(54)))
        b_out.lab.text = "[b]-[/b]"
        b_out.lab.font_size = sp(30)
        b_out.bind(on_release=lambda *a: self.map.zoom_by(-1))
        zc.add_widget(b_in)
        zc.add_widget(b_out)
        fl.add_widget(zc)

        # ---- κάτω: φίλτρα
        self.filter_bar = BoxLayout(size_hint=(0.94, None), height=dp(40), spacing=dp(6),
                                    pos_hint={"center_x": 0.5, "y": 0.015})
        fl.add_widget(self.filter_bar)
        self.render_filters()

        # ---- κάρτα επιλεγμένου χώρου
        self.sheet = BoxLayout(orientation="vertical", size_hint=(0.96, None), height=dp(330),
                               pos_hint={"center_x": 0.5, "y": 0.075}, opacity=0)

        def shbg(*a):
            self.sheet.canvas.before.clear()
            with self.sheet.canvas.before:
                Color(0, 0, 0, 0.22)
                RoundedRectangle(pos=(self.sheet.x, self.sheet.y - dp(3)), size=self.sheet.size, radius=[dp(22)])
                Color(*UW.C["card"])
                RoundedRectangle(pos=self.sheet.pos, size=self.sheet.size, radius=[dp(22)])
        self.sheet.bind(pos=shbg, size=shbg)
        self.map_layout = fl          # η κάρτα μπαίνει μόνο όταν ανοίγει (κρυφή δεν «τρώει» αγγίγματα)
        scr.add_widget(fl)
        return scr

    def render_filters(self):
        self.filter_bar.clear_widgets()
        for key, label in FILTERS:
            on = key == self.filter
            b = Button(text=label, bold=on, background_normal="", font_size=sp(13),
                       background_color=TERRA if on else (1, 1, 1, 0.94), color=(1, 1, 1, 1) if on else UW.C["ink"])
            b.bind(on_release=lambda w, k=key: self.set_filter(k))
            self.filter_bar.add_widget(b)

    def set_filter(self, k):
        self.filter = k
        self.render_filters()
        self.apply_filter()
        self.save_cfg()

    def apply_filter(self):
        f = self.filter
        n = 0
        for s in self.sites:
            show = (f == "all" or (f == "important" and s.get("important")) or (f == "unesco" and s.get("unesco"))
                    or (f == "fav" and s["id"] in self.favs))
            s["_hidden"] = not show
            n += show
        self.map.set_sites(self.sites)
        total = len(self.sites)
        extra = "" if self.loaded_all else " · φόρτωση όλων των χώρων…"
        self.set_status(f"{n} αρχαιολογικοί χώροι{'' if f == 'all' else ' (' + dict(FILTERS)[f] + ')'}"
                        f"{extra if f == 'all' else ''}" if total else "")

    def set_status(self, text):
        self.status.text = esc(text)

    def next_layer(self):
        keys = self.map.layers()
        cur = self.map.layer if self.map.layer in keys else keys[0]
        self.map.layer = keys[(keys.index(cur) + 1) % len(keys)]
        self.layer_btn.lab.text = f"[b]{TM.LAYERS[self.map.layer]['short']}[/b]"
        extra = "" if self.map.layer == "lite" else " (με internet)"
        self.toast(f"Χάρτης: {TM.LAYERS[self.map.layer]['name']}{extra}")
        self.save_cfg()

    def load_all(self):
        def work():
            try:
                extra = D.fetch_all()
                merged = D.merge(D.builtin(), extra)
                Clock.schedule_once(lambda dt: done(merged, None), 0)
            except Exception as e:  # noqa: BLE001
                Clock.schedule_once(lambda dt, err=e: done(None, err), 0)

        def done(merged, err):
            if merged:
                sel = self.map.selected
                self.sites = merged
                self.loaded_all = True
                self.apply_filter()
                if sel:
                    self.map.selected = next((s for s in merged if s["id"] == sel["id"]), None)
                self.toast(f"Φορτώθηκαν {len(merged)} αρχαιολογικοί χώροι")
            else:
                self.set_status(f"{len(self.sites)} σημαντικότεροι χώροι · χωρίς internet")
        threading.Thread(target=work, daemon=True).start()

    # ---------------- αναζήτηση
    def do_search(self, *a):
        self.results.clear_widgets()
        q = self.search_in.text.strip()
        if len(q) < 2:
            return
        hits = D.search(self.sites, q)[:7]
        if not hits:
            hits = []
            lb = Label(text="Δεν βρέθηκε χώρος", size_hint_y=None, height=dp(40), color=UW.C["muted"])
            self.results.add_widget(lb)
        for s in hits:
            r = Tap(orientation="horizontal", size_hint_y=None, height=dp(48), padding=(dp(14), dp(4)), spacing=dp(8))

            def bg(*a, w=r):
                w.canvas.before.clear()
                with w.canvas.before:
                    Color(1, 1, 1, 0.97)
                    RoundedRectangle(pos=w.pos, size=w.size, radius=[dp(12)])
            r.bind(pos=bg, size=bg)
            col = SG.PERIOD_COLORS.get(s.get("period", ""), TERRA)
            r.add_widget(UW.text(f"[b]{esc(s['name'])}[/b]\n[size={int(sp(11))}][color={hx(col)}]"
                                 f"{esc(' · '.join(x for x in (s.get('period'), s.get('region')) if x))}[/color][/size]",
                                 14))
            r.bind(on_release=lambda w, ss=s: self.pick(ss))
            self.results.add_widget(r)

    def pick(self, s):
        self.results.clear_widgets()
        self.search_in.text = ""
        self.search_in.focus = False
        if s.get("_hidden"):
            self.set_filter("all")
        self.sm.current = "map"
        self.map.select(s, fly=True)

    # ================================================================== κάρτα χώρου
    def open_sheet(self, s):
        sh = self.sheet
        sh.clear_widgets()
        if not s:
            return self.close_sheet()
        frame = FloatLayout(size_hint_y=None, height=dp(150))
        col = SG.PERIOD_COLORS.get(s.get("period", ""), TERRA)

        def fbg(*a):
            frame.canvas.before.clear()
            with frame.canvas.before:
                Color(1, 1, 1, 1)
                RoundedRectangle(pos=frame.pos, size=frame.size, radius=[dp(22), dp(22), 0, 0], texture=self.grad)
        frame.bind(pos=fbg, size=fbg)
        img = self.new_image(size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        frame.add_widget(img)
        close = RoundBtn(text="X", color=(0, 0, 0, 0.45), fg=(1, 1, 1, 1), size=(dp(36), dp(36)),
                         pos_hint={"right": 0.98, "top": 0.96})
        close.bind(on_release=lambda *a: self.close_sheet())
        frame.add_widget(close)
        if s.get("unesco"):
            frame.add_widget(UW.Chip("UNESCO", GOLD, pos_hint={"x": 0.03, "top": 0.94}))
        sh.add_widget(frame)
        body = BoxLayout(orientation="vertical", padding=(dp(14), dp(8), dp(14), dp(10)), spacing=dp(4))
        body.add_widget(Label(text=f"[b]{esc(s['name'])}[/b]", markup=True, color=UW.C["ink"], font_size=sp(19),
                              size_hint_y=None, height=dp(28), halign="left", valign="middle", shorten=True,
                              text_size=(Window.width * 0.88, dp(28))))
        meta = " · ".join(x for x in (s.get("period"), s.get("region")) if x)
        if meta:
            body.add_widget(Label(text=f"[color={hx(col)}][b]{esc(meta)}[/b][/color]", markup=True, font_size=sp(13),
                                  size_hint_y=None, height=dp(20), halign="left", valign="middle", shorten=True,
                                  text_size=(Window.width * 0.88, dp(20))))
        self.sheet_desc = Label(text=esc(s.get("desc") or "Φόρτωση πληροφοριών…"), color=UW.C["muted"],
                                font_size=sp(13), halign="left", valign="top", max_lines=3, shorten=False,
                                text_size=(Window.width * 0.88, None))
        self.sheet_desc.bind(width=lambda w, v: setattr(w, "text_size", (v, w.height)))
        body.add_widget(self.sheet_desc)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        for txt, cb, c in (("Περισσότερα", lambda: self.open_detail(s), BLUE),
                           ("Οδηγίες", lambda: self.directions(s), TERRA),
                           ("Αγαπημένο" if s["id"] in self.favs else "+ Αγαπημένα", lambda: self.toggle_fav(s),
                            GOLD if s["id"] in self.favs else UW.C["line"])):
            b = Button(text=txt, bold=True, background_normal="", background_color=c, font_size=sp(13),
                       color=(1, 1, 1, 1) if c != UW.C["line"] else UW.C["ink"])
            b.bind(on_release=lambda w, f=cb: f())
            row.add_widget(b)
        body.add_widget(row)
        sh.add_widget(body)
        Animation.cancel_all(sh)
        if sh.parent is None:
            sh.opacity = 0
            self.map_layout.add_widget(sh)
        Animation(opacity=1, d=0.15).start(sh)
        self.fill_image(img, s)
        if not s.get("desc") or not s.get("top"):
            self.bg(lambda: D.wiki_summary(s), lambda r: self._sheet_text(s, r))

    def _sheet_text(self, s, r):
        if not r or self.map.selected is not s:
            if not s.get("desc"):
                self.sheet_desc.text = "Πάτα «Περισσότερα» για λεπτομέρειες."
            return
        if r.get("text") and not s.get("top"):
            self.sheet_desc.text = esc(r["text"][:260])
        if r.get("img") and not s.get("img"):
            s["img"] = r["img"]

    def close_sheet(self):
        sh = self.sheet
        Animation.cancel_all(sh)
        if sh.parent is not None:
            sh.parent.remove_widget(sh)
        sh.opacity = 0
        self.map.selected = None
        self.map._sstate = None
        self.map._trig()

    def fill_image(self, img, s):
        """Φωτογραφία χώρου: από το Wikidata ή από τη Wikipedia."""
        if s.get("img"):
            return self.load_image(img, D.thumb(s["img"], 720))

        def get():
            r = D.wiki_summary(s)
            u = (r or {}).get("img", "")
            if not u:
                ph = D.photos(s, limit=8)
                u = ph[0]["full"] if ph else ""
            return u

        def done(u):
            if u:
                s["img"] = u
                self.load_image(img, D.thumb(u, 720))
        self.bg(get, done)

    # ================================================================== λεπτομέρειες
    def open_detail(self, s):
        self.detail_site = s
        box = self.detail_box
        box.clear_widgets()
        self.detail_sv.scroll_y = 1
        self.detail_title.text = s["name"]
        col = SG.PERIOD_COLORS.get(s.get("period", ""), TERRA)
        hero = FloatLayout(size_hint_y=None, height=dp(250))

        def hbg(*a):
            hero.canvas.before.clear()
            with hero.canvas.before:
                Color(1, 1, 1, 1)
                RoundedRectangle(pos=hero.pos, size=hero.size, radius=[dp(18)], texture=self.grad)
        hero.bind(pos=hbg, size=hbg)
        img = self.new_image(size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        hero.add_widget(img)
        shade = Widget(size_hint=(1, 0.5), pos_hint={"x": 0, "y": 0})

        def sh(*a):
            shade.canvas.clear()
            with shade.canvas:
                for i in range(10):
                    Color(0, 0, 0, 0.06 * (10 - i))
                    Rectangle(pos=(shade.x, shade.y + shade.height * i / 10), size=(shade.width, shade.height / 10))
        shade.bind(pos=sh, size=sh)
        hero.add_widget(shade)
        tx = BoxLayout(orientation="vertical", size_hint=(1, None), pos_hint={"x": 0, "y": 0}, padding=(dp(14), dp(10)))
        tx.bind(minimum_height=tx.setter("height"))
        tx.add_widget(UW.text(f"[b]{esc(s['name'])}[/b]", 24, (1, 1, 1, 1)))
        meta = " · ".join(x for x in (s.get("period"), s.get("region")) if x)
        if meta:
            tx.add_widget(UW.text(esc(meta), 14, (1, 1, 1, 0.95)))
        hero.add_widget(tx)
        box.add_widget(hero)
        self.fill_image(img, s)

        chips = [(x, c) for x, c in ((s.get("period"), col), ("UNESCO", GOLD) if s.get("unesco") else (None, None))
                 if x]
        if chips:
            st = StackLayout(size_hint_y=None, spacing=dp(6))
            st.bind(minimum_height=st.setter("height"))
            for t, c in chips:
                st.add_widget(UW.Chip(t, c))
            box.add_widget(st)

        self.btn_row(box, [("Οδηγίες", lambda: self.directions(s), TERRA),
                           ("Στον χάρτη", lambda: self.pick(s), BLUE),
                           ("Αφαίρεση από αγαπημένα" if s["id"] in self.favs else "+ Αγαπημένα",
                            lambda: (self.toggle_fav(s), self.open_detail(s)), None)])

        # φωτογραφίες
        pc = UW.CardBox(pad=12)
        box.add_widget(pc)
        pc.add_widget(UW.text(f"[b][color={hx(BLUE)}]Φωτογραφίες[/color][/b]", 16))
        ph_holder = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        ph_holder.bind(minimum_height=ph_holder.setter("height"))
        pc.add_widget(ph_holder)
        ph_holder.add_widget(UW.text("Αναζήτηση φωτογραφιών…", 13, UW.C["muted"]))
        self.bg(lambda: D.photos(s), lambda ph: self.show_photos(ph_holder, ph, s) if self.detail_site is s else None,
                lambda e: self.show_photos(ph_holder, [], s) if self.detail_site is s else None)

        # κείμενο
        c = UW.CardBox(pad=14)
        box.add_widget(c)
        c.add_widget(UW.text(f"[b][color={hx(BLUE)}]Ιστορία & περιγραφή[/color][/b]", 16))
        if s.get("desc"):
            c.add_widget(UW.text(esc(s["desc"]), 15))
        txt_holder = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        txt_holder.bind(minimum_height=txt_holder.setter("height"))
        c.add_widget(txt_holder)
        txt_holder.add_widget(UW.text("Φόρτωση από τη Wikipedia…", 13, UW.C["muted"]))

        def show_text(r):
            txt_holder.clear_widgets()
            if not r or not r.get("text"):
                txt_holder.add_widget(UW.text("Δεν υπάρχει ακόμα άρθρο στην ελληνική Wikipedia.", 13, UW.C["muted"]))
                return
            if r["text"].strip() != (s.get("desc") or "").strip():
                txt_holder.add_widget(UW.text(esc(r["text"]), 15))
            if r.get("url"):
                self.btn_row(txt_holder, [("Όλο το άρθρο στη Wikipedia", lambda: webbrowser.open(r["url"]), None)])
        self.bg(lambda: D.wiki_summary(s), show_text, lambda e: show_text(None))

        # στοιχεία + γκαλερί
        info = UW.CardBox(pad=14)
        box.add_widget(info)
        info.add_widget(UW.text(f"[b][color={hx(BLUE)}]Στοιχεία[/color][/b]", 16))
        dist = self.dist_text(s)
        lines = [f"[b]Συντεταγμένες:[/b] {s['lat']:.4f}, {s['lon']:.4f}"]
        if dist:
            lines.append(f"[b]Απόσταση από σένα:[/b] {dist}")
        for ln in lines:
            info.add_widget(UW.text(ln, 14))
        gal = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        gal.bind(minimum_height=gal.setter("height"))
        box.add_widget(gal)

        def show_details(d):
            if not d:
                return
            rows = [("Τύπος", ", ".join(d.get("types", [])[:3])), ("Χρονολόγηση", d.get("year")),
                    ("Περιοχή", d.get("admLabel")),
                    ("Επισκέπτες τον χρόνο", f"{int(float(d['vis'])):,}".replace(",", ".") if d.get("vis") else "")]
            for k, v in rows:
                if v:
                    info.add_widget(UW.text(f"[b]{k}:[/b] {esc(v)}", 14))
            if d.get("web"):
                self.btn_row(info, [("Επίσημη ιστοσελίδα / εισιτήρια", lambda: webbrowser.open(d["web"]), None)])
        if s.get("qid"):
            self.bg(lambda: D.details(s["qid"]), show_details)

        # κοντινοί χώροι
        near = sorted((x for x in self.sites if x is not s),
                      key=lambda x: D.distance(s["lat"], s["lon"], x["lat"], x["lon"]))[:8]
        c = UW.CardBox(pad=12)
        box.add_widget(c)
        c.add_widget(UW.text(f"[b][color={hx(BLUE)}]Κοντινοί αρχαιολογικοί χώροι[/color][/b]", 16))
        for x in near:
            km = D.distance(s["lat"], s["lon"], x["lat"], x["lon"])
            r = Tap(orientation="horizontal", size_hint_y=None, height=dp(48), padding=(dp(8), dp(4)))

            def rbg(*a, w=r, cc=SG.PERIOD_COLORS.get(x.get("period", ""), TERRA)):
                w.canvas.before.clear()
                with w.canvas.before:
                    Color(*UW.C["bg"])
                    RoundedRectangle(pos=w.pos, size=w.size, radius=[dp(10)])
                    Color(*cc)
                    RoundedRectangle(pos=(w.x, w.y + dp(6)), size=(dp(4), w.height - dp(12)), radius=[dp(2)])
            r.bind(pos=rbg, size=rbg)
            r.add_widget(UW.text(f"[b]{esc(x['name'])}[/b]\n[size={int(sp(11))}]{km:.1f} χλμ."
                                 f"{' · ' + esc(x['period']) if x.get('period') else ''}[/size]", 14))
            r.bind(on_release=lambda w, xx=x: self.open_detail(xx))
            c.add_widget(r)
        c2 = UW.CardBox(pad=10)
        box.add_widget(c2)
        c2.add_widget(UW.text("Πηγές: Wikidata, Wikipedia, Wikimedia Commons (ελεύθερες άδειες). Πριν την επίσκεψη "
                              "έλεγξε ωράριο και εισιτήριο στο odysseus.culture.gr.", 11, UW.C["muted"]))
        self.btn_row(c2, [("Κοινοποίηση", lambda: self.share(s), None),
                          ("Ωράρια (Οδυσσεύς)", lambda: webbrowser.open("https://odysseus.culture.gr/"), None)])
        self.sm.current = "detail"

    def show_photos(self, holder, photos, s, shown=12):
        """Πλέγμα φωτογραφιών (3 ανά σειρά)· πάτημα = μεγάλη προβολή με εναλλαγή."""
        holder.clear_widgets()
        if not photos:
            holder.add_widget(UW.text("Δεν βρέθηκαν φωτογραφίες γι' αυτόν τον χώρο (ή δεν υπάρχει σύνδεση).", 13,
                                      UW.C["muted"]))
            return
        holder.add_widget(UW.text(f"{len(photos)} φωτογραφίες από Wikimedia Commons · πάτα για μεγέθυνση", 12,
                                  UW.C["muted"]))
        gap = dp(6)
        side = (Window.width - dp(24) - dp(24) - 2 * gap) / 3
        grid = StackLayout(size_hint_y=None, spacing=gap)
        grid.bind(minimum_height=grid.setter("height"))
        for i, ph in enumerate(photos[:shown]):
            fr = Tap(size_hint=(None, None), size=(side, side))

            def fb(*a, w=fr):
                w.canvas.before.clear()
                with w.canvas.before:
                    Color(*UW.C["line"])
                    RoundedRectangle(pos=w.pos, size=w.size, radius=[dp(10)])
            fr.bind(pos=fb, size=fb)
            im = self.new_image()
            try:
                im.fit_mode = "cover"
            except Exception:  # noqa: BLE001
                pass
            fr.add_widget(im)
            fr.bind(on_release=lambda w, k=i: self.viewer(photos, k))
            grid.add_widget(fr)
            self.load_image(im, ph["thumb"])
        holder.add_widget(grid)
        if len(photos) > shown:
            self.btn_row(holder, [(f"Όλες οι φωτογραφίες ({len(photos)})",
                                   lambda: self.show_photos(holder, photos, s, len(photos)), None)])

    def viewer(self, photos, index=0):
        """Φωτογραφία σε όλη την οθόνη. Σύρε ή πάτα δεξιά/αριστερά για την επόμενη/προηγούμενη, στη μέση για κλείσιμο."""
        if isinstance(photos, str):
            photos = [{"thumb": photos, "full": photos, "author": "", "title": ""}]
        st = {"i": index}
        ov = Tap(orientation="vertical")

        def bg(*a):
            ov.canvas.before.clear()
            with ov.canvas.before:
                Color(0, 0, 0, 0.95)
                Rectangle(pos=ov.pos, size=ov.size)
        ov.bind(pos=bg, size=bg)
        im = Image(opacity=0)
        try:
            im.fit_mode = "contain"
        except Exception:  # noqa: BLE001
            pass
        ov.add_widget(im)
        cap = Label(size_hint_y=None, height=dp(52), color=(1, 1, 1, 0.9), font_size=sp(12), halign="center",
                    valign="middle", markup=True)
        cap.bind(size=lambda w, v: setattr(w, "text_size", v))
        ov.add_widget(cap)
        scr = self.sm.get_screen("detail")

        def show():
            ph = photos[st["i"]]
            im.opacity = 0
            im.source = ""
            by = f" · {esc(ph['author'])}" if ph.get("author") else ""
            cap.text = (f"[b]{st['i'] + 1} / {len(photos)}[/b]  {esc(ph.get('title', ''))[:60]}{by}\n"
                        f"[size={int(sp(10))}]◀ σύρε ή πάτα στις άκρες · πάτα στη μέση για κλείσιμο ▶[/size]")
            self.load_image(im, ph["full"], fallback=ph["thumb"])

        def go(d):
            st["i"] = (st["i"] + d) % len(photos)
            show()

        def up(w, touch):
            if touch.grab_current is not w or ov.parent is None:
                return
            touch.ungrab(w)
            dx = touch.x - touch.ox
            if abs(dx) > dp(50):
                go(-1 if dx > 0 else 1)
            elif touch.x < w.width * 0.28 and len(photos) > 1:
                go(-1)
            elif touch.x > w.width * 0.72 and len(photos) > 1:
                go(1)
            else:
                scr.remove_widget(ov)
            return True
        ov.bind(on_touch_up=up)
        scr.add_widget(ov)
        self.viewer_ov = ov
        show()

    # ================================================================== λίστα
    def open_list(self):
        box = self.list_box
        box.clear_widgets()
        self.list_sv.scroll_y = 1
        favs = [s for s in self.sites if s["id"] in self.favs]
        if favs:
            c = UW.CardBox(pad=12)
            box.add_widget(c)
            c.add_widget(UW.text(f"[b][color={hx(GOLD)}]Αγαπημένα ({len(favs)})[/color][/b]", 16))
            for s in favs:
                self.list_row(c, s)
        groups = {}
        for s in self.sites:
            if s.get("important"):
                groups.setdefault(s.get("region") or "Άλλοι", []).append(s)
        c = UW.CardBox(pad=12)
        box.add_widget(c)
        c.add_widget(UW.text(f"[b][color={hx(BLUE)}]Οι σημαντικότεροι ανά περιοχή[/color][/b]\n"
                             f"[size={int(sp(12))}]Σύνολο στον χάρτη: {len(self.sites)} χώροι[/size]", 16))
        for g in sorted(groups, key=lambda k: -len(groups[k]))[:25]:
            sec = UW.Section(f"{g} ({len(groups[g])})", opened=False,
                             builder=lambda body, items=groups[g]: [self.list_row(body, s) for s in
                                                                   sorted(items, key=lambda x: -x.get("n", 0))[:60]])
            c.add_widget(sec)
        leg = UW.CardBox(pad=12)
        box.add_widget(leg)
        leg.add_widget(UW.text(f"[b][color={hx(BLUE)}]Χρώματα εποχών στον χάρτη[/color][/b]", 15))
        st = StackLayout(size_hint_y=None, spacing=dp(6))
        st.bind(minimum_height=st.setter("height"))
        for p in SG.PERIODS:
            st.add_widget(UW.Chip(p, SG.PERIOD_COLORS[p]))
        st.add_widget(UW.Chip("Άλλοι χώροι", TERRA))
        st.add_widget(UW.Chip("Χρυσό περίγραμμα: UNESCO", GOLD))
        leg.add_widget(st)
        self.sm.current = "list"

    def list_row(self, parent, s):
        r = Tap(orientation="horizontal", size_hint_y=None, height=dp(50), padding=(dp(10), dp(4)))
        col = SG.PERIOD_COLORS.get(s.get("period", ""), TERRA)

        def bg(*a, w=r):
            w.canvas.before.clear()
            with w.canvas.before:
                Color(*UW.C["bg"])
                RoundedRectangle(pos=w.pos, size=w.size, radius=[dp(10)])
                Color(*col)
                RoundedRectangle(pos=(w.x, w.y + dp(6)), size=(dp(4), w.height - dp(12)), radius=[dp(2)])
        r.bind(pos=bg, size=bg)
        sub = " · ".join(x for x in (s.get("period"), "UNESCO" if s.get("unesco") else "", (s.get("desc") or "")[:50])
                         if x)
        r.add_widget(UW.text(f"[b]{esc(s['name'])}[/b]\n[size={int(sp(11))}]{esc(sub)}[/size]", 14))
        r.bind(on_release=lambda w: self.pick(s))
        parent.add_widget(r)

    # ================================================================== γενικά
    def top_bar(self, name):
        bar = BoxLayout(size_hint_y=None, height=dp(56), padding=(dp(6), dp(6)), spacing=dp(6))

        def bg(*a):
            bar.canvas.before.clear()
            with bar.canvas.before:
                Color(1, 1, 1, 1)
                Rectangle(pos=bar.pos, size=bar.size, texture=self.grad)
        bar.bind(pos=bg, size=bg)
        b = Button(text="<", bold=True, font_size=sp(24), size_hint_x=None, width=dp(46), background_normal="",
                   background_color=(1, 1, 1, 0.15))
        b.bind(on_release=lambda *a: setattr(self.sm, "current", "map"))
        bar.add_widget(b)
        lb = Label(text="Αρχαιολογικοί χώροι" if name == "list" else "", bold=True, font_size=sp(19), halign="left",
                   valign="middle", shorten=True)
        lb.bind(size=lambda w, s: setattr(w, "text_size", s))
        bar.add_widget(lb)
        if name == "detail":
            self.detail_title = lb
        return bar

    def btn_row(self, parent, items, h=46):
        row = BoxLayout(size_hint_y=None, height=dp(h), spacing=dp(6))
        for txt, cb, col in items:
            b = Button(text=txt, bold=True, background_normal="", font_size=sp(13), halign="center",
                       background_color=col or UW.C["line"], color=(1, 1, 1, 1) if col else UW.C["ink"])
            b.bind(size=lambda w, s: setattr(w, "text_size", (s[0] - dp(6), None)))
            b.bind(on_release=lambda *a, f=cb: f())
            row.add_widget(b)
        parent.add_widget(row)

    def new_image(self, **kw):
        im = Image(opacity=0, **kw)
        try:
            im.fit_mode = "cover"
        except Exception:  # noqa: BLE001
            im.allow_stretch, im.keep_ratio = True, False
        return im

    def load_image(self, img, url, fallback=None):
        if not url:
            return
        path = os.path.join(net.CACHE or DATA_DIR, "img_" + hashlib.sha1(url.encode()).hexdigest() + ".jpg")

        def work():
            try:
                if not os.path.exists(path):
                    with self.img_sem:
                        req = urllib.request.Request(url, headers={"User-Agent": net.UA})
                        with urllib.request.urlopen(req, timeout=30, context=net._ctx()) as r:
                            data = r.read()
                    with open(path, "wb") as fh:
                        fh.write(data)
                Clock.schedule_once(lambda dt: (setattr(img, "source", path),
                                                Animation(opacity=1, d=0.35).start(img)), 0)
            except Exception:  # noqa: BLE001
                if fallback and fallback != url:
                    Clock.schedule_once(lambda dt: self.load_image(img, fallback), 0)
        threading.Thread(target=work, daemon=True).start()

    def bg(self, fn, done, fail=None):
        def work():
            try:
                r = fn()
                Clock.schedule_once(lambda dt: done(r), 0)
            except Exception as e:  # noqa: BLE001
                if fail:
                    Clock.schedule_once(lambda dt, err=e: fail(err), 0)
        threading.Thread(target=work, daemon=True).start()

    def toast(self, text):
        fl = self.sm.get_screen(self.sm.current).children[0]
        lb = Label(text=f"[b]{esc(text)}[/b]", markup=True, size_hint=(None, None), color=(1, 1, 1, 1),
                   font_size=sp(14), padding=(dp(16), dp(10)), pos_hint={"center_x": 0.5, "y": 0.13})
        lb.bind(texture_size=lambda w, t: setattr(w, "size", t))

        def bg(*a):
            lb.canvas.before.clear()
            with lb.canvas.before:
                Color(0.1, 0.1, 0.12, 0.88)
                RoundedRectangle(pos=lb.pos, size=lb.size, radius=[dp(18)])
        lb.bind(pos=bg, size=bg)
        try:
            fl.add_widget(lb)
        except Exception:  # noqa: BLE001
            return
        Clock.schedule_once(lambda dt: lb.parent and lb.parent.remove_widget(lb), 3)

    def toggle_fav(self, s):
        if s["id"] in self.favs:
            self.favs.discard(s["id"])
            self.toast("Αφαιρέθηκε από τα αγαπημένα")
        else:
            self.favs.add(s["id"])
            self.toast("Προστέθηκε στα αγαπημένα")
        self.save_cfg()
        if self.map.selected is s:
            self.open_sheet(s)

    def directions(self, s):
        webbrowser.open(f"https://www.google.com/maps/dir/?api=1&destination={s['lat']},{s['lon']}&travelmode=driving")

    def share(self, s):
        text = f"{s['name']} – https://www.google.com/maps/search/?api=1&query={s['lat']},{s['lon']}"
        if not IS_ANDROID:
            return self.toast(text)
        try:
            from jnius import autoclass, cast
            Intent = autoclass("android.content.Intent")
            String = autoclass("java.lang.String")
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            i = Intent(Intent.ACTION_SEND)
            i.setType("text/plain")
            i.putExtra(Intent.EXTRA_TEXT, cast("java.lang.CharSequence", String(text)))
            act.startActivity(Intent.createChooser(i, cast("java.lang.CharSequence", String("Κοινοποίηση"))))
        except Exception:  # noqa: BLE001
            pass

    # ---------------- θέση
    def my_location(self):
        if not IS_ANDROID:
            return self.toast("Η θέση λειτουργεί στο κινητό")
        try:
            from android.permissions import Permission, check_permission, request_permissions
            if not check_permission(Permission.ACCESS_FINE_LOCATION):
                request_permissions([Permission.ACCESS_FINE_LOCATION, Permission.ACCESS_COARSE_LOCATION],
                                    lambda p, g: Clock.schedule_once(lambda dt: self._locate(), 0.3) if any(g) else
                                    None)
                return
        except Exception:  # noqa: BLE001
            pass
        self._locate()

    def _locate(self):
        try:
            from jnius import autoclass, cast
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            Ctx = autoclass("android.content.Context")
            lm = cast("android.location.LocationManager", act.getSystemService(Ctx.LOCATION_SERVICE))
            best = None
            for prov in ("gps", "network", "passive"):
                try:
                    loc = lm.getLastKnownLocation(prov)
                except Exception:  # noqa: BLE001
                    loc = None
                if loc is not None and (best is None or loc.getTime() > best.getTime()):
                    best = loc
            if best is None:
                return self.toast("Δεν βρέθηκε θέση· άνοιξε την Τοποθεσία στο κινητό")
            self.me = (best.getLatitude(), best.getLongitude())
            self.map.fly_to(self.me[0], self.me[1], 10.5)
            near = min(self.sites, key=lambda s: D.distance(self.me[0], self.me[1], s["lat"], s["lon"]))
            self.toast(f"Πιο κοντινός: {near['name']} ({self.dist_text(near)})")
        except Exception:  # noqa: BLE001
            self.toast("Δεν ήταν δυνατή η εύρεση θέσης")

    def dist_text(self, s):
        me = getattr(self, "me", None)
        if not me:
            return ""
        km = D.distance(me[0], me[1], s["lat"], s["lon"])
        return f"{km:.1f} χλμ." if km < 100 else f"{int(km)} χλμ."

    def check_update(self):
        def work():
            try:
                res = UP.check()
            except Exception:  # noqa: BLE001
                res = None
            if res:
                Clock.schedule_once(lambda dt: self.toast("Υπάρχει νέα έκδοση της εφαρμογής στο GitHub"), 0)
        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    ArchaiaApp().run()
