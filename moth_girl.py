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
  yumi --quit          let her go
Env: YUMI_SCALE (integer pixel scale, overrides settings).
"""
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
from yumi import chat, config, ears, files, jobs, music, pixfont, pixui, system, tts, voice  # noqa: E402
from yumi.brain import UI_PX, Brain  # noqa: E402
from yumi.phrases import Speech  # noqa: E402
from yumi.sprites import Meta, palette_delta, palette_from_hue, recolor_bgra, ui_colors  # noqa: E402

APP_ID = 'io.github.lamex30.Yumi'
ACTIONS = ('toggle', 'pipette', 'home', 'recent', 'sleep', 'monitor', 'quit')
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
        for name in ACTIONS:
            a = Gio.SimpleAction.new(name, None)
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
        self.fetch_chat_models()        # her answer model for the settings, in the background
        self.on_caps(initial=True)
        GLib.timeout_add_seconds(3600, lambda: self.apply_outfit() or True)    # the calendar moves on
        self.queue = jobs.Queue(self.job_progress, self.job_done, self.job_start)
        self.ops = []
        self.timer = None
        self.dl_announced = {}          # the model a daemon fetches now: src -> name, told about
        GLib.timeout_add_seconds(4, self.watch_downloads)
        self.hold()                                  # keep running without a mapped window too
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

    def on_action(self, _a, _p, name):
        self.do_activate()
        b = self.brain
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
            elif kind == 'search_files':
                query = r[1] if len(r) > 1 else ''
                if query:
                    # Search for files using find or fd
                    import subprocess
                    from pathlib import Path
                    
                    expand_path = Path.home()
                    results = []
                    
                    # Try fd first
                    try:
                        result = subprocess.run(
                            ['fd', '-t', 'f', '--glob', query, str(expand_path)],
                            capture_output=True, text=True, timeout=10
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            results = [Path(p) for p in result.stdout.strip().split('\n') if p.strip()]
                    except Exception:
                        pass
                    
                    # Fall back to find
                    if not results:
                        try:
                            result = subprocess.run(
                                ['find', str(expand_path), '-name', query, '-type', 'f'],
                                capture_output=True, text=True, timeout=30
                            )
                            if result.returncode == 0:
                                results = [Path(p) for p in result.stdout.strip().split('\n') if p.strip()]
                        except Exception:
                            pass
                    
                    # Return results to brain
                    if results:
                        b.requests.append(('search_results', results))
                    else:
                        b.requests.append(('search_done', []))
            elif kind == 'search_results':
                # Display search results in a menu
                results = r[1]
                if results:
                    # Create a menu with search results
                    lang = b.settings.get('ui_lang', 'en')
                    ui = config.UI.get(lang, config.UI['en'])
                    
                    rows = []
                    for i, path in enumerate(results[:10]):  # Show max 10 results
                        name = path.name
                        rows.append(('menu', f'search_{i}', name))
                    
                    if len(results) > 10:
                        rows.append(('more', None, f'... и еще {len(results) - 10}'))
                    
                    rows.append(('back', None, ui.get('back', 'Back')))
                    
                    b.menu = {
                        'kind': 'search',
                        'rows': rows,
                        'title': ui.get('search_title', 'Результаты поиска'),
                        'data': {'query': r[2] if len(r) > 2 else query, 'results': [str(p) for p in results]}
                    }
                else:
                    b.menu = {
                        'kind': 'search',
                        'rows': [('none', None, 'Файлы не найдены')],
                        'title': ui.get('search_title', 'Результаты поиска'),
                    }
            elif kind == 'search_done':
                # Clear search state
                b.menu = None
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
                              ear=self.settings.get('ears_range', 'mid'),
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
                                ear=self.settings.get('ears_range', 'mid'),
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
                GLib.idle_add(self.duck_begin)   # her name passed: quiet now, whatever comes
                busy = (b.sleeping or b.thinking or b.listening)
                if busy:
                    continue            # she got busy in those seconds: the word is spent
                if b.now < b.talk_until or self.talking():
                    self.hush()         # her own line was still sounding: it dies, your turn now
                GLib.idle_add(lambda t=tail: self.ev(lambda br: br.wake_up(t)) and False)

        self.wake_thread = threading.Thread(target=work, daemon=True)
        self.wake_thread.start()

    def tap_set(self, on):
        """the ears' diary: while it is on, the daemon records everything the ears hear
        into /tmp/yumi (the sound and the words), in its own thread beside her work"""
        def work():
            ears.tap(bool(on), lang=self.settings.get('speech_lang', 'en'),
                     size=self.settings.get('stt_size', 'small'),
                     mic=self.settings.get('stt_mic'),
                     ear=self.settings.get('ears_range', 'mid'))
        threading.Thread(target=work, daemon=True).start()

    def chat_ask(self, history):
        """the question is away to Ollama; the answer comes back into her bubble"""
        def work():
            prompt = chat.system_prompt(self.settings.get('speech_lang', 'en'))
            reply = chat.ask(self.settings, [{'role': 'system', 'content': prompt}] + history)
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
        """her mind is asked for a joke; the reply goes back to the brain"""
        def work():
            reply = chat.joke(self.settings, payload.get('lang', 'en'))
            GLib.idle_add(lambda: self.ev(lambda b: b.joked(reply)) and False)
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


def main(argv):
    app = App()
    action = next((a[2:] for a in argv[1:] if a.startswith('--') and a[2:] in ACTIONS), None)
    try:
        app.register(None)
    except GLib.Error:
        pass
    if app.get_is_remote():
        if action:
            app.activate_action(action, None)
        else:
            app.activate()
        return 0
    if action:
        GLib.idle_add(lambda: app.activate_action(action, None) and False)
    rc = app.run([argv[0]])
    return 1 if app.failed else rc


if __name__ == '__main__':
    random.seed()
    sys.exit(main(sys.argv))
