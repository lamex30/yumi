"""Sprites and Brain draw-ops painted with cairo on the overlay."""
import cairo
from gi.repository import Gdk, GdkPixbuf, GLib

from yumi import pixfont, pixui
from yumi.brain import UI_PX
from yumi.sprites import recolor_bgra, ui_colors

from .constants import FADE


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

