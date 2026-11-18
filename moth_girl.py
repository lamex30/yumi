"""Yumi: a sleepy moth-girl desktop assistant (GTK4 + gtk4-layer-shell).

One transparent layer-shell overlay covers the screen; only what is really drawn catches the
pointer (the input region follows her, the moth, the file pile, menus and bubbles). All the
behaviour lives in yumi/brain.py; this file draws its ops with cairo and wires GTK, the
file monitors, the job queue and the system watchers to it.

  yumi                 start (or do nothing if she is already running)
  yumi --toggle        pop out / hide / wake up (bind it to a key, e.g. Super+Z)
  yumi --pipette       pick a color from the screen
  yumi --home          send her back to the bottom-right corner
  yumi --recent        open the pile of recent files
  yumi --sleep         Do Not Disturb on / off
  yumi --monitor       move her to the next screen
  yumi --ask "фраза"   say it to her as if the mic had heard it (voice, bubble, answer)
  yumi --quit          let her go
Env: YUMI_SCALE (integer pixel scale, overrides settings).
"""
import json
import math
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# gtk4-layer-shell is already loaded; the tools she starts must not inherit it (see bin/yumi)
os.environ['LD_PRELOAD'] = ':'.join(p for p in os.environ.get('LD_PRELOAD', '').split(':')
                                    if 'gtk4-layer-shell' not in p)

import gi  # noqa: E402
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk4LayerShell', '1.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_foreign('cairo')
import cairo  # noqa: E402
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yumi import chat, config, ears, files, jobs, memory, music, pixfont, pixui, system, tts, voice  # noqa: E402
from yumi import webpanel  # noqa: E402
from yumi.webpanel import catalog as webcatalog  # noqa: E402
from yumi.brain import UI_PX, Brain  # noqa: E402
from yumi.phrases import Speech  # noqa: E402
from yumi.sprites import Meta, palette_delta, palette_from_hue, recolor_bgra, ui_colors  # noqa: E402

APP_ID = 'io.github.lamex30.Yumi'
ACTIONS = ('toggle', 'pipette', 'home', 'recent', 'sleep', 'monitor', 'web', 'quit')
ASK = 'ask'          # the one action that carries a phrase («yumi --ask "какая погода"»)
FADE = 0.6          # s of crossfade when the wallpaper palette changes


# ---------------------------------------------------------------------------------------------
# sprites as cairo surfaces, recolored to the wallpaper

def load_surfaces(meta, delta):
    out = {}
    for key in meta.names:
        try:
            s = cairo.ImageSurface.create_from_png(str(meta.paths[key]))
        except (cairo.Error, MemoryError, OSError):
            continue
        if delta is not None:
            s.flush()
            recolor_bgra(s.get_data(), delta, keep=meta.keep)
            s.mark_dirty()
        out[key] = s
    return out


class Painter:
    """draws Brain ops with cairo; the interface is pixel art: rectangles from yumi.pixui and
    text from the built-in pixel font (yumi.pixfont), scaled up by whole interface pixels"""

    LINE = 11            # text line height in interface pixels

    def __init__(self, meta, scale):
        self.meta = meta
        self.S = scale
        self.P = UI_PX * scale
        self.surf = {}
        self.old = None
        self.old_meta = None
        self.fade_from = 0.0
        self.set_palette(None)
        self.set_meta(meta)
        self.msize = {}
        self.texts = {}
        self.thumbs = {}

    def set_meta(self, meta):
        self.meta = meta
        self.fw = {n: p['w'] for n, p in meta.rig['parts'].items()}
        self.fw['moth'] = meta.moth['w']
        self.fw.update(meta.fw)

    def measure(self, text, bold=False):
        return (pixfont.width(text) + 1) * self.P, self.LINE * self.P

    def set_palette(self, pal):
        self.col = ui_colors(pal)
        self.sh = pixui.shades(self.col)
        self.texts = {}

    def rgba(self, key, a=1.0):
        if key == 'ink':
            key = 'line'
        r, g, b = self.sh.get(key) or self.col.get(key, (1, 0, 1))
        return r, g, b, a

    def swap(self, surfaces, now, old_meta=None):
        if self.surf:
            self.old = self.surf
            self.old_meta = old_meta or self.meta
            self.fade_from = now
        self.surf = surfaces

    def old_ops(self, ops):
        """the sprite ops for the fading-out layer: an earlier outfit's hat sat elsewhere"""
        new = self.meta.fx.get('hat') or {}
        old = (self.old_meta or self.meta).fx.get('hat') or {}
        dx = (old.get('x', 0) - new.get('x', 0)) * self.S
        dy = (old.get('y', 0) - new.get('y', 0)) * self.S
        out = []
        for o in ops:
            if o[0] != 'spr':
                continue
            if o[1] in ('hat', 'hat:front') and (dx or dy):
                o = (o[0], o[1], o[2], o[3] + dx, o[4] + dy) + tuple(o[5:])
            out.append(o)
        return out

    def paint(self, cr, ops, now):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        fade = None
        if self.old is not None:
            t = (now - self.fade_from) / FADE
            if t >= 1:
                self.old = None
            else:
                fade = t
        self.draw_ops(cr, ops, self.surf)
        if fade is not None:
            cr.push_group()
            self.draw_ops(cr, self.old_ops(ops), self.old)
            cr.pop_group_to_source()
            cr.paint_with_alpha(1 - fade)

    def text_surface(self, text, color):
        """the text at 1 font pixel per pixel (built from the pixel font), cached"""
        key = (text, color)
        s = self.texts.get(key)
        if s is None:
            s = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixfont.width(text) + 1, self.LINE)
            c = cairo.Context(s)
            c.set_source_rgba(*self.rgba(color))
            for x, y, w in pixfont.runs(text):
                c.rectangle(x, y + 1, w, 1)
            c.fill()
            s.flush()
            self.texts[key] = s
            if len(self.texts) > 600:
                self.texts.pop(next(iter(self.texts)))
        return s

    def rects(self, cr, rects):
        for x, y, w, h, col, a in rects:
            if w > 0 and h > 0:
                cr.set_source_rgba(*self.rgba(col, a))
                cr.rectangle(x, y, w, h)
                cr.fill()

    def draw_ops(self, cr, ops, surf):
        S, P = self.S, self.P
        for op in ops:
            k = op[0]
            if k == 'spr':
                _, key, fr, x, y, alpha, angle, px, py = op
                s = surf.get(key)
                if s is None:
                    continue
                fw = self.fw.get(key, s.get_width())
                cr.save()
                cr.translate(round(x), round(y))
                if angle:
                    cr.translate(px * S, py * S)
                    cr.rotate(angle)
                    cr.translate(-px * S, -py * S)
                cr.scale(S, S)
                cr.rectangle(0, 0, fw, s.get_height())
                cr.clip()
                cr.set_source_surface(s, -fr * fw, 0)
                cr.get_source().set_filter(cairo.FILTER_NEAREST)
                if alpha < 1:
                    cr.paint_with_alpha(alpha)
                else:
                    cr.paint()
                cr.restore()
            elif k == 'group':                         # drawn whole, then see-through as one
                cr.push_group()
                self.draw_ops(cr, op[2], surf)
                cr.pop_group_to_source()
                cr.paint_with_alpha(op[1])
            elif k in ('panel', 'tail', 'switch', 'bar', 'icon', 'circle', 'hollow'):
                self.rects(cr, pixui.expand(op, P))
            elif k == 'rect':
                _, x, y, w, h, fill, a = op
                cr.set_source_rgba(*self.rgba(fill, a))
                cr.rectangle(round(x), round(y), round(w), round(h))
                cr.fill()
            elif k == 'text':
                _, x, y, text, color = op[:5]
                mult = op[5] if len(op) > 5 else 1
                s = self.text_surface(text, color)
                cr.save()
                cr.translate(int(round(x / P)) * P, int(round(y / P)) * P)
                cr.scale(P * mult, P * mult)
                cr.set_source_surface(s, 0, 0)
                cr.get_source().set_filter(cairo.FILTER_NEAREST)
                cr.paint()
                cr.restore()
            elif k == 'chip':
                _, x, y, w, h, hexc = op
                rgb = Gdk.RGBA()
                rgb.parse(hexc)
                cr.set_source_rgba(rgb.red, rgb.green, rgb.blue, 1)
                cr.rectangle(round(x) + P, round(y) + P, round(w) - 2 * P, round(h) - 2 * P)
                cr.fill()
                self.rects(cr, pixui.chip(x, y, w, h, P))
            elif k == 'thumb':
                _, path, x, y, w, h = op
                self.thumb(cr, path, x, y, w, h)

    def thumb(self, cr, path, x, y, w, h):
        key = (path, int(w), int(h))
        pb = self.thumbs.get(key)
        if pb is None and key not in self.thumbs:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, int(w), int(h), True)
            except GLib.Error:
                pb = None
            self.thumbs[key] = pb
            if len(self.thumbs) > 64:
                self.thumbs.pop(next(iter(self.thumbs)))
        if pb is None:
            return
        tx = round(x + (w - pb.get_width()) / 2)
        ty = round(y + (h - pb.get_height()) / 2)
        Gdk.cairo_set_source_pixbuf(cr, pb, tx, ty)
        cr.paint()


# ---------------------------------------------------------------------------------------------

class Overlay(Gtk.ApplicationWindow):
    """one transparent layer-shell window per screen; she lives on one of them (app.active), the
    others only show her while she is carried across their edge"""

    def __init__(self, app, monitor=None):
        super().__init__(application=app)
        self.app = app
        self.monitor = monitor
        geo = monitor.get_geometry() if monitor is not None else None
        self.ox, self.oy = (geo.x, geo.y) if geo is not None else (0, 0)
        self.mw, self.mh = (geo.width, geo.height) if geo is not None else (1920, 1080)
        self.name = (monitor.get_connector() if monitor is not None else None) or 'default'
        self.drew = False
        self.add_css_class('moth-girl')
        LayerShell.init_for_window(self)
        if monitor is not None:
            LayerShell.set_monitor(self, monitor)
        LayerShell.set_namespace(self, 'yumi')
        LayerShell.set_layer(self, LayerShell.Layer.TOP)          # under fullscreen windows
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT,
                     LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self, edge, True)
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self.draw)
        self.area.connect('resize', self.on_resize)
        self.set_child(self.area)
        self.last_region = None

        motion = Gtk.EventControllerMotion()
        motion.connect('motion', lambda _c, x, y: self.app.ev(lambda b: b.motion(x, y)))
        motion.connect('enter', lambda _c, x, y: self.app.ev(lambda b: b.motion(x, y)))
        motion.connect('leave', lambda _c: self.app.ev(lambda b: b.leave()))
        self.area.add_controller(motion)

        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect('pressed', self.on_pressed)
        click.connect('released', self.on_released)
        click.connect('cancel', lambda _g, _s: self.app.ev(self.cancel_press))
        self.area.add_controller(click)

        drag = Gtk.DragSource()
        drag.set_actions(Gdk.DragAction.COPY)
        drag.connect('prepare', self.on_drag_prepare)
        drag.connect('drag-begin', self.on_drag_begin)
        drag.connect('drag-end', self.on_drag_end)
        self.area.add_controller(drag)

        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL |
                                              Gtk.EventControllerScrollFlags.DISCRETE)
        wheel.connect('scroll', self.on_scroll)
        self.area.add_controller(wheel)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)   # before GTK takes Tab for its own focus moves
        keys.connect('key-pressed', self.on_key)
        self.add_controller(keys)
        self.keyboard = LayerShell.KeyboardMode.NONE

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('enter', self.on_dnd_motion)
        drop.connect('motion', self.on_dnd_motion)
        drop.connect('leave', lambda _t: self.app.ev(lambda b: b.dnd_leave()))
        drop.connect('drop', self.on_drop)
        self.area.add_controller(drop)
        self.connect('realize', lambda _w: self.update_input())

    # ---- input
    @staticmethod
    def cancel_press(b):
        b.press = None
        b.click_at = None
        b.ring_drag = None

    def on_pressed(self, g, n, x, y):
        button = g.get_current_button()
        self.app.ev(lambda b: b.press_at(button, x, y, n))

    def on_released(self, g, _n, x, y):
        button = g.get_current_button()
        if self.app.brain.mode == 'held':
            # carried to another screen? the pointer coordinates run on past this window's edge
            target = self.app.monitor_at(self.ox + x, self.oy + y)
            if target is not None and target is not self:
                gx, gy = self.ox + x, self.oy + y
                self.app.switch_to(target)
                self.app.ev(lambda b: b.release_at(button, gx - target.ox, gy - target.oy))
                return
        self.app.ev(lambda b: b.release_at(button, x, y))

    def on_scroll(self, _c, _dx, dy):
        used = self.app.brain.scroll(dy)
        self.app.kick()
        return used

    def on_key(self, _c, keyval, _code, state):
        name = Gdk.keyval_name(keyval) or ''
        command = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)
        if self.app.brain.form and command and name in ('v', 'V', 'Cyrillic_em', 'Cyrillic_EM'):
            self.app.form_paste()
            return True
        # shortcut: letters come straight from the layout, no input method (CJK, compose); add Gtk.IMContext if asked
        u = Gdk.keyval_to_unicode(keyval)
        used = self.app.brain.key(name, chr(u) if u >= 32 and u != 127 and not command else '')
        self.app.kick()
        return used

    def set_keyboard(self, mode):
        """the keyboard comes to her only while the pointer is over the timer or a button form is open"""
        if mode != self.keyboard:
            self.keyboard = mode
            LayerShell.set_keyboard_mode(self, mode)

    def on_drag_prepare(self, src, x, y):
        b = self.app.brain
        if b.mode == 'held':
            return None
        path = b.card_path_at(x, y)
        if not path:
            return None
        gfile = Gio.File.new_for_path(path)
        providers = []
        try:
            fl = Gdk.FileList.new_from_list([gfile])
            providers.append(Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, fl)))
        except (AttributeError, TypeError):
            pass
        uri = (gfile.get_uri() + '\r\n').encode()
        providers.append(Gdk.ContentProvider.new_for_bytes('text/uri-list', GLib.Bytes.new(uri)))
        providers.append(Gdk.ContentProvider.new_for_bytes('text/plain;charset=utf-8',
                                                           GLib.Bytes.new(path.encode())))
        if jobs.kind_of(path) == 'image':
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 96, 96, True)
                src.set_icon(Gdk.Texture.new_for_pixbuf(pb), 8, 8)
            except (GLib.Error, AttributeError, TypeError):
                pass
        self.dragging = path
        return Gdk.ContentProvider.new_union(providers)

    def on_drag_begin(self, _src, _drag):
        self.app.ev(lambda b: (setattr(b, 'file_drag', True), self.cancel_press(b)))

    def on_drag_end(self, _src, _drag, _delete):
        def done(b):
            b.file_drag = False
            b.say('drag_out')
        self.app.ev(done)

    def on_dnd_motion(self, _t, x, y):
        ok = self.app.brain.dnd_motion(x, y)
        self.app.kick()
        return Gdk.DragAction.COPY if ok else Gdk.DragAction(0)

    def on_drop(self, _t, value, x, y):
        try:
            paths = [f.get_path() for f in value.get_files()]
        except AttributeError:
            paths = []
        paths = [p for p in paths if p]
        ok = self.app.brain.dnd_drop(paths, x, y)
        self.app.kick()
        return ok

    def on_resize(self, _a, w, h):
        if self.app.active is self:
            self.app.brain.resize(w, h)
            self.app.kick()

    def update_input(self):
        surface = self.get_surface()
        if surface is None:
            return
        rects = self.app.brain.input_rects() if self.app.active is self else []
        key = tuple(sorted(rects))
        if key == self.last_region:
            return
        self.last_region = key
        region = cairo.Region()
        for x, y, w, h in rects:
            if w > 0 and h > 0:
                region.union(cairo.RectangleInt(x, y, w, h))
        surface.set_input_region(region)

    def draw(self, _area, cr, _w, _h):
        app = self.app
        act = app.active
        if act is self:
            ops = app.ops
        elif act is not None and app.brain.mode == 'held':
            ops = app.ops                          # being carried over: show her on this screen too
            cr.translate(act.ox - self.ox, act.oy - self.oy)
        else:
            ops = []
        self.drew = bool(ops)
        app.painter.paint(cr, ops, app.brain.now)


# GTK paints every window with the theme (USER priority); ours goes one above it.
# Not fully transparent (alpha 0.004 is invisible): GTK gives a fully transparent window
# no pointer input.
CSS = '''
window.moth-girl, window.moth-girl.background, window.moth-girl.csd {
    background: rgba(0, 0, 0, 0.004);
    background-image: none;
    box-shadow: none;
    border: none;
    border-radius: 0;
    outline: none;
}
window.moth-girl drawingarea { background: none; }
'''

class App(Gtk.Application):
    DUCK_LEVEL = 30                 # percent, while the ears wait for the phrase after her name
    DUCK_BACK_MS = 12000            # the loudness comes back this long after the name went by

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.windows = []
        self.active = None
        self.sound_src = None
        self.voice_proc = None          # the player saying her current line
        self.failed = False             # this desktop cannot show her
        self.voice_id = None            # which bubble it belongs to
        self.load_gen = 0               # sprite reloads: only the newest one is used
        self.outfit_want = None         # the outfit she wears or is changing into
        self.sound_procs = []
        self.pending = []
        self.duck_saved = None          # the loudness before the duck (the name_duck setting)
        self.duck_restore = None        # the timer that brings the loudness back
        self.form_picking = False       # the file dialog of the button form is open
        self.started = time.time()
        self.web = None                 # the optional browser panel (off by default)
        self.web_sig = None             # (enabled, host, port, session hours) it was started with
        self.web_sync_src = None
        self.web_opened = False
        self._web_music_at = 0.0
        self._web_music = None
        self._web_volume = None
        self._web_bubble = ''
        self._web_status_at = 0.0
        self.web_chat_path = config.STATE_DIR / 'web-chat.json'
        self.web_chat_lock = threading.Lock()
        self.web_chat = self.load_web_chat()
        ask_type = GLib.VariantType.new('s')
        for name in ACTIONS + (ASK,):
            a = Gio.SimpleAction.new(name, ask_type if name == ASK else None)
            a.connect('activate', self.on_action, name)
            self.add_action(a)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.settings = config.load_settings()
        self.state = config.load_state()
        system.set_mute_hook(self.self_mute)   # her ears close while her voice plays
        scale = int(os.environ.get('YUMI_SCALE') or self.settings.get('scale') or 1)
        self.world = system.World({
            'charger': lambda on: self.ev(lambda b: b.world('charger_on' if on else 'charger_off')),
            'battery_low': lambda pct: self.ev(lambda b: b.world('battery_low', pct=pct)),
            'wifi': lambda ssid: self.ev(lambda b: b.world('wifi_on', ssid=ssid) if ssid else b.world('wifi_off')),
            'dnd': lambda on: self.ev(lambda b: b.set_sleep(on)),
            'music': lambda on: self.ev(lambda b: b.world('music', playing=on)),
            'beat': lambda v: self.ev(lambda b: b.world('beat', level=v)),
            'idle': lambda kind: self.ev(lambda b: b.idle_event(kind)),
            'palette': self.on_palette,
            'caps': self.on_caps,
        })
        self.meta = Meta(config.SPRITES, self.pick_outfit())
        self.outfit_want = self.meta.outfit
        self.painter = Painter(self.meta, scale)
        self.palette = self.effective_palette()
        self.painter.set_palette(self.palette)
        self.painter.surf = load_surfaces(self.meta, palette_delta(self.palette))
        self.speech = Speech(self.settings)
        self.recent = files.Recent(changed=self.kick,
                                   download=lambda p: self.ev(lambda b: b.world('download', file=p.name)),
                                   screenshot=lambda p: self.ev(lambda b: b.world('screenshot', file=p.name)))
        self.brain = Brain(self.meta, self.settings, self.state, self.speech, self.painter.measure,
                           scale=scale, now=time.monotonic(), recent=self.recent.list)
        self.brain.outfit_names = Meta.outfits(config.SPRITES)
        self.wake_on = False             # the wake-word loop is running or not
        self.wake_thread = None
        self.wake_set(bool(self.settings.get('wake_ears')))   # «Юми» works out of the box
        self.tap_set(bool(self.settings.get('tap_ears')))     # the diary, too, if it was asked
        music.apply_settings(self.settings)   # her mpd, or the server's (music_server)
        threading.Thread(target=music.service_start, daemon=True).start()   # mpd lives with her
        if not self.settings.get('sassy'):
            # a crash may have kept the brat's lines after the mode went off:
            # the diary is swept at every start while the mode is off
            threading.Thread(target=memory.clear_tag,
                             args=(memory.TAG_SASSY,), daemon=True).start()
        self.fetch_chat_models()        # her answer model for the settings, in the background
        self.on_caps(initial=True)
        GLib.timeout_add_seconds(3600, lambda: self.apply_outfit() or True)    # the calendar moves on
        self.queue = jobs.Queue(self.job_progress, self.job_done, self.job_start)
        self.ops = []
        self.timer = None
        self.dl_announced = {}          # the model a daemon fetches now: src -> name, told about
        GLib.timeout_add_seconds(4, self.watch_downloads)
        self.hold()                                  # keep running without a mapped window too
        self.web_sync()                              # the browser panel, only if switched on
        GLib.timeout_add(1500, lambda: self.ev(lambda b: b.say('hello', force=True)) and False)

    def pick_outfit(self):
        """the outfit folder to wear now: by the calendar, or the one chosen in the settings"""
        want = self.settings.get('outfit', 'auto')
        name = config.outfit_for() if want == 'auto' else None if want == 'off' else want
        return name if name in Meta.outfits(config.SPRITES) else None

    def apply_outfit(self):
        name = self.pick_outfit()
        if name == self.outfit_want:
            return
        self.outfit_want = name
        self.apply_palette(self.effective_palette(), Meta(config.SPRITES, name))
        self.ev(lambda b: b.set_face('happy', 2.5))

    def close(self):
        """let all her companion services go before she does: the TTS daemon,
        the STT daemon, her music and anything else that outlives her process."""
        self.wake_on = False            # the wake loop stops asking: no ears rise after her
        if self.web:
            self.web.stop()
            self.web = None
            self.web_sig = None
        self.duck_end()                 # the ducked loudness comes back before she goes
        if self.duck_restore:
            GLib.source_remove(self.duck_restore)
            self.duck_restore = None
        tts.close()
        ears.close()
        self.stop_timer_sound()
        try:
            music.service_stop()
        except Exception:
            pass

    def watch_downloads(self):
        """her servants fetch their models on their own (vosk, whisper, silero voices):
        every few seconds she asks them what they pull, so a long quiet download never
        looks like she froze — a bubble and a word when a model's download begins,
        and one more when it lands; the models' first download is the only one told"""
        threading.Thread(target=self.watch_downloads_once, daemon=True).start()
        return True                     # the timer keeps going

    def watch_downloads_once(self):
        """runs off the interface thread: each daemon's answer is a name, '' for
        «alive, fetching nothing» or None for «not running»; a fresh name is told,
        a vanished one is congratulated (only while its daemon is still alive)"""
        now = {}
        for src, state in (('ears', ears.downloading()), ('tts', tts.downloading())):
            if state is not None:       # the daemon answers: it is alive
                now[src] = state
        for src, name in now.items():
            if name and src not in self.dl_announced:
                self.dl_announced[src] = name
                self.ev(lambda b, n=name: b.world('model_dl', model=n))
        for src, name in list(self.dl_announced.items()):
            if src not in now:          # the daemon itself went away: no «ready» words
                del self.dl_announced[src]
            elif now[src] != name:      # the download finished while it still runs
                del self.dl_announced[src]
                self.ev(lambda b, n=name: b.world('model_dl_done', model=n))

    def do_activate(self):
        if not LayerShell.is_supported():
            sys.stderr.write('yumi: this desktop has no wlr-layer-shell (GNOME and X11 are not '
                             'supported yet). She lives on sway, Hyprland, niri, river, Wayfire, '
                             'labwc, KDE Plasma, COSMIC...\n')
            self.failed = True
            self.close()
            self.quit()
            return
        if not self.windows:
            self.sync_windows()
            self.kick()

    # ---- screens
    @staticmethod
    def list_monitors():
        try:
            model = Gdk.Display.get_default().get_monitors()
            return [model.get_item(i) for i in range(model.get_n_items())]
        except Exception:
            return []

    def sync_windows(self):
        """a window per screen (kept in step when screens come and go)"""
        mons = self.list_monitors()
        names = [(m.get_connector() or str(i)) for i, m in enumerate(mons)]
        old = {w.name: w for w in self.windows}
        wins = []
        if not mons:
            wins = self.windows[:1] or [Overlay(self)]
        else:
            for name, mon in zip(names, mons):
                w = old.pop(name, None)
                if w is None:
                    w = Overlay(self, mon)
                wins.append(w)
        for w in old.values():                      # screens that went away
            w.destroy()
        self.windows = wins
        want = self.state.get('monitor')
        target = next((w for w in wins if w.name == want), None) or wins[0]
        if self.active not in wins:
            self.active = None
        if self.active is None:
            self.switch_to(target, remember=False)
        self.brain.monitors = len(wins)
        for w in wins:
            w.present()
        if not getattr(self, '_mon_watch', False) and mons:
            self._mon_watch = True
            try:
                Gdk.Display.get_default().get_monitors().connect(
                    'items-changed', lambda *_a: GLib.idle_add(lambda: self.sync_windows() and False))
            except Exception:
                pass

    def monitor_at(self, gx, gy):
        for w in self.windows:
            if w.ox <= gx < w.ox + w.mw and w.oy <= gy < w.oy + w.mh:
                return w
        return None

    def switch_to(self, win, remember=True):
        """she lives on this screen from now on"""
        if win is self.active:
            return
        self.active = win
        if remember:
            self.state['monitor'] = win.name
            self.state.save()
        w, h = win.area.get_width(), win.area.get_height()
        ok = isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0
        self.brain.resize(w if ok else win.mw, h if ok else win.mh)
        for other in self.windows:
            other.last_region = None
            other.update_input()
            other.area.queue_draw()

    def cycle_monitor(self):
        if len(self.windows) < 2:
            return
        i = self.windows.index(self.active) if self.active in self.windows else -1
        self.switch_to(self.windows[(i + 1) % len(self.windows)])
        self.ev(lambda b: b.go_home())

    def on_action(self, _a, param, name):
        self.do_activate()
        b = self.brain
        if name == ASK:
            # the phrase goes in where the mic's words go: she wakes, says it back
            # and answers with her voice, as if it had been heard in the room
            phrase = ' '.join(str(param.get_string() if param is not None else '').split())
            if phrase:
                self.ev(lambda b: b.wake_up(phrase))
            return
        if name == 'toggle':
            self.ev(lambda b: b.toggle())
        elif name == 'pipette':
            self.ev(lambda b: (b.pop(True) if b.mode == 'docked' and not b.sleeping else None,
                               b.start_pipette()))
        elif name == 'home':
            self.ev(lambda b: b.go_home())
        elif name == 'monitor':
            self.cycle_monitor()
        elif name == 'recent':
            self.ev(lambda b: b.toggle_pile('recent'))
        elif name == 'sleep':
            self.ev(lambda b: b.requests.append(('dnd', not b.sleeping)))
        elif name == 'web':
            self.web_open()
        elif name == 'quit':
            self.close()
            self.quit()

    # ---- the loop: every event and every frame goes through here
    def ev(self, fn=None):
        if fn:
            fn(self.brain)
        self.kick()

    def kick(self):
        """run a frame now and make sure the timer keeps running at the brain's pace"""
        self.frame()

    def frame(self):
        b = self.brain
        b.update(time.monotonic())
        self.handle_requests()
        if self.voice_proc and (not b.bubble or b.bubble.get('id') != self.voice_id):
            self.hush()                              # the bubble went away: so does her voice
        self.ops = b.scene()
        self.web_tick()
        for w in self.windows:
            if w is self.active:
                w.area.queue_draw()
                w.update_input()
                w.set_keyboard(LayerShell.KeyboardMode.EXCLUSIVE if b.form and not self.form_picking else
                               LayerShell.KeyboardMode.ON_DEMAND if b.kb_hover else LayerShell.KeyboardMode.NONE)
            elif b.mode == 'held' or w.drew:
                w.area.queue_draw()                 # carried across / one last clear
        fps = b.fps()
        if self.painter.old is not None:
            fps = 60
        interval = int(1000 / fps)
        if self.timer is None or self.timer[1] != interval:
            if self.timer:
                GLib.source_remove(self.timer[0])
            self.timer = (GLib.timeout_add(interval, self.on_timer), interval)
        return False

    def on_timer(self):
        self.timer_fired = True
        old = self.timer
        self.frame()
        # frame() may have replaced the timer; keep this one only if it is still current
        return self.timer is old

    # ---- requests from the brain
    def handle_requests(self):
        b = self.brain
        while b.requests:
            r = b.requests.pop(0)
            kind = r[0]
            if kind == 'open':
                files.open_path(r[1])
            elif kind == 'job':
                self.queue.add(r[1], r[2], r[3])
            elif kind == 'pipette':
                system.pick_color(lambda hexc: self.ev(lambda b: b.pipette_done(hexc)),
                                  (self.effective_palette() or {}).get('accent'))
            elif kind == 'dnd':
                if not r[1]:
                    b.nap = False          # you asked her to wake: the nap guard must not ignore it
                self.world.set_dnd(r[1])
                b.set_sleep(r[1])          # the notification daemon will confirm; act now
            elif kind == 'copy':
                if not system.copy_text(r[1]):
                    Gdk.Display.get_default().get_clipboard().set(r[1])
            elif kind == 'run':
                system.spawn('sh', '-c', r[1])
            elif kind == 'url':
                system.open_url(r[1])
            elif kind == 'monitor_next':
                self.cycle_monitor()
            elif kind == 'outfit':
                self.apply_outfit()
            elif kind == 'sound':
                self.play_timer_sound()
            elif kind == 'sound_stop':
                self.stop_timer_sound()
            elif kind == 'voice':
                self.duck_end()     # her answer sounds at your loudness, not the ducked one
                self.speak(r[1], r[2], r[3], r[4] if len(r) > 4 else 0.5, r[5] if len(r) > 5 else '')
            elif kind == 'listen':
                self.listen_ears(r[1])
            elif kind == 'listen_stop':
                threading.Thread(target=ears.stop, daemon=True).start()
            elif kind == 'wake_set':
                self.wake_set(r[1])
            elif kind == 'tap_set':
                self.tap_set(r[1])
            elif kind == 'chat_ask':
                self.chat_ask(r[1])
            elif kind == 'weather_ask':
                self.weather_ask(r[1])
            elif kind == 'hourly_ask':
                self.hourly_ask(r[1])
            elif kind == 'joke_ask':
                self.joke_ask(r[1])
            elif kind == 'chat_models':
                self.fetch_chat_models()
            elif kind == 'mics':
                self.fetch_mics()
            elif kind == 'music_control':
                cmd = r[1]
                if cmd.startswith('volume'):
                    self.duck_keep()    # the level you asked for is the one that stays
                try:
                    music.music_control(cmd)
                except Exception:
                    pass
            elif kind == 'music_who':
                self.music_who()
            elif kind == 'music_search':
                self.music_search(r[1])
            elif kind == 'stop_all':
                self.hush()                    # her voice dies mid-word
                self.stop_timer_sound()
                try:
                    music.music_control('stop')
                except Exception:
                    pass
            elif kind == 'pick_sound':
                self.pick_sound()
            elif kind == 'form_pick':
                self.form_pick()
            elif kind == 'save_actions':
                config.save_actions(b.custom)
            elif kind == 'save':
                self.state.save()
            elif kind == 'save_settings':
                self.settings.save()
                music.apply_settings(self.settings)   # the server mpd may have moved
                self.web_sync_soon()                  # on / off / another port, live
            elif kind == 'web_open':
                self.web_open()
            elif kind == 'web_copy':
                self.web_copy()
            elif kind == 'web_rotate':
                self.web_rotate()
            elif kind == 'memory_clear_sassy':
                # «Дерзкая 18+» went off: its talks and jokes leave the diary,
                # vectors and all (yumi/memory.py, tag «sassy…»)
                threading.Thread(target=memory.clear_tag,
                                 args=(memory.TAG_SASSY,), daemon=True).start()
            elif kind == 'recolor':
                self.apply_palette(self.effective_palette())
            elif kind == 'quit':
                self.state.save()
                self.close()
                self.quit()
            elif kind == 'restart':
                self.state.save()
                self.close()
                self.restart()

    def restart(self):
        """start a fresh Yumi a moment from now (the app id is single-instance, so after we are gone)"""
        here = Path(__file__).resolve().parent
        exe = next((str(p) for p in (here / 'bin' / 'yumi', here.parent.parent / 'bin' / 'yumi')
                    if p.exists()), shutil.which('yumi'))
        if exe:
            subprocess.Popen(['sh', '-c', 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; exec "$0"',
                              exe, str(os.getpid())], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self.close()
        self.quit()

    # ---- timer sound: her 8-bit chime, or a file chosen in the settings
    def play_timer_sound(self):
        self.stop_timer_sound()
        own = self.settings.get('timer_sound')
        path = own if own and Path(own).exists() else config.SOUNDS / 'timer.wav'
        self.sound_procs = [system.play_file(path)]
        # twice, so it is hard to miss: the second one starts when the first has ended
        gap = 2.0
        try:
            import wave
            with wave.open(str(path)) as w:
                gap = w.getnframes() / w.getframerate() + 0.35
        except Exception:
            pass

        def again():
            self.sound_src = None
            self.sound_procs.append(system.play_file(path))
            return False
        self.sound_src = GLib.timeout_add(int(min(gap, 8.0) * 1000), again)

    # ---- her voice: 8-bit blips, or a real one from a local Silero (yumi/tts.py)
    def speak(self, blips, bid, end_pitch, volume=0.5, text=''):
        self.hush()
        self.voice_id = bid
        if not blips and not text:
            return
        path = config.CACHE_DIR / f'voice{bid % 3}.wav'
        real = bool(text) and self.settings.get('voice_kind') == 'tts'

        def work():
            p = tts.speak(text, volume) if real else None
            if real and not p:
                self.warn_tts()               # not set up or failed: the blips speak instead
            if not p:
                try:
                    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    voice.render(blips, path, seed=bid, end_pitch=end_pitch, volume=volume)
                except OSError:
                    return
                p = path
            GLib.idle_add(lambda: self.voice_start(p, bid) and False)
        threading.Thread(target=work, daemon=True).start()

    def warn_tts(self):
        """once, into the log: why the real voice did not happen"""
        first = not getattr(self, '_tts_warned', False)
        self._tts_warned = True
        if first:
            print('yumi: her real voice is not set up (run tools/tts_setup.sh); '
                  'the 8-bit blips speak instead', file=sys.stderr)
        return first

    def voice_start(self, path, bid):
        b = self.brain
        if bid == self.voice_id and b.bubble and b.bubble.get('id') == bid:
            secs = tts.wav_len(path)
            if secs:                          # the voice may be longer than the typed words
                b.bubble['until'] = max(b.bubble['until'], b.now + secs + 1.2)
            self.voice_proc = system.play_file(path)

    def hush(self):
        p, self.voice_proc = self.voice_proc, None
        try:
            if p:
                p.terminate()
        except (OSError, AttributeError):
            pass

    # ---- her ears and her mind: a local Vosk hears (yumi/ears.py), a local Ollama answers (yumi/chat.py)
    def listen_ears(self, secs):
        """one listen: the daemon records the mic and recognizes it, in its own thread.
        The mic opens only when she is done talking: her own line must not be heard"""
        def work():
            b = self.brain
            for _ in range(80):             # at most ~8 s: her line is not that long
                if not b.listening:
                    return                  # clicked again while she was talking: called off
                if b.now >= b.talk_until and not self.talking():
                    break
                time.sleep(0.1)
            if not b.listening:
                return
            self.hush()                     # still sounding after the wait: she gives way to you
            ears.unmute()                   # her voice is gone: the ears open for you at once
            res = ears.listen(lang=self.settings.get('speech_lang', 'en'), secs=secs,
                              size=self.settings.get('stt_size', 'small'),
                              mic=self.settings.get('stt_mic'),
                              engine=self.settings.get('stt_engine'))
            text = res.get('text') if isinstance(res, dict) else res
            vtxt = res.get('vosk', '') if isinstance(res, dict) else ''
            GLib.idle_add(lambda t=text, v=vtxt: self.ev(lambda b: b.heard(t, v)) and False)
        threading.Thread(target=work, daemon=True).start()

    def talking(self):
        """her voice is sounding at this very moment"""
        p = self.voice_proc
        return bool(p) and p.poll() is None

    def duck_begin(self):
        """her name was heard: the sounds duck so the ears hear the phrase.
        The loudness before the duck is remembered and comes back in a while,
        unless you set the volume yourself — then the new level stays"""
        if not self.settings.get('name_duck'):
            return
        if self.duck_restore:
            GLib.source_remove(self.duck_restore)
        self.duck_restore = GLib.timeout_add(self.DUCK_BACK_MS, self.duck_end)
        if self.duck_saved is not None:
            return                      # already low: the first loudness is the one to keep
        try:
            saved = music.read_volume()
            if saved is None:
                print('yumi: the duck could not read the volume, it stays as it is')
                return
            self.duck_saved = saved
            music.system_volume(f'volume {min(saved, self.DUCK_LEVEL)}')
        except Exception:
            self.duck_saved = None

    def duck_keep(self):
        """a volume command went through: the level she just named is the one that stays"""
        if self.duck_restore:
            GLib.source_remove(self.duck_restore)
        self.duck_restore = None
        self.duck_saved = None

    def duck_end(self):
        """the phrase is over: the loudness you had comes back"""
        self.duck_restore = None
        saved, self.duck_saved = self.duck_saved, None
        if saved is not None:
            try:
                music.system_volume(f'volume {saved}')
            except Exception:
                pass
        return False

    def self_mute(self, secs):
        """her ears close while she speaks (system.play_file keeps the mute alive only
        while the sound plays): a click-listen waits her out anyway, and the wake's
        recorder drops the wake logic until the window runs out — she cannot wake to
        her own words, with or without the echo canceller; a shout of «стоп» gets
        through (the daemon judges the phrases it hears for a stop and nothing else)
        (stt_selfmute)"""
        if not self.settings.get('stt_selfmute', True):
            return
        threading.Thread(target=ears.mute, args=(secs,), daemon=True).start()

    def wake_set(self, on):
        """the wake word: while it is on, a loop keeps the daemon listening for her name
        and, the moment it passes by, the phrase that follows goes straight to her mind"""
        self.wake_on = bool(on)
        if not self.wake_on:
            if self.wake_thread:            # a /wake may still be waiting: let it off the mic
                threading.Thread(target=ears.stop, daemon=True).start()
            return
        if self.wake_thread:
            return

        def work():
            b = self.brain
            while self.wake_on:
                time.sleep(0.15)
                if b.listening:
                    continue            # the mic is lent to a click-listen: not ours now
                res = ears.wake(lang=self.settings.get('speech_lang', 'en'),
                                word=self.settings.get('wake_word', 'юми'), secs=120,
                                size=self.settings.get('stt_size', 'small'),
                                mic=self.settings.get('stt_mic'),
                                sensitivity=self.settings.get('wake_sensitivity', 'normal'),
                                confirm=self.settings.get('wake_confirm', True),
                                model_size=self.settings.get('wake_model_size', 'small'),
                                judge_log=self.settings.get('wake_judge_log', True),
                                engine=self.settings.get('stt_engine'),
                                oww_ear=self.settings.get('wake_ear', 'auto'))
                if not self.wake_on or not res:
                    continue            # called off or the ears are away
                if res.get('stop'):
                    # «стоп» overrides everything: even mid-word, asleep or thinking
                    GLib.idle_add(lambda: self.ev(lambda b: b.stop_all()) and False)
                    continue
                if not res.get('woke'):
                    continue            # quiet: nobody called her name
                tail = res.get('text') or ''
                alt = res.get('alt') or ''  # the stop ear's reading, the other ear
                GLib.idle_add(self.duck_begin)   # her name passed: quiet now, whatever comes
                if b.thinking or b.listening:
                    continue            # she got busy in those seconds: the word is spent
                # the name was heard: the moth that marks where she hides becomes the
                # girl — asleep she wakes, hidden she comes out, right now, while the
                # phrase (or the wait for it) is still going
                GLib.idle_add(lambda: self.ev(lambda br: br.wake_form()) and False)
                if b.now < b.talk_until or self.talking():
                    self.hush()         # her own line was still sounding: it dies, your turn now
                if tail:
                    # name + text already recognised: pass the whole phrase straight to the brain
                    GLib.idle_add(lambda t=tail, a=alt: self.ev(lambda br: br.wake_up(t, a)) and False)
                else:
                    # «юми» без текста: ухо openWakeWord только имя ловит,
                    # команда за ней теряется — слушаем фразу, которая пошла после имени
                    cmd = ears.listen(lang=self.settings.get('speech_lang', 'ru'),
                                      secs=15,
                                      size=self.settings.get('stt_size', 'small'),
                                      mic=self.settings.get('stt_mic'),
                                      engine=self.settings.get('stt_engine'))
                    if cmd:
                        said = cmd.get('text') or ''
                        vtxt = cmd.get('vosk') or ''   # the other reading, as on the click-listen
                        if said:
                            GLib.idle_add(lambda t=said, v=vtxt: self.ev(lambda br: br.wake_up(t, v)) and False)
                        else:
                            # silence after the name: she asks what you wanted
                            GLib.idle_add(lambda: self.ev(lambda br: br.wake_up('')) and False)
                    else:
                        # ears unavailable: fall back to asking
                        GLib.idle_add(lambda: self.ev(lambda br: br.wake_up('')) and False)

        self.wake_thread = threading.Thread(target=work, daemon=True)
        self.wake_thread.start()

    def tap_set(self, on):
        """the ears' diary: while it is on, the daemon records everything the ears hear
        into /tmp/yumi (the sound and the words), in its own thread beside her work"""
        def work():
            ears.tap(bool(on), lang=self.settings.get('speech_lang', 'en'),
                     size=self.settings.get('stt_size', 'small'),
                     mic=self.settings.get('stt_mic'))
        threading.Thread(target=work, daemon=True).start()

    def chat_ask(self, history):
        """the question is away to Ollama; the answer comes back into her bubble"""
        def work():
            s = self.settings
            lang = s.get('speech_lang', 'en')
            # the copy the worker walks: `history` is the caller's list, and an
            # assignment to it here would make every read of it a crash
            # (UnboundLocalError — every question died before the answer)
            hist = list(history)
            user_text = str(hist[-1].get('content') or '')   # the question just asked
            # the diary: older lines about the same thing join the question, the
            # talks about other things stay out of the way (yumi/memory.py)
            if s.get('chat_memory', True):
                try:
                    note = chat.recalled_note(
                        memory.recall(user_text, s, skip_recent=len(hist)), lang)
                except Exception:
                    note = ''
                if note:
                    hist.append({'role': 'system', 'content': note})
            sassy = bool(s.get('sassy'))
            prompt = chat.system_prompt(lang, sassy)
            reply = chat.ask(s, [{'role': 'system', 'content': prompt}] + hist)
            # the «Дерзкая 18+» lines carry their tag too: switching the mode
            # off wipes them from the diary together with the jokes. A switch
            # made while the answer was on its way leaves the diary clean
            tag = memory.TAG_SASSY if sassy else None
            if s.get('chat_memory', True) and bool(s.get('sassy')) == sassy:
                try:
                    memory.remember('user', user_text, s, tag=tag)
                    if reply:
                        memory.remember('assistant', reply, s, tag=tag)
                except Exception:
                    pass
            GLib.idle_add(lambda: self.ev(lambda b: b.replied(reply)) and False)
        threading.Thread(target=work, daemon=True).start()

    def weather_ask(self, ask):
        """the weather question is away to the clouds; the peek comes back into her bubble"""
        def work():
            lang = self.settings.get('speech_lang', 'en')
            reply = chat.weather_report(ask.get('where', ''), lang, ask.get('when', 'today'))
            GLib.idle_add(lambda: self.ev(lambda b: b.said_weather(reply)) and False)
        threading.Thread(target=work, daemon=True).start()

    def music_who(self):
        """the «кто играет» question: the player is asked in its own thread
        (playerctl and mpc take a moment), the names go back into her bubble"""
        def work():
            playing = music.now_playing()
            GLib.idle_add(lambda: self.ev(lambda b: b.said_now_playing(playing)) and False)
        threading.Thread(target=work, daemon=True).start()

    def music_search(self, payload):
        """«включи песню ...»: the libraries are asked away from her main thread
        (a search walks the network and her folders, then the queue is built),
        the report goes back into her bubble"""
        def work():
            music.apply_settings(self.settings)
            report = music.play_query(payload.get('kind', 'song'),
                                      payload.get('query', ''), self.settings)
            GLib.idle_add(lambda: self.ev(lambda b: b.said_music_found(report)) and False)
        threading.Thread(target=work, daemon=True).start()

    def hourly_ask(self, payload):
        """her mind is asked for a funny line about this hour; the reply goes back to the brain"""
        def work():
            lang = payload.get('lang', 'en')
            hour = int(payload.get('hour', time.localtime().tm_hour))
            joke = chat.hourly_joke(self.settings, hour, lang)
            GLib.idle_add(lambda: self.ev(lambda b: b.hour_joked({'joke': joke})) and False)
        threading.Thread(target=work, daemon=True).start()

    def joke_ask(self, payload):
        """her mind is asked for a joke; the request and the joke go into the
        diary tagged (the vectors too), and in «Дерзкая 18+» the fourth one in a
        row gets a model-written brush-off instead"""
        def work():
            s = self.settings
            lang = payload.get('lang', 'en')
            sassy = bool(s.get('sassy'))
            tag = memory.TAG_SASSY_JOKE if sassy else memory.TAG_JOKE
            asked = str(payload.get('text') or '').strip() or (
                'анекдот' if str(lang) == 'ru' else 'a joke')
            # how many jokes in a row this one makes: the diary holds the ones
            # already asked, the current request has not been written down yet
            try:
                streak = memory.joke_streak(tag) + 1
            except Exception:
                streak = 1
            tired = sassy and streak >= chat.JOKE_TIRED_AFTER
            reply = chat.joke_tired(s, lang, streak) if tired else chat.joke(s, lang)
            GLib.idle_add(lambda: self.ev(lambda b: b.joked(reply, tired)) and False)
            # the request and the joke go into the diary tagged, with their
            # vectors (the jokes are written even when the resting chats are
            # not: the run is counted from them, and the mode's lines must be
            # found and wiped when «Дерзкая 18+» goes off)
            if bool(s.get('sassy')) != sassy:
                return                  # the mode changed mid-joke: leave the diary clean
            try:
                memory.remember('user', asked, s, tag=tag)
                if reply:
                    memory.remember('assistant', reply, s, tag=tag)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def fetch_chat_models(self):
        """the installed Ollama models, for Settings → Her mind → Answer model"""
        def work():
            names = chat.models(self.settings)
            GLib.idle_add(lambda: self.ev(lambda b: b.chat_models_loaded(names)) and False)
        threading.Thread(target=work, daemon=True).start()

    def fetch_mics(self):
        """the microphones her ears can hear, for Settings → Her ears → Microphone"""
        def work():
            mics = ears.mics()
            GLib.idle_add(lambda: self.ev(lambda b: b.mics_loaded(mics)) and False)
        threading.Thread(target=work, daemon=True).start()

    def stop_timer_sound(self):
        """Stop pressed: no second chime, and the one playing is cut off"""
        src = getattr(self, 'sound_src', None)
        if src:
            GLib.source_remove(src)
            self.sound_src = None
        for p in getattr(self, 'sound_procs', []):
            try:
                if p:
                    p.terminate()
            except (OSError, AttributeError):
                pass
        self.sound_procs = []

    def pick_sound(self):
        dialog = Gtk.FileDialog()
        dialog.set_title(config.tr(self.settings, 'timer_sound'))
        flt = Gtk.FileFilter()
        flt.set_name(config.tr(self.settings, 'audio_files'))
        flt.add_mime_type('audio/*')
        for ext in ('wav', 'ogg', 'oga', 'mp3', 'flac', 'opus'):
            flt.add_suffix(ext)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dialog.set_filters(store)
        dialog.set_default_filter(flt)

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return                                   # cancelled
            if f and f.get_path():
                self.settings['timer_sound'] = f.get_path()
                self.settings.save()
                self.play_timer_sound()
                self.ev(lambda b: b.open_menu('set_sound', None) if b.menu else None)
        dialog.open(None, None, done)

    # ---- a picture for a button: the system's file dialog, like the timer sound
    def form_pick(self):
        self.form_picking = True               # the dialog needs the keyboard she holds
        flt = Gtk.FileFilter()
        flt.set_name(config.tr(self.settings, 'images'))
        flt.add_pixbuf_formats()
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)

        def done(dlg, res):
            self.form_picking = False
            try:
                path = dlg.open_finish(res).get_path()
                GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 32, 32, True)
            except GLib.Error as e:            # cancelled, or not a picture she can read
                bad = not e.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED)
                self.ev(lambda b: b.set_face('worried', 1.5) if bad else None)
                return
            except TypeError:                  # not a local file
                self.ev(lambda b: b.set_face('worried', 1.5))
                return
            icon = config.keep_icon(path)
            self.ev(lambda b: b.form.update(icon=icon) if b.form else None)
        Gtk.FileDialog(title=config.tr(self.settings, 'ring_icon'), filters=store, default_filter=flt).open(None, None, done)

    def form_paste(self):
        def done(cb, res):
            try:
                text = cb.read_text_finish(res)
            except GLib.Error:
                return
            if text:
                self.ev(lambda b: b.form_text(text) if b.form else None)
        Gdk.Display.get_default().get_clipboard().read_text_async(None, done)

    # ---- jobs
    def job_label(self):
        return config.tr(self.settings, 'working')

    def job_start(self, job):
        self.ev(lambda b: b.job_progress(0.0, self.job_label()))

    def job_progress(self, job):
        self.ev(lambda b: b.job_progress(job.fraction, self.job_label()))

    def job_done(self, job, ok, msg):
        saved = None
        out = job.out
        if ok and out and out.exists():
            after = out.stat().st_size
            if job.op == 'compress':
                saved = jobs.human_saved(job.size_before, after)
                if not saved:                      # bigger or equal: not worth keeping
                    try:
                        out.unlink()
                    except OSError:
                        pass
                    out = None
            if out:
                self.recent.add(out)
        elif ok and msg == 'nogain':
            saved = None
        last = not self.queue.jobs
        if not ok or last:
            self.ev(lambda b: b.job_done(ok, job.op, out=out, saved=saved, fmt=job.arg, msg=msg))
        if not last:
            self.ev(lambda b: b.job_progress(0.0, self.job_label()))

    # ---- colors: the wallpaper's, or one picked by hand in the settings
    def effective_palette(self):
        if self.settings.get('recolor', True):
            if self.world.wall_palette:
                return self.world.wall_palette
        return palette_from_hue(self.settings.get('hue'))

    def on_palette(self, pal):
        if self.settings.get('recolor', True):
            self.apply_palette(pal)

    def on_caps(self, initial=False):
        """what this desktop lets her do; the settings show the rest as not available yet"""
        off = set()
        if self.world.wall is None:
            off.add('recolor')
        if not self.world.can_idle:
            off.add('idle_nap')
        changed = off != self.brain.unavailable
        self.brain.unavailable = off
        if changed and not initial:
            self.apply_palette(self.effective_palette())
            self.kick()

    def apply_palette(self, pal, meta=None):
        """reload every sprite (recolored to `pal`) in the background; with `meta` (another
        outfit) she changes into it only once its sprites are ready, so nothing jumps"""
        self.palette = pal
        delta = palette_delta(pal)
        meta = meta or self.meta
        self.load_gen += 1
        gen = self.load_gen

        def done(surfaces):
            if gen != self.load_gen:          # a newer load is on its way
                return False
            old_meta = self.meta
            if meta is not self.meta:
                self.meta = meta
                self.brain.meta = meta
                self.painter.set_meta(meta)
            self.painter.set_palette(pal)
            self.painter.swap(surfaces, time.monotonic(), old_meta)
            self.kick()
            return False

        def work():
            surfaces = load_surfaces(meta, delta)
            GLib.idle_add(lambda: done(surfaces))
        threading.Thread(target=work, daemon=True).start()


    # ------------------------------------------------------------------ browser panel
    def load_web_chat(self):
        """the panel's own conversation, so a browser reload does not lose the talk"""
        try:
            data = json.loads(self.web_chat_path.read_text())
            if isinstance(data, list):
                return [m for m in data if isinstance(m, dict) and m.get('text')][-100:]
        except (OSError, ValueError):
            pass
        return []

    def save_web_chat(self):
        try:
            self.web_chat_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.web_chat_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.web_chat[-100:], indent=1, ensure_ascii=False))
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(self.web_chat_path)
        except OSError:
            pass

    def web_config_sig(self):
        try:
            return (bool(self.settings.get('web_panel')), str(self.settings.get('web_panel_host')),
                    int(self.settings.get('web_panel_port') or 8765),
                    int(self.settings.get('web_panel_session_hours') or 168))
        except (TypeError, ValueError):
            return (bool(self.settings.get('web_panel')), '127.0.0.1', 8765, 168)

    def web_sync_soon(self):
        """a setting changed: start/stop/restart after the frame, so the response can leave"""
        if self.web_sync_src:
            return
        self.web_sync_src = GLib.timeout_add(250, self._web_sync_later)

    def _web_sync_later(self):
        self.web_sync_src = None
        self.web_sync()
        return False

    def web_sync(self):
        sig = self.web_config_sig()
        if not sig[0]:
            if self.web:
                self.web.stop()
                self.web = None
            self.web_sig = None
            self.web_opened = False
            return
        if self.web and self.web.running and self.web_sig == sig:
            self.web.auth.set_session_hours(sig[3])
            return
        if self.web:
            self.web.stop()
            self.web = None
        panel = webpanel.Panel(self.settings, WebBridge(self))
        if panel.start():
            self.web = panel
            self.web_sig = sig
            print(f'yumi: browser panel at {panel.url(True)}', file=sys.stderr)
            if self.settings.get('web_panel_open') and not self.web_opened:
                self.web_opened = True
                system.open_url(panel.url(True))
        else:
            self.web = None
            self.web_sig = None
            print(f'yumi: the browser panel did not start: {panel.error}', file=sys.stderr)
            b = getattr(self, 'brain', None)
            if b:
                b.say_text(config.tr(self.settings, 'web_fail', err=panel.error or 'port is busy'))

    def web_open(self):
        was_running = bool(self.web and self.web.running)
        if not self.settings.get('web_panel'):
            self.settings['web_panel'] = True
            self.settings.save()
            self.web_sync()
        elif not self.web:
            self.web_sync()
        if self.web and self.web.running:
            # web_sync() may already have opened the browser via web_panel_open
            if was_running or not self.settings.get('web_panel_open'):
                system.open_url(self.web.url(True))
            self.brain.say_text(config.tr(self.settings, 'web_on', url=self.web.url(False)))
        else:
            err = (self.web.error if self.web else 'port is busy') or 'port is busy'
            self.brain.say_text(config.tr(self.settings, 'web_fail', err=err))

    def web_copy(self):
        if not self.web or not self.web.running:
            self.web_sync()
        if self.web and self.web.running:
            url = self.web.url(True)
            if not system.copy_text(url):
                Gdk.Display.get_default().get_clipboard().set(url)
            self.brain.say_text(config.tr(self.settings, 'web_on', url=self.web.url(False)))
        else:
            self.brain.say_text(config.tr(self.settings, 'web_off'))

    def web_rotate(self):
        if not self.web or not self.web.running:
            self.brain.say_text(config.tr(self.settings, 'web_off'))
            return
        token = self.web.rotate()
        url = self.web.url(False) + '?t=' + token
        if not system.copy_text(url):
            Gdk.Display.get_default().get_clipboard().set(url)
        self.brain.say_text(config.tr(self.settings, 'web_on', url=self.web.url(False)))

    def _web_call(self, fn, timeout=30):
        """run a bridge call on the GTK thread; the HTTP worker waits for the answer"""
        if threading.current_thread() is threading.main_thread():
            return fn()
        box = {}
        done = threading.Event()

        def run():
            try:
                box['value'] = fn()
            except BaseException as exc:      # the HTTP layer turns it into a response
                box['error'] = exc
            finally:
                done.set()
            return False
        GLib.idle_add(run)
        if not done.wait(timeout):
            raise TimeoutError('Yumi is busy')
        if 'error' in box:
            raise box['error']
        return box.get('value')

    def web_tick(self):
        """the live feed: the bubble and a light status, only while a browser listens"""
        web = self.web
        if not web or not web.running:
            return
        with web.bus.lock:
            listening = bool(web.bus.subs)
        if not listening:
            return
        b = self.brain
        text = str((b.bubble or {}).get('text') or '')
        if text != self._web_bubble:
            self._web_bubble = text
            web.publish('bubble', {'text': text,
                                   'kind': (b.bubble or {}).get('kind'),
                                   'until': (b.bubble or {}).get('until')})
        now = time.monotonic()
        if now - self._web_status_at > 2.0:
            self._web_status_at = now
            try:
                web.publish('status', self._web_snapshot(light=True))
            except Exception:
                pass

    @staticmethod
    def _hex_color(rgb, fallback=None):
        try:
            if isinstance(rgb, str):
                return rgb
            return '#%02x%02x%02x' % tuple(max(0, min(255, round(float(c) * 255))) for c in rgb[:3])
        except (TypeError, ValueError):
            return fallback or '#c7a8db'

    def _web_snapshot(self, light=False):
        b = self.brain
        s = self.settings
        st = self.state
        now = time.monotonic()
        if now - self._web_music_at > 4.0:
            self._web_music_at = now
            try:
                self._web_music = music.now_playing()
            except Exception:
                self._web_music = None
            try:
                self._web_volume = music.read_volume()
            except Exception:
                self._web_volume = None
        tm = b.timer or {}
        timer = {
            'seconds': int(round(b.timer_seconds())),
            'text': b.timer_text() if tm else '',
            'running': bool(tm and not tm.get('done') and tm.get('end') is not None),
            'pending': bool(tm.get('pending')),
            'paused': bool(tm.get('paused')),
            'done': bool(tm.get('done')),
        }
        palette = self.palette or {}
        ui = {}
        try:
            ui = {k: self._hex_color(v) for k, v in ui_colors(palette).items()}
        except Exception:
            pass
        queue = self.queue
        cur = queue.current if queue else None
        jobs = {
            'busy': bool(queue.busy) if queue else False,
            'current': None if not cur else {
                'op': cur.op, 'src': str(cur.src), 'arg': cur.arg,
                'fraction': round(float(cur.fraction or 0), 3)},
            'queued': [{'op': j.op, 'src': str(j.src), 'arg': j.arg}
                       for j in (queue.jobs if queue else [])][:20],
        }
        data = {
            'time': time.time(),
            'version': __import__('yumi').__version__,
            'uptime': int(time.time() - self.started),
            'settings': webcatalog.redact(s),
            'state': {
                'mode': st.get('mode'), 'monitor': st.get('monitor'),
                'shelf': st.get('shelf') or [], 'colors': st.get('colors') or [],
            },
            'runtime': {
                'mode': b.mode, 'out': bool(b.out), 'sleeping': bool(b.sleeping),
                'pinned': bool(s.get('pinned')), 'thinking': bool(b.thinking),
                'listening': bool(b.listening), 'busy': bool(b.busy),
                'progress': b.progress,
                'bubble': (b.bubble or {}).get('text') or '',
                'face': b.face,
                'timer': timer,
                'monitors': int(getattr(b, 'monitors', 1) or 1),
                'chat_models': list(b.chat_models or []),
                'mics': list(b.mics or []),
                'unavailable': sorted(b.unavailable or []),
                'palette': palette,
                'ui_colors': ui,
                'music': {'now_playing': self._web_music, 'volume': self._web_volume},
                'wake_on': bool(self.wake_on),
                'tap_ears': bool(s.get('tap_ears')),
                'downloads': self.dl_announced,
                'outfit': getattr(self.meta, 'outfit', None),
                'shelf_count': len([p for p in (st.get('shelf') or []) if Path(p).exists()]),
                'recent_count': len(getattr(self.recent, 'items', {}) or {}),
            },
            'jobs': jobs,
            'panel': self.web.info() if self.web else {'running': False},
            'features': {
                'ffmpeg': bool(shutil.which('ffmpeg')),
                'magick': bool(shutil.which('magick') or shutil.which('convert')),
                'playerctl': bool(shutil.which('playerctl')),
                'mpd': bool(shutil.which('mpc') or shutil.which('mpd')),
                'idle': bool(getattr(self.world, 'can_idle', False)),
                'wallpaper': bool(getattr(self.world, 'wall', None)),
                'desktop': 'wayland',
            },
        }
        if not light:
            data['settings_full'] = webcatalog.redact(s)
        return data

    def _web_apply_settings(self, patch):
        old = {k: self.settings.get(k) for k in patch}
        self.settings.update(patch)
        self.settings.save()
        b = self.brain
        if 'outfit' in patch:
            b.requests.append(('outfit',))
        if 'recolor' in patch or 'hue' in patch:
            b.requests.append(('recolor',))
        if 'wake_ears' in patch:
            b.requests.append(('wake_set', bool(self.settings.get('wake_ears'))))
        if 'tap_ears' in patch:
            b.requests.append(('tap_set', bool(self.settings.get('tap_ears'))))
        if 'pinned' in patch and self.settings.get('pinned'):
            b.pop(True)
        if 'sassy' in patch and not self.settings.get('sassy'):
            b.chat_history = []
            threading.Thread(target=memory.clear_tag, args=(memory.TAG_SASSY,),
                             daemon=True).start()
        if 'chat_provider' in patch:
            b.chat_models = []
            b.requests.append(('chat_models',))
        try:
            music.apply_settings(self.settings)
        except Exception:
            pass
        self.web_sync_soon()
        return {'saved': {k: self.settings.get(k) for k in patch},
                'before': old, 'restart': 'scale' in patch}

    def _web_timer_get(self):
        b = self.brain
        tm = b.timer or {}
        return {'seconds': int(round(b.timer_seconds())), 'text': b.timer_text() if tm else '',
                'running': bool(tm and not tm.get('done') and tm.get('end') is not None),
                'pending': bool(tm.get('pending')), 'paused': bool(tm.get('paused')),
                'done': bool(tm.get('done'))}

    def _web_timer(self, action, seconds):
        b = self.brain
        try:
            secs = int(seconds or 0)
        except (TypeError, ValueError):
            raise ValueError('seconds must be a number')
        if action in ('set', 'start'):
            if secs <= 0:
                b.timer = None
                b.kb_hover = False
            else:
                b.timer_set(min(secs, 24 * 3600 - 1), start_in=0)
                if b.mode == 'docked' and not b.out:
                    b.pop(True)
        elif action == 'add':
            b.timer_set(int(b.timer_seconds()) + max(1, secs), start_in=0)
        elif action == 'stop':
            b.timer = None
            b.kb_hover = False
        elif action == 'pause':
            tm = b.timer
            if tm and not tm.get('done'):
                tm['left'] = b.timer_seconds()
                tm['end'] = None
                tm['pending'] = None
                tm['paused'] = True
        elif action == 'resume':
            tm = b.timer
            if tm and not tm.get('done'):
                left = tm.get('left') or tm.get('total') or 0
                tm['end'] = b.now + left
                tm['pending'] = None
                tm['paused'] = False
        else:
            raise ValueError('unknown timer action')
        return {'timer': self._web_timer_get()}

    def _web_backup(self, name, path):
        path = Path(path)
        if not path.exists():
            return ''
        try:
            folder = config.CONFIG_DIR / 'backups'
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
            shutil.copyfile(path, dest)
            old = sorted(folder.glob(f'{name}-*.json'))
            for extra in old[:-20]:
                try:
                    extra.unlink()
                except OSError:
                    pass
            return str(dest)
        except OSError:
            return ''

    def _web_config_info(self, name):
        spec = webcatalog.CONFIG_FILES[name]
        path = Path(spec['path']())
        try:
            st = path.stat()
            return {'name': name, 'path': str(path), 'exists': True,
                    'size': st.st_size, 'mtime': st.st_mtime,
                    'title': spec['title'], 'kind': spec['kind']}
        except OSError:
            return {'name': name, 'path': str(path), 'exists': False,
                    'size': 0, 'mtime': 0, 'title': spec['title'], 'kind': spec['kind']}

    def _web_configs(self):
        return [self._web_config_info(name) for name in webcatalog.CONFIG_FILES]

    def _web_config_get(self, name):
        info = self._web_config_info(name)
        spec = webcatalog.CONFIG_FILES[name]
        path = Path(spec['path']())
        writable = name != 'webpanel'
        data = None
        raw = ''
        if name == 'settings':
            data = webcatalog.redact(self.settings)
            raw = json.dumps(data, indent=1, ensure_ascii=False)
        elif name == 'actions':
            data = [dict(a) for a in self.brain.custom]
            raw = path.read_text() if path.exists() else json.dumps(data, indent=1, ensure_ascii=False)
        elif name == 'state':
            data = dict(self.state)
            raw = json.dumps(data, indent=1, ensure_ascii=False)
        elif name == 'webpanel':
            data = {'token': webcatalog.MASK if self.web else '',
                    'secret': webcatalog.MASK, 'created': None,
                    'path': str(path), 'note': 'managed on the Security page'}
            raw = json.dumps(data, indent=1, ensure_ascii=False)
        else:
            try:
                text = path.read_text()
            except OSError:
                text = '{\n}\n'
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            raw = text
        return {'file': info, 'data': data, 'text': raw, 'writable': writable}

    def _web_config_save(self, name, value):
        if name not in webcatalog.CONFIG_FILES:
            raise ValueError('unknown config file')
        if name == 'webpanel':
            raise ValueError('the panel token is changed on the Security page')
        spec = webcatalog.CONFIG_FILES[name]
        path = Path(spec['path']())
        if isinstance(value, str):
            try:
                data = json.loads(value)
            except ValueError as exc:
                raise ValueError(f'this is not JSON: {exc}')
        else:
            data = value
        backup = self._web_backup(name, path)
        if name == 'settings':
            if not isinstance(data, dict):
                raise ValueError('settings must be an object')
            patch = webcatalog.validate_settings_patch(data)
            result = self._web_apply_settings(patch)
            return {'saved': list(patch), 'backup': backup, 'restart': result.get('restart')}
        if name == 'actions':
            cleaned = self._clean_actions(data)
            config.save_actions(cleaned)
            self.brain.custom = config.load_actions()
            self.brain.actions_check()
            return {'saved': len(cleaned), 'backup': backup}
        if name == 'state':
            if not isinstance(data, dict):
                raise ValueError('state must be an object')
            if data.get('mode') not in (None, 'docked', 'air'):
                raise ValueError('mode must be docked or air')
            self.state.update(data)
            self.state.save()
            return {'saved': list(data), 'backup': backup}
        if not isinstance(data, (dict, list)):
            raise ValueError('this file holds a JSON object or list')
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        tmp.replace(path)
        if name == 'palette':
            self.brain.requests.append(('recolor',))
        return {'saved': True, 'backup': backup}

    def _clean_actions(self, actions):
        if not isinstance(actions, list):
            raise ValueError('actions must be a list')
        out = []
        seen = set()
        for raw in actions:
            if not isinstance(raw, dict):
                continue
            command = str(raw.get('command') or '').strip()
            if not command or command in seen:
                continue
            seen.add(command)
            label = str(raw.get('label') or command)[:18]
            label_ru = str(raw.get('label_ru') or label)[:18]
            item = {'label': label, 'label_ru': label_ru,
                    'icon': str(raw.get('icon') or 'star')[:120],
                    'command': command[:2000]}
            phrases = [str(p)[:200] for p in (raw.get('phrases') or []) if str(p).strip()]
            keywords = [str(k)[:80] for k in (raw.get('keywords') or []) if str(k).strip()]
            if phrases:
                item['phrases'] = phrases
            if keywords:
                item['keywords'] = keywords
            if str(raw.get('speak') or '').strip():
                item['speak'] = str(raw['speak'])[:300]
            if str(raw.get('url') or '').strip():
                item['url'] = str(raw['url'])[:2000]
            out.append(item)
        return out

    def _web_actions_get(self):
        b = self.brain
        table = b.ring_table()
        ids = b.ring_ids(table)
        ring = []
        for i in ids:
            if i not in table:
                continue
            action, arg, icon, label, ident = table[i]
            ring.append({'id': i, 'label': label, 'icon': icon,
                         'custom': i.startswith('cmd:') or i.startswith('/'),
                         'off': not b.ring_shown(i), 'fixed': i == 'settings'})
        return {'actions': [dict(a) for a in b.custom], 'ring': ring,
                'ring_order': list(b.settings.get('ring') or []),
                'ring_off': list(b.settings.get('ring_off') or []),
                'icons': sorted(pixui.ICONS),
                'icon_patterns': dict(pixui.ICONS)}

    def _web_logs(self, name, lines):
        paths = {
            'yumi': config.STATE_DIR / 'yumi.log',
            'judge': Path('/tmp/yumi/judge.log'),
            'ears': Path('/tmp/yumi/recogn.txt'),
            'web': Path(config.STATE_DIR) / 'web-audit.log',
        }
        path = paths.get(name)
        if path is None:
            raise ValueError('unknown log')
        try:
            with path.open('rb') as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 512 * 1024))
                text = fh.read().decode('utf-8', 'replace')
        except OSError:
            text = ''
        return {'name': name, 'path': str(path), 'text': '\n'.join(text.splitlines()[-lines:])}

    def _web_file_allowed(self, path):
        """the panel may preview or open the files it knows about: recent, shelf and
        its own uploads.  A crafted ``?path=/home/…/secret.png`` gets nothing."""
        try:
            target = Path(str(path or '')).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return False
        if not target.is_file():
            return False
        known = set()
        for source in ((self.state.get('shelf') or []),
                       list(getattr(self.recent, 'items', {}) or {})):
            for item in source:
                try:
                    known.add(Path(str(item)).expanduser().resolve())
                except (OSError, RuntimeError, ValueError):
                    pass
        if target in known:
            return True
        try:
            uploads = (Path(config.CACHE_DIR) / 'uploads').resolve()
        except (OSError, RuntimeError):
            return False
        return target == uploads or uploads in target.parents

    def _web_chat(self, text):
        """the panel's own talk with the same mind and diary, away from the GTK thread"""
        text = ' '.join(str(text or '').split())
        if not text:
            raise ValueError('a message is needed')
        with self.web_chat_lock:
            history = list(self.web_chat)
        s = self.settings
        lang = s.get('speech_lang', 'en')
        n = max(2, int(s.get('chat_history_n', 10)))
        turns = [{'role': m['role'], 'content': m['text']} for m in history[-n:]]
        note = ''
        if s.get('chat_memory', True):
            try:
                note = chat.recalled_note(memory.recall(text, s, skip_recent=len(turns)), lang)
            except Exception:
                note = ''
        messages = [{'role': 'system', 'content': chat.system_prompt(lang, bool(s.get('sassy')))}]
        messages += turns
        if note:
            messages.append({'role': 'system', 'content': note})
        messages.append({'role': 'user', 'content': text})
        reply = chat.ask(s, messages)
        with self.web_chat_lock:
            self.web_chat.append({'role': 'user', 'text': text, 'ts': time.time()})
            if reply:
                self.web_chat.append({'role': 'assistant', 'text': reply, 'ts': time.time()})
            self.web_chat = self.web_chat[-100:]
            self.save_web_chat()
        sassy = bool(s.get('sassy'))
        if s.get('chat_memory', True) and bool(s.get('sassy')) == sassy:
            tag = memory.TAG_SASSY if sassy else None
            try:
                memory.remember('user', text, s, tag=tag)
                if reply:
                    memory.remember('assistant', reply, s, tag=tag)
            except Exception:
                pass
        return {'reply': reply or '', 'recalled': note != '',
                'history': len(self.web_chat)}


class WebBridge:
    """The bridge the HTTP server sees. Every call that touches GTK is wrapped by
    ``App._web_call``; the pure ones (chat, music, memory) stay on the HTTP thread."""

    def __init__(self, app):
        self.app = app

    def _on(self, fn, timeout=30):
        return self.app._web_call(fn, timeout)

    # ---- read side
    def snapshot(self):
        return self._on(self.app._web_snapshot)

    def configs(self):
        return self._on(self.app._web_configs)

    def config_get(self, name):
        return self._on(lambda: self.app._web_config_get(name))

    def config_save(self, name, value):
        return self._on(lambda: self.app._web_config_save(name, value), 60)

    def actions_get(self):
        return self._on(self.app._web_actions_get)

    def actions_save(self, actions):
        return self._on(lambda: self.app._web_config_save('actions', actions), 60)

    def action_run(self, index):
        b = self.app.brain
        self._on(lambda: b.extras_do('custom', int(index)))
        return {'ran': int(index)}

    def apply_settings(self, patch):
        return self._on(lambda: self.app._web_apply_settings(patch), 60)

    # ---- world actions
    def state_action(self, name, payload):
        a = self.app
        b = a.brain

        def run():
            if name in ('toggle', 'show'):
                b.toggle()
            elif name == 'hide':
                b.close_all()
                b.pop(False)
            elif name == 'home':
                b.go_home()
            elif name == 'monitor':
                a.cycle_monitor()
            elif name == 'pipette':
                b.start_pipette()
            elif name == 'recent':
                b.toggle_pile('recent')
            elif name == 'shelf':
                b.toggle_pile('shelf')
            elif name == 'dnd':
                on = bool(payload.get('on', not b.sleeping))
                if not on:
                    b.nap = False
                b.requests.append(('dnd', on))
            elif name == 'pin':
                on = bool(payload.get('on', not a.settings.get('pinned')))
                a.settings['pinned'] = on
                a.settings.save()
                if on:
                    b.pop(True)
            elif name == 'sound':
                b.requests.append(('sound', 'timer'))
            elif name == 'stop':
                b.stop_all()
            elif name == 'listen':
                b.requests.append(('listen', int(payload.get('seconds') or 8)))
            elif name == 'joke':
                b.requests.append(('joke_ask', {'lang': a.settings.get('speech_lang', 'en'),
                                                'text': payload.get('text') or ''}))
            elif name == 'tour':
                b.start_tour()
            elif name == 'config-dir':
                system.spawn('xdg-open', str(config.CONFIG_DIR))
            elif name == 'restart':
                b.requests.append(('restart',))
            elif name == 'quit':
                b.requests.append(('quit',))
            else:
                raise ValueError(f'unknown action: {name}')
            return {'action': name}
        return self._on(run)

    def files_recent(self):
        return self._on(self._files_recent)

    def _files_recent(self):
        items = sorted(getattr(self.app.recent, 'items', {}).items(), key=lambda x: -x[1])[:60]
        return [self._file_info(p, t) for p, t in items]

    @staticmethod
    def _file_info(path, mtime=None):
        p = Path(path)
        try:
            st = p.stat()
            size, mtime, exists = st.st_size, mtime or st.st_mtime, True
        except OSError:
            size, exists = 0, False
        kind = jobs.kind_of(p) or ('archive' if p.suffix.lower().lstrip('.') in
                                   ('zip', 'tar', 'gz', 'xz', 'zst', '7z', 'rar', 'bz2') else 'other')
        return {'path': str(p), 'name': p.name, 'size': size, 'mtime': mtime,
                'exists': exists, 'kind': kind, 'ext': p.suffix.lower().lstrip('.')}

    def files_shelf(self):
        return self._on(lambda: [self._file_info(p) for p in
                                 (self.app.state.get('shelf') or [])])

    def shelf_change(self, paths, add=True):
        def run():
            shelf = self.app.state.setdefault('shelf', [])
            for path in paths:
                p = str(Path(path).expanduser())
                if add:
                    if p not in shelf:
                        shelf.insert(0, p)
                else:
                    while p in shelf:
                        shelf.remove(p)
            del shelf[40:]
            self.app.state.save()
            return {'shelf': list(shelf)}
        return self._on(run)

    def files_open(self, path):
        def run():
            if not self.app._web_file_allowed(path):
                raise ValueError('this file is not one the panel knows')
            return {'opened': bool(files.open_path(path))}
        return self._on(run)

    def file_allowed(self, path):
        return self._on(lambda: self.app._web_file_allowed(path))

    def job_add(self, op, path, arg):
        def run():
            if op not in ('convert', 'compress'):
                raise ValueError('unknown job')
            if not self.app._web_file_allowed(path):
                raise ValueError('this file is not one the panel knows')
            p = Path(path).expanduser()
            if not p.is_file():
                raise ValueError('the file is gone')
            kind = jobs.kind_of(p)
            if not kind:
                raise ValueError('this file is not a picture, sound or video')
            allowed = jobs.TARGETS.get(kind, []) if op == 'convert' else jobs.PRESETS.get(kind, [])
            if arg not in allowed:
                raise ValueError(f'unknown target: {arg}')
            self.app.queue.add(op, str(p), arg)
            return {'queued': True, 'kind': kind, 'op': op, 'arg': arg}
        return self._on(run)

    def jobs_cancel(self):
        return self._on(lambda: self.app.queue.cancel())

    def timer_get(self):
        return self._on(self.app._web_timer_get)

    def timer(self, action, seconds):
        return self._on(lambda: self.app._web_timer(action, seconds))

    def ask(self, text, speak=True):
        def run():
            b = self.app.brain
            if b.thinking:
                return {'queued': False, 'reason': 'she is thinking already'}
            b.wake_up(text)
            return {'queued': True}
        return self._on(run)

    def chat(self, text):
        return self.app._web_chat(text)

    def chat_history(self):
        with self.app.web_chat_lock:
            return list(self.app.web_chat)

    def chat_clear(self):
        with self.app.web_chat_lock:
            self.app.web_chat = []
            self.app.save_web_chat()
        return {'cleared': True}

    def memory_recent(self, limit=50):
        return memory.recent(limit, hide_sassy=not self.app.settings.get('sassy'))

    def memory_search(self, query, limit=50):
        return memory.recall(query, self.app.settings, k=min(50, limit))

    def memory_clear(self, tag):
        if tag == 'all':
            return {'deleted': memory.clear_all()}
        return {'deleted': memory.clear_tag(tag)}

    def music_status(self):
        playing = music.now_playing()
        return {'now_playing': playing, 'volume': music.read_volume(),
                'mpd': bool(music.get_mpd_status()),
                'source': self.app.settings.get('music_source')}

    def music_search(self, kind, query):
        from yumi import library
        res = library.resolve(self.app.settings, kind, query)
        tracks = [{'title': t.get('title', ''), 'artist': t.get('artist', ''),
                   'album': t.get('album', ''), 'source': t.get('source', ''),
                   'path': t.get('path', ''), 'url': t.get('url', '')}
                  for t in (res.get('tracks') or [])[:40]]
        return {'source': res.get('source'), 'tracks': tracks, 'kind': kind, 'query': query}

    def music_play(self, kind, query):
        music.apply_settings(self.app.settings)
        report = music.play_query(kind, query, self.app.settings)
        track = report.get('track')
        return {'report': {'ok': report.get('ok'), 'source': report.get('source'),
                           'count': report.get('count'), 'kind': report.get('kind'),
                           'query': report.get('query'),
                           'track': {'artist': track[0], 'title': track[1]} if track else None}}

    def music_control(self, cmd):
        ok = music.music_control(cmd)
        return {'ok': bool(ok), 'cmd': cmd, 'volume': music.read_volume()}

    def voice_listen(self, seconds):
        res = ears.listen(lang=self.app.settings.get('speech_lang', 'en'),
                          secs=max(2, min(30, float(seconds or 8))),
                          size=self.app.settings.get('stt_size', 'small'),
                          mic=self.app.settings.get('stt_mic'),
                          engine=self.app.settings.get('stt_engine'))
        if not res:
            return {'heard': None, 'readings': None}
        heard = str(res.get('text') or res.get('vosk') or '').strip()
        return {'heard': heard or None, 'readings': res}

    def voice_stop(self):
        threading.Thread(target=ears.stop, daemon=True).start()
        return {'stopped': True}

    def voice_speak(self, text):
        self._on(lambda: self.app.brain.say_text(text))
        return {'spoke': True}

    def tools_time(self):
        return {'time': chat.the_time(self.app.settings.get('speech_lang', 'en')),
                'stamp': time.strftime('%Y-%m-%d %H:%M:%S')}

    def tools_weather(self, where, when):
        s = self.app.settings
        lang = s.get('speech_lang', 'en')
        place = where or str(s.get('weather_city') or '')
        return {'report': chat.weather_report(place, lang, when or 'today'),
                'place': place, 'when': when or 'today'}

    def tools_joke(self):
        s = self.app.settings
        return {'joke': chat.joke(s, s.get('speech_lang', 'en'))}

    def tools_params(self):
        lang = self.app.settings.get('speech_lang', 'en')
        return {'report': self._on(lambda: self.app.brain.params_report(lang))}

    def logs(self, name, lines):
        return self.app._web_logs(name, lines)


def main(argv):
    app = App()
    action, param = None, None
    args = argv[1:]
    for i, a in enumerate(args):
        if a == '--' + ASK:
            phrase = args[i + 1] if i + 1 < len(args) else ''
            action, param = ASK, GLib.Variant('s', phrase)
            break
        if a.startswith('--') and a[2:] in ACTIONS:
            action = a[2:]
            break
    try:
        app.register(None)
    except GLib.Error:
        pass
    if app.get_is_remote():
        if action:
            app.activate_action(action, param)
        else:
            app.activate()
        return 0
    if action:
        GLib.idle_add(lambda: app.activate_action(action, param) and False)
    rc = app.run([argv[0]])
    return 1 if app.failed else rc


if __name__ == '__main__':
    random.seed()
    sys.exit(main(sys.argv))
