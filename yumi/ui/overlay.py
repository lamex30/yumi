"""The transparent layer-shell window, one per screen."""
import cairo
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from yumi import jobs


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

