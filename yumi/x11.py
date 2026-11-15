"""X11 in place of wlr-layer-shell: her windows are override-redirect (no window manager
touches them: no frame, no taskbar entry, no tiling), placed over a screen by hand and raised
back on top now and then, except over a fullscreen window. The input shape is GTK's own
set_input_region, which works on X11 as it is. Plain ctypes on libX11, which every X11 session has."""
import ctypes
import ctypes.util

import gi
gi.require_version('GdkX11', '4.0')
import time

from gi.repository import GdkX11, GLib  # noqa: E402

_x = ctypes.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
_x.XOpenDisplay.restype = ctypes.c_void_p
_x.XOpenDisplay.argtypes = [ctypes.c_char_p]
_x.XDefaultRootWindow.restype = ctypes.c_ulong
_x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
_x.XInternAtom.restype = ctypes.c_ulong
_x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
for _f, _a in (('XMoveResizeWindow', [ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]),
               ('XRaiseWindow', [ctypes.c_ulong]),
               ('XSetInputFocus', [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]),
               ('XGetInputFocus', [ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int)]),
               ('XFlush', []), ('XFree', [])):
    getattr(_x, _f).argtypes = [ctypes.c_void_p] + _a


class _Attrs(ctypes.Structure):          # XSetWindowAttributes
    _fields_ = [('background_pixmap', ctypes.c_ulong), ('background_pixel', ctypes.c_ulong),
                ('border_pixmap', ctypes.c_ulong), ('border_pixel', ctypes.c_ulong),
                ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int),
                ('backing_store', ctypes.c_int), ('backing_planes', ctypes.c_ulong),
                ('backing_pixel', ctypes.c_ulong), ('save_under', ctypes.c_int),
                ('event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
                ('override_redirect', ctypes.c_int), ('colormap', ctypes.c_ulong),
                ('cursor', ctypes.c_ulong)]


_x.XChangeWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(_Attrs)]
_x.XGetWindowProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
                                  ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                  ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
                                  ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]

CW_OVERRIDE_REDIRECT = 1 << 9
REVERT_TO_PARENT = 2
_dpy = None


def is_x11(display):
    return isinstance(display, GdkX11.X11Display)


_Handler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
_x.XSetErrorHandler.restype = ctypes.c_void_p
_x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
_gtk_handler = None


@_Handler
def _on_error(dpy, ev):
    """errors on our own connection (focus handed back to a window that has closed) are ignored;
    the rest go on to GTK's handler (the default one would exit the program)"""
    if dpy == _dpy or not _gtk_handler:
        return 0
    return _Handler(_gtk_handler)(dpy, ev)


def _d():
    global _dpy, _gtk_handler
    if _dpy is None:
        _dpy = _x.XOpenDisplay(None)
        _gtk_handler = _x.XSetErrorHandler(ctypes.cast(_on_error, ctypes.c_void_p))
    return _dpy


def _atom(name):
    return _x.XInternAtom(_d(), name.encode(), 0)


def _ulongs(win, prop):
    """an XA_WINDOW / XA_ATOM list property (both come back as longs)"""
    typ, fmt, n, rest, data = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
    if _x.XGetWindowProperty(_d(), win, _atom(prop), 0, 64, 0, 0, ctypes.byref(typ), ctypes.byref(fmt),
                             ctypes.byref(n), ctypes.byref(rest), ctypes.byref(data)) != 0 or not data.value:
        return []
    out = list(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[:n.value]) if fmt.value == 32 else []
    _x.XFree(data)
    return out


def xid(win):
    s = win.get_surface()
    return GdkX11.X11Surface.get_xid(s) if s is not None else 0


def unmanage(win):
    """call between realize and the first map: the window manager will leave it alone"""
    a = _Attrs(override_redirect=1)
    _x.XChangeWindowAttributes(_d(), xid(win), CW_OVERRIDE_REDIRECT, ctypes.byref(a))
    _x.XFlush(_d())


def place(win, x, y, w, h):
    _x.XMoveResizeWindow(_d(), xid(win), x, y, w, h)
    _x.XFlush(_d())


def fullscreen_active():
    act = _ulongs(_x.XDefaultRootWindow(_d()), '_NET_ACTIVE_WINDOW')
    return bool(act and act[0] and _atom('_NET_WM_STATE_FULLSCREEN') in _ulongs(act[0], '_NET_WM_STATE'))


def raise_(win):
    _x.XRaiseWindow(_d(), xid(win))
    _x.XFlush(_d())


_given = {}                                # our window -> the window that had the keyboard before


def focus(win, on):
    """take the keyboard for her (on) or hand it back to where it was"""
    w = xid(win)
    if on and w not in _given:
        prev, rev = ctypes.c_ulong(), ctypes.c_int()
        _x.XGetInputFocus(_d(), ctypes.byref(prev), ctypes.byref(rev))
        _given[w] = prev.value
        _x.XSetInputFocus(_d(), w, REVERT_TO_PARENT, 0)
    elif not on and w in _given:
        prev = _given.pop(w)
        if prev > 1:                       # 0 None, 1 PointerRoot
            _x.XSetInputFocus(_d(), prev, REVERT_TO_PARENT, 0)
    _x.XFlush(_d())


# ---- the pipette: a crosshair grab on the root window, then the pixel under the click

class _Btn(ctypes.Structure):            # XButtonEvent (XKeyEvent has the same head)
    _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('root', ctypes.c_ulong),
                ('subwindow', ctypes.c_ulong), ('time', ctypes.c_ulong), ('x', ctypes.c_int),
                ('y', ctypes.c_int), ('x_root', ctypes.c_int), ('y_root', ctypes.c_int),
                ('state', ctypes.c_uint), ('button', ctypes.c_uint), ('same_screen', ctypes.c_int)]


class _Event(ctypes.Union):              # XEvent
    _fields_ = [('b', _Btn), ('pad', ctypes.c_long * 24)]


class _Image(ctypes.Structure):          # the head of XImage
    _fields_ = [('width', ctypes.c_int), ('height', ctypes.c_int), ('xoffset', ctypes.c_int),
                ('format', ctypes.c_int), ('data', ctypes.c_void_p), ('byte_order', ctypes.c_int),
                ('bitmap_unit', ctypes.c_int), ('bitmap_bit_order', ctypes.c_int), ('bitmap_pad', ctypes.c_int),
                ('depth', ctypes.c_int), ('bytes_per_line', ctypes.c_int), ('bits_per_pixel', ctypes.c_int),
                ('red_mask', ctypes.c_ulong), ('green_mask', ctypes.c_ulong), ('blue_mask', ctypes.c_ulong)]


class _Visual(ctypes.Structure):          # the head of Visual
    _fields_ = [('ext_data', ctypes.c_void_p), ('visualid', ctypes.c_ulong), ('cls', ctypes.c_int),
                ('red_mask', ctypes.c_ulong), ('green_mask', ctypes.c_ulong), ('blue_mask', ctypes.c_ulong)]


_x.XDefaultScreen.argtypes = [ctypes.c_void_p]
_x.XDefaultVisual.restype = ctypes.POINTER(_Visual)
_x.XDefaultVisual.argtypes = [ctypes.c_void_p, ctypes.c_int]
_x.XCreateFontCursor.restype = ctypes.c_ulong
_x.XCreateFontCursor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
_x.XGrabPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_uint, ctypes.c_int,
                            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
_x.XGrabKeyboard.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_ulong]
for _f in ('XUngrabPointer', 'XUngrabKeyboard', 'XFreeCursor'):
    getattr(_x, _f).argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_x.XPending.argtypes = [ctypes.c_void_p]
_x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Event)]
_x.XGetImage.restype = ctypes.POINTER(_Image)
_x.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
                         ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]

XC_CROSSHAIR, BUTTON_PRESS_MASK, GRAB_ASYNC = 34, 1 << 2, 1
KEY_PRESS, BUTTON_PRESS, Z_PIXMAP = 2, 4, 2


def pixel_hex(px, red, green, blue):
    """a TrueColor pixel to '#rrggbb' by its channel masks"""
    def ch(mask):
        shift = (mask & -mask).bit_length() - 1
        return round(((px & mask) >> shift) * 255 / (mask >> shift))
    return '#%02x%02x%02x' % (ch(red), ch(green), ch(blue))


def _pixel(root, x, y):
    im = _x.XGetImage(_d(), root, x, y, 1, 1, ctypes.c_ulong(-1).value, Z_PIXMAP)
    if not im:
        return None
    i = im.contents
    v = _x.XDefaultVisual(_d(), _x.XDefaultScreen(_d())).contents   # XGetImage leaves the masks empty
    try:
        if not (v.red_mask and v.green_mask and v.blue_mask):
            return None                            # not TrueColor: nothing sane to read
        raw = ctypes.string_at(i.data, max(1, i.bits_per_pixel // 8))
        return pixel_hex(int.from_bytes(raw, 'little' if i.byte_order == 0 else 'big'),
                         v.red_mask, v.green_mask, v.blue_mask)
    finally:
        _x.XFree(i.data)
        _x.XFree(im)


def pick(done):
    """crosshair until a click; done('#rrggbb') for a left click, done(None) for another button,
    any key or two minutes of nothing. Polled from the main loop, so she keeps moving meanwhile."""
    d, root = _d(), _x.XDefaultRootWindow(_d())
    cur = _x.XCreateFontCursor(d, XC_CROSSHAIR)
    start = time.monotonic()
    ev = _Event()

    def finish(hexc):
        _x.XUngrabPointer(d, 0)
        _x.XUngrabKeyboard(d, 0)
        _x.XFreeCursor(d, cur)
        _x.XFlush(d)
        done(hexc)
        return False

    def poll():
        while _x.XPending(d):
            _x.XNextEvent(d, ctypes.byref(ev))
            if ev.b.type == BUTTON_PRESS:
                return finish(_pixel(root, ev.b.x_root, ev.b.y_root) if ev.b.button == 1 else None)
            if ev.b.type == KEY_PRESS:
                return finish(None)
        return True if time.monotonic() - start < 120 else finish(None)

    def grab():
        # the click on her button may still hold the pointer for a moment: try again
        if _x.XGrabPointer(d, root, 0, BUTTON_PRESS_MASK, GRAB_ASYNC, GRAB_ASYNC, 0, cur, 0) == 0:
            _x.XGrabKeyboard(d, root, 0, GRAB_ASYNC, GRAB_ASYNC, 0)
            GLib.timeout_add(20, poll)
            return False
        return True if time.monotonic() - start < 2 else finish(None)

    GLib.timeout_add(20, grab)
